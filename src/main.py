from __future__ import annotations

import asyncio
import signal
from datetime import date, datetime, timezone

import structlog

from src.api.client import DerivClient
from src.config import load_settings
from src.execution.order_manager import ContractManager
from src.logging_setup import setup_logging
from src.loop.profit_loop import LoopPhase, ProfitLoop
from src.risk.manager import DailyPnLTracker, StakeSizer
from src.storage.state import StateStore
from src.strategy.engine import TradeAction, VolPairEngine, VolPairSignal

log = structlog.get_logger()


class DerivVolBot:
    """Autonomous Deriv vol pair bot with profit insertion loop."""

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
        self._cycle_event = asyncio.Event()
        self._last_signal: VolPairSignal | None = None
        self._last_quote: dict[str, float] = {}
        self._paper_ticks_left = 0
        self._paper_entry_price = 0.0
        self._paper_start_balance = 10000.0

        r = self.settings.risk
        self.pnl = DailyPnLTracker(
            target_usd=r.daily_profit_target_usd,
            max_daily_loss_pct=r.max_daily_loss_pct,
            max_drawdown_pct=r.max_drawdown_pct,
            max_lifetime_drawdown_pct=r.max_lifetime_drawdown_pct,
            max_consecutive_loss_days=r.max_consecutive_loss_days,
            cooldown_hours=r.cooldown_hours,
            anchor_balance=10000.0,
            peak_balance=10000.0,
            lifetime_peak_balance=10000.0,
            lifetime_anchor_balance=10000.0,
        )
        self.sizer = StakeSizer(r.max_stake_usd, r.min_stake_usd, r.stake_pct_of_balance)

        self.profit_loop = ProfitLoop(
            self.settings.bot.profit_loop,
            target_usd=r.daily_profit_target_usd,
        )
        self._loop_enabled = self.settings.bot.profit_loop.enabled

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
        if self.pnl.profit_day_locked:
            self.profit_loop.set_phase(LoopPhase.LOCKED)

        status = "PROFIT_LOCKED" if self.pnl.profit_day_locked else (
            "COOLDOWN" if self.pnl.in_cooldown else "LOOP_ACTIVE"
        )
        log.info(
            "bot_started",
            status=status,
            profit_loop=self._loop_enabled,
            symbols=symbols,
            paper=self.client.paper_mode,
            target_usd=self.settings.risk.daily_profit_target_usd,
            daily_pnl=round(self.pnl.total_pnl_usd, 4),
        )

        reconcile = asyncio.create_task(self._reconcile_loop())
        profit_loop_task = asyncio.create_task(self._profit_insertion_loop())
        try:
            while self._running:
                await asyncio.sleep(1)
        finally:
            reconcile.cancel()
            profit_loop_task.cancel()
            await self.client.close()

    async def stop(self) -> None:
        self._running = False
        self._cycle_event.set()
        log.info("bot_stopped", daily_pnl=round(self.pnl.total_pnl_usd, 4))

    async def _profit_insertion_loop(self) -> None:
        """
        Core insertion loop — runs continuously:

          SCAN → INSERT → wait settle → ACCUMULATE → re-INSERT until +$target
        """
        while self._running:
            try:
                await asyncio.wait_for(self._cycle_event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                if self._loop_enabled and self.profit_loop.phase == LoopPhase.SCAN:
                    self._cycle_event.set()
                continue
            self._cycle_event.clear()

            if not self._loop_enabled or self.pnl.is_blocked:
                if self.pnl.profit_day_locked:
                    self.profit_loop.set_phase(LoopPhase.LOCKED)
                continue

            if not self.profit_loop.ready_to_insert(self.pnl.is_blocked, self.contracts.has_open):
                continue

            sig = self._last_signal
            if not sig or sig.action == TradeAction.FLAT:
                self.profit_loop.set_phase(LoopPhase.SCAN)
                continue

            quote = self._last_quote.get(sig.symbol)
            if quote is None:
                continue

            base = self.sizer.stake(
                self._get_balance(), sig.confidence, self.pnl.stake_multiplier
            )
            stake = self.profit_loop.next_stake(
                base, self.settings.risk.max_stake_usd, self.pnl.total_pnl_usd
            )

            self.profit_loop.set_phase(LoopPhase.INSERT)
            opened = await self.contracts.open_contract(sig, stake)
            if not opened:
                self.profit_loop.set_phase(LoopPhase.SCAN)
                continue

            self.profit_loop.on_trade_opened()
            self.strategy.set_in_contract(True)
            if opened.paper:
                self._paper_ticks_left = self.settings.strategy.contract_duration
                self._paper_entry_price = quote

            snap = self.profit_loop.snapshot(self.pnl.total_pnl_usd)
            log.info(
                "loop_insert",
                loop=snap["loop_count"] + 1,
                phase="INSERT",
                progress_pct=snap["progress_pct"],
                stake=stake,
                symbol=sig.symbol,
                action=sig.action.value,
            )

    async def _handle_tick(self, tick: dict) -> None:
        symbol = tick["symbol"]
        quote = float(tick["quote"])
        epoch = int(tick["epoch"])
        self._last_quote[symbol] = quote

        sig = self.strategy.on_tick(symbol, quote, epoch)
        if sig is not None:
            self._last_signal = sig
            if self.profit_loop.phase in (LoopPhase.SCAN, LoopPhase.ACCUMULATE):
                self._cycle_event.set()

        if self.contracts.has_open and self.contracts.open and self.contracts.open.paper:
            if symbol == self.contracts.open.symbol:
                self._paper_ticks_left -= 1
                if self._paper_ticks_left <= 0:
                    await self._settle_and_loop()
            return

    async def _settle_and_loop(self) -> None:
        oc = self.contracts.open
        if not oc:
            return
        quote = self._last_quote.get(oc.symbol, self._paper_entry_price)
        won = (
            quote > self._paper_entry_price
            if oc.contract_type == "CALL"
            else quote < self._paper_entry_price
        )
        pnl = await self.contracts.settle_paper(won)
        await self._after_settlement(pnl, oc.trade_id, oc.symbol, won)

    async def _after_settlement(
        self, pnl: float, trade_id: str, symbol: str, won: bool
    ) -> None:
        self.strategy.record_result(pnl)
        self.pnl.record(pnl, self._get_balance())
        self.profit_loop.on_trade_settled(
            pnl, self.pnl.total_pnl_usd, self.pnl.profit_day_locked
        )
        snap = self.profit_loop.snapshot(self.pnl.total_pnl_usd)
        log.info(
            "loop_accumulate",
            result="win" if won else "loss",
            pnl=round(pnl, 4),
            daily_pnl=round(self.pnl.total_pnl_usd, 4),
            progress_pct=snap["progress_pct"],
            loops=snap["loop_count"],
        )
        if self.pnl.profit_day_locked:
            log.info(
                "profit_day_complete",
                pnl=round(self.pnl.total_pnl_usd, 2),
                loops=snap["loop_count"],
            )
        await self._persist_risk_state()
        self.strategy.set_in_contract(False)
        await self.store.log_trade(trade_id, symbol, "win" if won else "loss", pnl, True)

        if not self.pnl.is_blocked:
            self.profit_loop.set_phase(LoopPhase.SCAN)
            self._cycle_event.set()

    async def _handle_contract_update(self, data: dict) -> None:
        if not self.contracts.open:
            return
        oc = self.contracts.open
        trade_id, symbol = oc.trade_id, oc.symbol
        pnl = self.contracts.on_contract_update(data)
        if pnl is not None:
            await self._after_settlement(pnl, trade_id, symbol, pnl >= 0)

    def _get_balance(self) -> float:
        if self.client.paper_mode:
            return self._paper_start_balance + self.contracts._paper_balance_delta
        return self.client.balance

    def _parse_cooldown(self, iso: str) -> datetime | None:
        if not iso:
            return None
        return datetime.fromisoformat(iso)

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
        self.pnl.consecutive_loss_days = saved.consecutive_loss_days
        self.pnl.stake_multiplier = saved.stake_multiplier
        self.pnl.cooldown_until = self._parse_cooldown(saved.cooldown_until)

        if saved_day == today:
            self.pnl.trading_day = today
            self.pnl.anchor_balance = saved.anchor_balance
            self.pnl.peak_balance = max(saved.peak_balance, live_balance)
            self.pnl.realized_pnl_usd = saved.realized_pnl_usd
            self.pnl.trades_today = saved.trades_today
            self.pnl.wins_today = saved.wins_today
            self.pnl.losses_today = saved.losses_today
            self.pnl.halted = saved.halted
            self.pnl.halt_reason = saved.halt_reason
            self.pnl.profit_day_locked = saved.profit_day_locked
        else:
            balance = self._paper_start_balance if self.client.paper_mode else live_balance
            self.pnl.trading_day = saved_day
            self.pnl.realized_pnl_usd = saved.realized_pnl_usd
            self.pnl.sync_day(balance, today)

    async def _persist_risk_state(self) -> None:
        balance = self._get_balance()
        cooldown = self.pnl.cooldown_until.isoformat() if self.pnl.cooldown_until else ""
        await self.store.save_risk_state(
            trading_day=self.pnl.trading_day,
            anchor_balance=self.pnl.anchor_balance,
            peak_balance=max(self.pnl.peak_balance, balance),
            realized_pnl_usd=self.pnl.total_pnl_usd,
            trades_today=self.pnl.trades_today,
            wins_today=self.pnl.wins_today,
            losses_today=self.pnl.losses_today,
            halted=self.pnl.halted,
            halt_reason=self.pnl.halt_reason,
            profit_day_locked=self.pnl.profit_day_locked,
            paper_balance=balance,
            lifetime_peak_balance=max(self.pnl.lifetime_peak_balance, balance),
            lifetime_anchor_balance=self.pnl.lifetime_anchor_balance,
            consecutive_loss_days=self.pnl.consecutive_loss_days,
            stake_multiplier=self.pnl.stake_multiplier,
            cooldown_until=cooldown,
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
