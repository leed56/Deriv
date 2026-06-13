from __future__ import annotations

import asyncio
import signal
from datetime import date, datetime, timezone

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
        self._paper_start_balance = 10000.0

        self.pnl = DailyPnLTracker(
            target_usd=self.settings.risk.daily_profit_target_usd,
            max_daily_loss_pct=self.settings.risk.max_daily_loss_pct,
            max_drawdown_pct=self.settings.risk.max_drawdown_pct,
            max_lifetime_drawdown_pct=self.settings.risk.max_lifetime_drawdown_pct,
            max_consecutive_loss_days=self.settings.risk.max_consecutive_loss_days,
            anchor_balance=10000.0,
            peak_balance=10000.0,
            lifetime_peak_balance=10000.0,
            lifetime_anchor_balance=10000.0,
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

        await self._restore_risk_state()
        balance = self._get_balance()
        self.pnl.check_equity(balance)
        await self._persist_risk_state()

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
            daily_pnl=round(self.pnl.total_pnl_usd, 4),
            halted=self.pnl.is_blocked,
            halt_reason=self.pnl.lifetime_halt_reason or self.pnl.halt_reason or None,
            lifetime_halted=self.pnl.lifetime_halted,
            consecutive_loss_days=self.pnl.consecutive_loss_days,
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
                    await self._persist_risk_state()
                    self.strategy.set_in_contract(False)
                    await self.store.log_trade(oc.trade_id, oc.symbol, "win" if won else "loss", pnl, True)
            return

        if self.contracts.has_open:
            return

        if self.pnl.is_blocked:
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
            await self._persist_risk_state()
            self.strategy.set_in_contract(False)
            log.info("contract_settled", pnl=round(pnl, 4))

    def _get_balance(self) -> float:
        if self.client.paper_mode:
            return self._paper_start_balance + self.contracts._paper_balance_delta
        return self.client.balance

    async def _restore_risk_state(self) -> None:
        today = datetime.now(timezone.utc).date()
        live_balance = self.client.balance if not self.client.paper_mode else 10000.0
        saved = await self.store.load_risk_state()

        if saved is None:
            self._paper_start_balance = live_balance
            self.pnl.trading_day = today
            self.pnl.anchor_balance = live_balance
            self.pnl.peak_balance = live_balance
            self.pnl.lifetime_peak_balance = live_balance
            self.pnl.lifetime_anchor_balance = live_balance
            return

        self._paper_start_balance = saved.paper_balance
        saved_day = date.fromisoformat(saved.trading_day)

        self.pnl.lifetime_peak_balance = saved.lifetime_peak_balance
        self.pnl.lifetime_anchor_balance = saved.lifetime_anchor_balance
        self.pnl.lifetime_halted = saved.lifetime_halted
        self.pnl.lifetime_halt_reason = saved.lifetime_halt_reason
        self.pnl.consecutive_loss_days = saved.consecutive_loss_days

        if saved_day == today:
            self.pnl.trading_day = today
            self.pnl.anchor_balance = saved.anchor_balance
            self.pnl.peak_balance = max(saved.peak_balance, live_balance)
            self.pnl.realized_pnl_usd = saved.realized_pnl_usd
            self.pnl.trades_today = saved.trades_today
            self.pnl.halted = saved.halted
            self.pnl.halt_reason = saved.halt_reason
            log.info(
                "risk_state_restored",
                day=saved.trading_day,
                realized_pnl=saved.realized_pnl_usd,
                lifetime_halted=saved.lifetime_halted,
            )
        else:
            # New UTC day: daily budget resets, lifetime limits carry forward
            balance = self._paper_start_balance if self.client.paper_mode else live_balance
            self.pnl.trading_day = saved_day  # sync_day needs prior day to count streak
            self.pnl.realized_pnl_usd = saved.realized_pnl_usd
            self.pnl.sync_day(balance, today)
            log.info(
                "new_trading_day",
                consecutive_loss_days=self.pnl.consecutive_loss_days,
                lifetime_halted=self.pnl.lifetime_halted,
            )

    async def _persist_risk_state(self) -> None:
        balance = self._get_balance()
        await self.store.save_risk_state(
            trading_day=self.pnl.trading_day,
            anchor_balance=self.pnl.anchor_balance,
            peak_balance=max(self.pnl.peak_balance, balance),
            realized_pnl_usd=self.pnl.total_pnl_usd,
            trades_today=self.pnl.trades_today,
            halted=self.pnl.halted,
            halt_reason=self.pnl.halt_reason,
            paper_balance=balance if self.client.paper_mode else self.client.balance,
            lifetime_peak_balance=max(self.pnl.lifetime_peak_balance, balance),
            lifetime_anchor_balance=self.pnl.lifetime_anchor_balance,
            lifetime_halted=self.pnl.lifetime_halted,
            lifetime_halt_reason=self.pnl.lifetime_halt_reason,
            consecutive_loss_days=self.pnl.consecutive_loss_days,
        )

    async def _reconcile_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.settings.bot.reconcile_interval_sec)
            self.pnl.check_equity(self._get_balance())
            await self._persist_risk_state()


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
