from __future__ import annotations

import asyncio
import signal
import time
from datetime import datetime, timezone

import structlog

from src.api.client import DerivClient
from src.config import load_settings
from src.execution.order_manager import ContractManager
from src.logging_setup import setup_logging
from src.risk.manager import DailyPnLTracker, StakeSizer
from src.storage.state import StateStore
from src.strategy.engine import TradeAction, VolPairEngine

log = structlog.get_logger()


class DerivVolBot:
    """Autonomous Deriv synthetic volatility pair bot (V10/V25/V50/V75)."""

    def __init__(self) -> None:
        self.settings = load_settings()
        self.client = DerivClient(
            self.settings.ws_url,
            self.settings.app_id,
            self.settings.api_token,
        )
        self.strategy = VolPairEngine(self.settings.strategy, self.settings.vol_pairs)
        self.contracts = ContractManager(self.client, self.settings.strategy, self.settings.risk)
        self.store = StateStore(self.settings.bot.state_db)
        self._running = False
        self._tick_event = asyncio.Event()
        self._paper_ticks_left = 0
        self._paper_entry_price = 0.0

        balance = 10000.0 if self.client.paper_mode else 0.0
        self.pnl = DailyPnLTracker(
            target_usd=self.settings.risk.daily_profit_target_usd,
            max_daily_loss_pct=self.settings.risk.max_daily_loss_pct,
            max_drawdown_pct=self.settings.risk.max_drawdown_pct,
            session_start_balance=balance,
        )
        self.sizer = StakeSizer(
            self.settings.risk.max_stake_usd,
            self.settings.risk.min_stake_usd,
            self.settings.risk.stake_pct_of_balance,
        )

    async def start(self) -> None:
        setup_logging(self.settings.bot.log_level)
        await self.store.init()
        await self.client.connect()

        balance = self.client.balance if not self.client.paper_mode else 10000.0
        self.pnl.session_start_balance = balance

        symbols = [leg.symbol for leg in self.settings.vol_pairs]
        for sym in symbols:
            self.client.on_tick(sym, self._handle_tick)
        self.client.on_contract(self._handle_contract_update)
        await self.client.subscribe_ticks(symbols)

        self._running = True
        log.info(
            "bot_started",
            symbols=symbols,
            paper=self.client.paper_mode,
            target_usd=self.settings.risk.daily_profit_target_usd,
        )

        reconcile = asyncio.create_task(self._reconcile_loop())
        try:
            while self._running:
                try:
                    await asyncio.wait_for(self._tick_event.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                self._tick_event.clear()
        finally:
            reconcile.cancel()
            await self.client.close()

    async def stop(self) -> None:
        self._running = False
        log.info("bot_stopped", daily_pnl=round(self.pnl.total_pnl_usd, 4))

    async def _handle_tick(self, tick: dict) -> None:
        symbol = tick["symbol"]
        quote = float(tick["quote"])
        epoch = int(tick["epoch"])
        self._tick_event.set()

        sig = self.strategy.on_tick(symbol, quote, epoch)
        if sig is None:
            return

        # Paper contract expiry countdown
        if self.contracts.has_open and self.contracts.open and self.contracts.open.paper:
            if symbol == self.contracts.open.symbol:
                self._paper_ticks_left -= 1
                if self._paper_ticks_left <= 0:
                    oc = self.contracts.open
                    won = (
                        quote > self._paper_entry_price
                        if oc.contract_type == "CALL"
                        else quote < self._paper_entry_price
                    )
                    pnl = await self.contracts.settle_paper(won)
                    self.pnl.record(pnl, self._get_balance())
                    self.strategy.set_in_contract(False)
                    await self.store.log_trade(oc.trade_id, oc.symbol, "win" if won else "loss", pnl, True)
            return

        if self.contracts.has_open:
            return

        if self.pnl.halted:
            return

        if not sig or sig.action == TradeAction.FLAT:
            return

        if not self.pnl.can_trade(0, self.settings.risk.max_open_contracts):
            return

        stake = self.sizer.stake(self._get_balance(), sig.confidence)
        opened = await self.contracts.open_contract(sig, stake)
        if opened:
            self.strategy.set_in_contract(True)
            if opened.paper:
                self._paper_ticks_left = self.settings.strategy.contract_duration
                self._paper_entry_price = quote
            log.info(
                "signal_trade",
                symbol=sig.symbol,
                action=sig.action.value,
                zscore=round(sig.zscore, 3),
                confidence=round(sig.confidence, 3),
                stake=stake,
                reason=sig.reason,
            )

        await self.store.log_snapshot(sig.zscore, sig.spread, self.pnl.total_pnl_usd, sig.reason)

    async def _handle_contract_update(self, data: dict) -> None:
        pnl = self.contracts.on_contract_update(data)
        if pnl is not None:
            self.pnl.record(pnl, self._get_balance())
            self.strategy.set_in_contract(False)
            log.info("contract_settled", pnl=round(pnl, 4))

    def _get_balance(self) -> float:
        if self.client.paper_mode:
            return 10000.0 + self.contracts._paper_balance_delta + self.pnl.total_pnl_usd
        return self.client.balance

    async def _reconcile_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.settings.bot.reconcile_interval_sec)
            today = datetime.now(timezone.utc).date().isoformat()
            await self.store.save_daily(
                today, self.pnl.total_pnl_usd, self.pnl.trades_today, self.pnl.halted, self.pnl.halt_reason
            )


async def main() -> None:
    bot = DerivVolBot()
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    loop.add_signal_handler(signal.SIGINT, stop_event.set)
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)
    run_task = asyncio.create_task(bot.start())
    await stop_event.wait()
    await bot.stop()
    run_task.cancel()
    try:
        await run_task
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    asyncio.run(main())
