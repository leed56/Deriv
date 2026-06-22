"""
Watcher Agent — monitors two bots and controls Deriv copy trading.
"""

import asyncio
import json
import logging
from datetime import datetime
from colorama import Fore, Style, init

init(autoreset=True)
logger = logging.getLogger("watcher")

COPY_TRADING_MIN_TRADES = 10   # minimum trades before watcher acts
OUTPERFORM_THRESHOLD    = 0.05  # 5% win-rate lead to trigger copy switch


class WatcherAgent:
    """
    Watches two BotAgent instances.
    - Prints a live comparison dashboard every `interval` seconds.
    - Pauses a bot that exceeds its stop-loss.
    - Starts Deriv copy trading from the winning account to the losing one
      once there's enough data.
    """

    def __init__(self, bot_a, bot_b, interval: int = 30):
        self.bots     = {"A": bot_a, "B": bot_b}
        self.interval = interval
        self.copy_active: dict[str, bool] = {"A": False, "B": False}
        self.running  = False

    # ── main loop ──────────────────────────────────────────────────────────
    async def run(self):
        self.running = True
        print(f"\n{Fore.CYAN}[Watcher] Started — checking every {self.interval}s{Style.RESET_ALL}\n")
        while self.running:
            await asyncio.sleep(self.interval)
            await self._tick()

    async def stop(self):
        self.running = False

    # ── per-interval logic ─────────────────────────────────────────────────
    async def _tick(self):
        stats = {k: b.risk_manager.summary() for k, b in self.bots.items()}
        self._print_dashboard(stats)
        await self._evaluate(stats)

    def _print_dashboard(self, stats: dict):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"\n{Fore.CYAN}── Watcher Dashboard [{ts}] {'─'*35}{Style.RESET_ALL}")
        rows = []
        for label, s in stats.items():
            bot  = self.bots[label]
            name = bot.label
            pnl_color = Fore.GREEN if float(s['pnl']) >= 0 else Fore.RED
            rows.append(
                f"  Bot {label} ({name:<28}) | "
                f"Trades={s['total_trades']:>4} | "
                f"WR={s['win_rate']:>6} | "
                f"PnL={pnl_color}{s['pnl']:>+7}{Style.RESET_ALL} | "
                f"CopyActive={self.copy_active[label]}"
            )
        for r in rows:
            print(r)
        print()

    async def _evaluate(self, stats: dict):
        sa, sb = stats["A"], stats["B"]

        # 1. Pause any bot that hit stop-loss
        for label, s in stats.items():
            bot = self.bots[label]
            stop, reason = bot.risk_manager.should_stop()
            if stop and bot.running:
                bot.running = False
                print(f"  {Fore.RED}[Watcher] Bot {label} PAUSED → {reason}{Style.RESET_ALL}")

        # 2. Copy-trading decision (need enough data from both)
        if sa["total_trades"] < COPY_TRADING_MIN_TRADES or sb["total_trades"] < COPY_TRADING_MIN_TRADES:
            return

        wr_a = float(sa["win_rate"].replace("%", "")) / 100
        wr_b = float(sb["win_rate"].replace("%", "")) / 100
        gap  = abs(wr_a - wr_b)

        if gap < OUTPERFORM_THRESHOLD:
            return  # too close to call

        winner  = "A" if wr_a > wr_b else "B"
        loser   = "B" if winner == "A" else "A"
        w_bot   = self.bots[winner]
        l_bot   = self.bots[loser]

        if not self.copy_active[loser]:
            print(
                f"  {Fore.GREEN}[Watcher] Bot {winner} outperforms by {gap:.1%} "
                f"→ starting copy trade: {w_bot.label} → {l_bot.label}{Style.RESET_ALL}"
            )
            await self._start_copy(provider=w_bot, copier=l_bot, loser_label=loser)

    async def _start_copy(self, provider, copier, loser_label: str):
        """Instructs copier account to copy provider account via Deriv API."""
        try:
            # Deriv copy trading: need provider's loginid
            resp = await copier.api._call({"copytrading_list": 1})
            # Start copying
            await copier.api._call({
                "copy_start": provider.loginid,
                "assets":     [],      # all assets
                "max_trade_stake": 1,
            })
            self.copy_active[loser_label] = True
            print(f"  {Fore.GREEN}[Watcher] Copy trading ACTIVE → {provider.loginid}{Style.RESET_ALL}")
        except Exception as e:
            logger.warning(f"Copy trade start failed: {e}")

    async def _stop_copy(self, copier, provider_loginid: str, label: str):
        try:
            await copier.api._call({"copy_stop": provider_loginid})
            self.copy_active[label] = False
            print(f"  {Fore.YELLOW}[Watcher] Copy trading STOPPED{Style.RESET_ALL}")
        except Exception as e:
            logger.warning(f"Copy trade stop failed: {e}")

    # ── copy trading statistics ─────────────────────────────────────────────
    async def get_copy_stats(self, bot) -> dict:
        try:
            resp = await bot.api._call({
                "copytrading_statistics": 1,
                "trader_id": bot.loginid,
            })
            return resp.get("copytrading_statistics", {})
        except Exception as e:
            logger.warning(f"Copy stats error: {e}")
            return {}
