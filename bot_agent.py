"""
BotAgent — a single AI-driven trading agent for one Deriv demo account.
Two of these run in parallel under the WatcherAgent.
"""

import asyncio
import json
import logging
import urllib.request
import ssl
from datetime import datetime
from colorama import Fore, Style, init

from config import STRATEGY_PARAMS, DEFAULT_STAKE
from strategies import STRATEGIES
from risk_manager import RiskManager
from performance_tracker import PerformanceTracker

init(autoreset=True)
logger = logging.getLogger("bot_agent")

WS_URL = "wss://ws.binaryws.com/websockets/v3?app_id=1089"


class DerivDirectAPI:
    """
    Thin async WebSocket client — one persistent connection per agent.
    Uses the token directly with Deriv's WebSocket API.
    """

    def __init__(self, token: str):
        self.token   = token
        self.ws      = None
        self._req_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._subs:    dict[str, asyncio.Queue]  = {}
        self._task     = None

    async def connect(self):
        import websockets
        self.ws   = await websockets.connect(WS_URL, ping_interval=30)
        self._task = asyncio.create_task(self._recv_loop())
        resp = await self._send({"authorize": self.token})
        if resp.get("error"):
            raise RuntimeError(f"Auth failed: {resp['error']['message']}")
        auth = resp["authorize"]
        return auth

    async def disconnect(self):
        if self._task:
            self._task.cancel()
        if self.ws:
            await self.ws.close()

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _send(self, payload: dict) -> dict:
        rid = self._next_id()
        payload["req_id"] = rid
        fut = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        await self.ws.send(json.dumps(payload))
        return await fut

    async def _recv_loop(self):
        try:
            import websockets
            async for raw in self.ws:
                msg = json.loads(raw)
                rid    = msg.get("req_id")
                sub    = msg.get("subscription", {})
                sub_id = sub.get("id") if isinstance(sub, dict) else None

                if sub_id and sub_id in self._subs:
                    await self._subs[sub_id].put(msg)
                elif rid and rid in self._pending:
                    fut = self._pending.pop(rid)
                    if not fut.done():
                        fut.set_result(msg)
        except (Exception, asyncio.CancelledError):
            pass

    async def _call(self, payload: dict) -> dict:
        return await self._send(payload)

    async def get_balance(self) -> float:
        r = await self._send({"balance": 1, "account": "current"})
        return float(r["balance"]["balance"])

    async def ticks_history(self, symbol: str, count: int) -> list[float]:
        r = await self._send({
            "ticks_history": symbol, "count": count,
            "end": "latest", "style": "ticks",
        })
        return [float(p) for p in r.get("history", {}).get("prices", [])]

    async def subscribe_ticks(self, symbol: str) -> tuple[str, asyncio.Queue]:
        rid = self._next_id()
        fut = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        await self.ws.send(json.dumps({"ticks": symbol, "subscribe": 1, "req_id": rid}))
        first  = await fut
        sub_id = first["subscription"]["id"]
        q: asyncio.Queue = asyncio.Queue()
        self._subs[sub_id] = q
        await q.put(first)
        return sub_id, q

    async def unsubscribe(self, sub_id: str):
        await self._send({"forget": sub_id})
        self._subs.pop(sub_id, None)

    async def trade_and_settle(self, symbol, contract_type, stake, duration, duration_unit, barrier=None) -> dict:
        """Proposal → buy → wait for settlement."""
        # 1. Proposal
        payload = {
            "proposal": 1, "amount": stake, "basis": "stake",
            "contract_type": contract_type, "currency": "USD",
            "duration": duration, "duration_unit": duration_unit, "symbol": symbol,
        }
        if barrier:
            payload["barrier"] = barrier
        prop_r = await self._send(payload)
        if prop_r.get("error"):
            raise RuntimeError(prop_r["error"]["message"])
        proposal = prop_r["proposal"]

        # 2. Buy
        buy_r = await self._send({"buy": proposal["id"], "price": proposal["ask_price"]})
        if buy_r.get("error"):
            raise RuntimeError(buy_r["error"]["message"])
        contract_id = buy_r["buy"]["contract_id"]

        # 3. Wait for settlement via proposal_open_contracts subscription
        poc_r = await self._send({
            "proposal_open_contracts": 1,
            "contract_id": contract_id,
            "subscribe": 1,
        })
        sub_id = poc_r.get("subscription", {}).get("id")
        q: asyncio.Queue = asyncio.Queue()
        if sub_id:
            self._subs[sub_id] = q
            await q.put(poc_r)

        # Poll queue until is_sold
        deadline = asyncio.get_event_loop().time() + 120
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=5)
                poc = msg.get("proposal_open_contracts", {})
                if poc.get("is_sold"):
                    if sub_id:
                        await self.unsubscribe(sub_id)
                    return {
                        "profit":    float(poc.get("profit", 0)),
                        "is_win":    float(poc.get("profit", 0)) > 0,
                        "sell_price": poc.get("sell_price"),
                        "status":    poc.get("status"),
                    }
            except asyncio.TimeoutError:
                continue

        raise RuntimeError("Contract settlement timeout")


# ─────────────────────────────────────────────────────────────────────────────

class BotAgent:
    """
    One AI trading agent — one Deriv demo account.
    Runs assigned strategies on assigned symbols.
    """

    def __init__(
        self,
        label: str,             # "A" or "B"
        token: str,
        symbols: list[str],
        strategy_names: list[str],
        stake: float = DEFAULT_STAKE,
        risk_mode: str = "fixed",
        max_trades: int = 100,
        live_trade: bool = False,
    ):
        self.label          = label
        self.token          = token
        self.symbols        = symbols
        self.strategy_names = strategy_names
        self.stake          = stake
        self.risk_mode      = risk_mode
        self.max_trades     = max_trades
        self.live_trade     = live_trade
        self.api            = DerivDirectAPI(token)
        self.tracker        = PerformanceTracker()
        self.risk_manager   = None   # set after connect
        self.loginid        = ""
        self.running        = False
        self.price_history: dict[str, list[float]] = {s: [] for s in symbols}

    async def start(self):
        auth    = await self.api.connect()
        balance = await self.api.get_balance()
        self.loginid      = auth.get("loginid", "")
        self.risk_manager = RiskManager(
            starting_balance=balance,
            base_stake=self.stake,
            mode=self.risk_mode,
        )
        print(
            f"  {Fore.GREEN}Bot {self.label} connected | "
            f"Account: {self.loginid} | Balance: ${balance:.2f}{Style.RESET_ALL}"
        )
        self.running = True
        tasks = [self._run_symbol(sym) for sym in self.symbols]
        await asyncio.gather(*tasks)

    async def stop(self):
        self.running = False
        await self.api.disconnect()
        self.tracker.print_report()

    async def _run_symbol(self, symbol: str):
        sub_id, queue = await self.api.subscribe_ticks(symbol)
        trade_count   = 0

        try:
            while self.running and trade_count < self.max_trades:
                stop, reason = self.risk_manager.should_stop()
                if stop:
                    print(f"  {Fore.RED}[Bot {self.label}] {symbol} stopping: {reason}{Style.RESET_ALL}")
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    continue

                price = float(msg.get("tick", {}).get("quote", 0))
                if price == 0:
                    continue
                self.price_history[symbol].append(price)
                prices = self.price_history[symbol]

                for strat_name in self.strategy_names:
                    from strategies import STRATEGIES
                    sig = STRATEGIES[strat_name](prices, symbol, STRATEGY_PARAMS.get(strat_name, {}))
                    if sig is None:
                        continue
                    trade_count += 1
                    await self._execute(symbol, strat_name, sig)
        finally:
            await self.api.unsubscribe(sub_id)

    async def _execute(self, symbol: str, strategy: str, signal: dict):
        ct       = signal["contract_type"]
        duration = signal["duration"]
        dur_unit = signal["duration_unit"]
        barrier  = signal.get("barrier")
        stake    = self.risk_manager.next_stake()
        ts       = datetime.now().strftime("%H:%M:%S")

        print(
            f"  [{ts}] {Fore.YELLOW}Bot {self.label}{Style.RESET_ALL} | "
            f"{symbol} | {Fore.MAGENTA}{strategy:<20}{Style.RESET_ALL} | "
            f"{Fore.CYAN}{ct:<8}{Style.RESET_ALL} | ${stake:.2f}"
        )

        if not self.live_trade:
            return

        try:
            result = await self.api.trade_and_settle(symbol, ct, stake, duration, dur_unit, barrier)
            profit = result["profit"]
            is_win = result["is_win"]

            if is_win:
                self.risk_manager.record_win(profit)
                print(f"    {Fore.GREEN}WIN  +${profit:.2f}{Style.RESET_ALL}")
            else:
                self.risk_manager.record_loss(stake)
                print(f"    {Fore.RED}LOSS -${stake:.2f}{Style.RESET_ALL}")

            self.tracker.record(symbol, strategy, {
                "outcome": "win" if is_win else "loss",
                "profit":  profit if is_win else 0,
                "stake":   stake,
                "contract_type": ct,
            })
        except RuntimeError as e:
            logger.warning(f"[Bot {self.label}] Trade error: {e}")
