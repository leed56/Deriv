"""
Core trading bot — runs strategies on live ticks and places demo trades.
"""

import asyncio
import logging
from datetime import datetime

from colorama import Fore, Style, init

from config import SYMBOLS, STRATEGY_PARAMS, DEFAULT_STAKE
from deriv_api import DerivAPI
from strategies import STRATEGIES
from risk_manager import RiskManager
from performance_tracker import PerformanceTracker

init(autoreset=True)
logger = logging.getLogger("bot")


class DerivBot:
    def __init__(
        self,
        token: str,
        symbols: list[str] | None = None,
        strategy_names: list[str] | None = None,
        stake: float = DEFAULT_STAKE,
        risk_mode: str = "fixed",
        max_trades: int = 50,
        live_trade: bool = False,
    ):
        self.api       = DerivAPI(token)
        self.symbols   = symbols or list(SYMBOLS.keys())
        self.strategies = strategy_names or list(STRATEGIES.keys())
        self.stake     = stake
        self.risk_mode = risk_mode
        self.max_trades = max_trades
        self.live_trade = live_trade  # if False → signal-only / backtest mode
        self.tracker   = PerformanceTracker()
        self.risk_mgrs: dict[str, RiskManager] = {}
        self.price_history: dict[str, list[float]] = {s: [] for s in self.symbols}
        self.running   = False

    # ── lifecycle ───────────────────────────────────────────────────────
    async def start(self):
        await self.api.connect()
        account = await self.api.authorize()
        balance = await self.api.get_balance()
        print(f"\n{Fore.GREEN}Connected  |  Account: {account.get('loginid')}  |  Balance: ${balance:.2f}{Style.RESET_ALL}\n")

        for sym in self.symbols:
            self.risk_mgrs[sym] = RiskManager(
                starting_balance=balance / len(self.symbols),
                base_stake=self.stake,
                mode=self.risk_mode,
            )

        self.running = True
        tasks = [self._run_symbol(sym) for sym in self.symbols]
        await asyncio.gather(*tasks)

    async def stop(self):
        self.running = False
        await self.api.disconnect()
        self.tracker.print_report()

    # ── per-symbol loop ─────────────────────────────────────────────────
    async def _run_symbol(self, symbol: str):
        risk  = self.risk_mgrs[symbol]
        name  = SYMBOLS[symbol]["name"]
        sub_id, queue = await self.api.subscribe_ticks(symbol)
        trade_count = 0

        print(f"  {Fore.CYAN}Watching {name} ({symbol}){Style.RESET_ALL}")

        try:
            while self.running and trade_count < self.max_trades:
                should_stop, reason = risk.should_stop()
                if should_stop:
                    print(f"  {Fore.RED}[{symbol}] Stopping: {reason}{Style.RESET_ALL}")
                    break

                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    continue

                tick = msg.get("tick", {})
                price = float(tick.get("quote", 0))
                if price == 0:
                    continue

                self.price_history[symbol].append(price)
                prices = self.price_history[symbol]

                # Evaluate each strategy
                for strat_name in self.strategies:
                    strat_fn = STRATEGIES[strat_name]
                    params   = STRATEGY_PARAMS.get(strat_name, {})
                    signal   = strat_fn(prices, symbol, params)
                    if signal is None:
                        continue

                    trade_count += 1
                    stake = risk.next_stake()
                    await self._execute_signal(symbol, strat_name, signal, stake, risk)

        finally:
            await self.api.unsubscribe(sub_id)

    # ── signal execution ────────────────────────────────────────────────
    async def _execute_signal(
        self,
        symbol: str,
        strategy: str,
        signal: dict,
        stake: float,
        risk: RiskManager,
    ):
        ct       = signal["contract_type"]
        duration = signal["duration"]
        dur_unit = signal["duration_unit"]
        barrier  = signal.get("barrier")
        strength = signal.get("signal_strength", 0)

        ts = datetime.now().strftime("%H:%M:%S")
        print(
            f"  [{ts}] {Fore.YELLOW}{symbol}{Style.RESET_ALL} | "
            f"{Fore.MAGENTA}{strategy:<20}{Style.RESET_ALL} | "
            f"{Fore.CYAN}{ct:<12}{Style.RESET_ALL} | "
            f"Stake=${stake:.2f} | Strength={strength:.3f}"
        )

        if not self.live_trade:
            # Signal-only mode: simulate outcome based on next tick
            await asyncio.sleep(0.1)
            return

        try:
            proposal = await self.api.get_proposal(
                symbol, ct, stake, duration, dur_unit, barrier
            )
            payout = float(proposal.get("payout", 0))
            buy    = await self.api.buy(proposal["id"], float(proposal["ask_price"]))

            # Wait for contract to settle
            await asyncio.sleep(duration * 1.5 if dur_unit == "t" else duration + 2)

            # Check result via portfolio (simplified — check if contract closed)
            contracts = await self.api.get_open_contracts()
            contract_ids = {c["contract_id"] for c in contracts}

            if buy["contract_id"] not in contract_ids:
                # Contract settled — we need the final P&L
                # For demo, assume we can check transaction history
                # Here we record based on the proposal payout vs stake
                win = payout > stake
                profit = payout - stake
                outcome = "win" if win else "loss"
                if win:
                    risk.record_win(profit)
                    print(f"    {Fore.GREEN}WIN  +${profit:.2f}{Style.RESET_ALL}")
                else:
                    risk.record_loss(stake)
                    print(f"    {Fore.RED}LOSS -${stake:.2f}{Style.RESET_ALL}")

                self.tracker.record(symbol, strategy, {
                    "outcome": outcome,
                    "profit":  profit if win else 0,
                    "stake":   stake,
                    "contract_type": ct,
                    "strategy": strategy,
                })
            else:
                # Still open — sell at market
                await self.api.sell(buy["contract_id"])

        except RuntimeError as e:
            logger.warning(f"Trade error [{symbol}/{strategy}]: {e}")
