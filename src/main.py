from __future__ import annotations

import asyncio
import signal
import time
from datetime import datetime, timezone

import structlog

from src.api.client import DeribitClient
from src.config import load_settings
from src.execution.order_manager import OrderManager, PositionState
from src.logging_setup import setup_logging
from src.risk.manager import build_risk
from src.storage.state import StateStore
from src.strategy.engine import MarketTick, SignalSide, SyntheticSpreadEngine
from src.strategy.microstructure import MicrostructureState, OrderBookSnapshot

log = structlog.get_logger()


class ProfitBot:
    """Fully autonomous Deribit demo trading bot targeting daily USD profit."""

    def __init__(self) -> None:
        self.settings = load_settings()
        self.client = DeribitClient(
            self.settings.http_url,
            self.settings.ws_url,
            self.settings.client_id,
            self.settings.client_secret,
        )
        self.strategy = SyntheticSpreadEngine(self.settings.strategy)
        self.pnl_tracker, self.sizer = build_risk(self.settings.risk)
        self.orders = OrderManager(
            self.client, self.settings.execution, self.settings.leg_a, self.settings.leg_b
        )
        self.store = StateStore(self.settings.bot.state_db)
        self._running = False
        self._latest: dict = {
            "btc_price": 0.0,
            "eth_price": 0.0,
            "btc_funding": 0.0,
            "eth_funding": 0.0,
            "micro": MicrostructureState(),
        }
        self._tick_event = asyncio.Event()
        self._account_equity_usd = 10000.0

    async def start(self) -> None:
        setup_logging(self.settings.bot.log_level)
        await self.store.init()

        today = datetime.now(timezone.utc).date().isoformat()
        restored = await self.store.load_today_pnl(today)
        self.pnl_tracker.realized_pnl_usd = restored

        await self.client.connect()
        await self._bootstrap_market_data()
        await self._subscribe()

        self._running = True
        log.info(
            "bot_started",
            env=self.settings.env,
            target_usd=self.settings.risk.daily_profit_target_usd,
            paper=self.client.paper_mode,
            legs=[self.settings.leg_a, self.settings.leg_b],
        )

        reconcile_task = asyncio.create_task(self._reconcile_loop())
        try:
            while self._running:
                try:
                    await asyncio.wait_for(self._tick_event.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                self._tick_event.clear()
                await self._cycle()
        finally:
            reconcile_task.cancel()
            await self.client.close()

    async def stop(self) -> None:
        self._running = False
        if self.orders.is_open and self._latest["btc_price"]:
            pnl = await self.orders.close_spread(
                self._latest["btc_price"], self._latest["eth_price"]
            )
            self.pnl_tracker.record_trade_pnl(pnl)
        log.info("bot_stopped", daily_pnl=round(self.pnl_tracker.total_pnl_usd, 4))

    async def _bootstrap_market_data(self) -> None:
        for leg, key in ((self.settings.leg_a, "btc"), (self.settings.leg_b, "eth")):
            ticker = await self.client.get_ticker(leg)
            book = await self.client.get_order_book(leg)
            price = ticker.get("last_price") or ticker.get("mark_price", 0)
            self._latest[f"{key}_price"] = price
            self._latest[f"{key}_funding"] = ticker.get("current_funding", 0) or 0
            snap = OrderBookSnapshot.from_deribit(book)
            micro: MicrostructureState = self._latest["micro"]
            from src.strategy.microstructure import order_book_imbalance, spread_bps

            obi = order_book_imbalance(snap)
            sp = spread_bps(snap)
            if key == "btc":
                micro.obi_a, micro.spread_a_bps = obi, sp
            else:
                micro.obi_b, micro.spread_b_bps = obi, sp

        if not self.client.paper_mode:
            try:
                summary = await self.client.get_account_summary(self.settings.currency)
                self._account_equity_usd = summary.get("equity", 10000) * (
                    self._latest["btc_price"] or 60000
                )
            except Exception:
                pass

    async def _subscribe(self) -> None:
        channels = [
            f"ticker.{self.settings.leg_a}.100ms",
            f"ticker.{self.settings.leg_b}.100ms",
            f"book.{self.settings.leg_a}.100ms",
            f"book.{self.settings.leg_b}.100ms",
        ]

        async def on_ticker_a(data: dict) -> None:
            self._latest["btc_price"] = data.get("last_price") or data.get("mark_price", 0)
            self._latest["btc_funding"] = data.get("current_funding", 0) or 0
            self._tick_event.set()

        async def on_ticker_b(data: dict) -> None:
            self._latest["eth_price"] = data.get("last_price") or data.get("mark_price", 0)
            self._latest["eth_funding"] = data.get("current_funding", 0) or 0
            self._tick_event.set()

        from src.strategy.microstructure import order_book_imbalance, spread_bps

        async def on_book_a(data: dict) -> None:
            snap = OrderBookSnapshot.from_deribit(data)
            micro: MicrostructureState = self._latest["micro"]
            micro.obi_a = order_book_imbalance(snap)
            micro.spread_a_bps = spread_bps(snap)

        async def on_book_b(data: dict) -> None:
            snap = OrderBookSnapshot.from_deribit(data)
            micro: MicrostructureState = self._latest["micro"]
            micro.obi_b = order_book_imbalance(snap)
            micro.spread_b_bps = spread_bps(snap)

        self.client.on_channel(f"ticker.{self.settings.leg_a}.100ms", on_ticker_a)
        self.client.on_channel(f"ticker.{self.settings.leg_b}.100ms", on_ticker_b)
        self.client.on_channel(f"book.{self.settings.leg_a}.100ms", on_book_a)
        self.client.on_channel(f"book.{self.settings.leg_b}.100ms", on_book_b)
        await self.client.subscribe(channels)

    async def _reconcile_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.settings.execution.reconcile_interval_sec)
            today = datetime.now(timezone.utc).date().isoformat()
            await self.store.save_daily(
                today,
                self.pnl_tracker.realized_pnl_usd,
                self.pnl_tracker.trades_today,
                self.pnl_tracker.halted,
                self.pnl_tracker.halt_reason,
            )

    async def _cycle(self) -> None:
        btc = self._latest["btc_price"]
        eth = self._latest["eth_price"]
        if btc <= 0 or eth <= 0:
            return

        tick = MarketTick(
            btc_price=btc,
            eth_price=eth,
            btc_funding=self._latest["btc_funding"],
            eth_funding=self._latest["eth_funding"],
            micro=self._latest["micro"],
            timestamp=time.time(),
        )

        signal = self.strategy.evaluate(tick)
        unrealized = self.orders.unrealized_pnl(btc, eth)
        self.pnl_tracker.update_unrealized(unrealized)

        await self.store.log_snapshot(
            signal.zscore, signal.beta, signal.spread, self.pnl_tracker.total_pnl_usd, signal.reason
        )

        if self.pnl_tracker.halted:
            if self.orders.is_open and signal.side == SignalSide.FLAT:
                pnl = await self.orders.close_spread(btc, eth)
                self.pnl_tracker.record_trade_pnl(pnl)
                self.strategy.set_position(SignalSide.FLAT)
            return

        # Close on exit signal
        if self.orders.is_open and signal.side == SignalSide.FLAT:
            pos = self.orders.position
            pnl = await self.orders.close_spread(btc, eth)
            self.pnl_tracker.record_trade_pnl(pnl)
            if pos:
                await self.store.log_trade(
                    pos.trade_id,
                    pos.state.value,
                    pos.btc_amount,
                    pos.eth_amount,
                    pos.entry_zscore,
                    pnl,
                    pos.paper,
                    pos.opened_at,
                )
            self.strategy.set_position(SignalSide.FLAT)
            log.info(
                "trade_closed",
                pnl=round(pnl, 4),
                daily_pnl=round(self.pnl_tracker.total_pnl_usd, 4),
                reason=signal.reason,
            )
            return

        # Open new spread trade
        if (
            not self.orders.is_open
            and signal.side != SignalSide.FLAT
            and self.pnl_tracker.can_open_trade(self.settings.risk.max_open_trades, 0)
        ):
            notional = self.sizer.size_usd(signal.confidence, self._account_equity_usd)
            btc_amt = self.sizer.btc_contracts(notional, btc)
            eth_amt = self.sizer.eth_contracts(notional, eth, btc, signal.beta)
            pos = await self.orders.open_spread(
                signal.side, btc_amt, eth_amt, btc, eth, signal.zscore
            )
            if pos:
                self.strategy.set_position(signal.side, signal.zscore)
                log.info(
                    "trade_opened",
                    side=signal.side.value,
                    confidence=round(signal.confidence, 3),
                    zscore=round(signal.zscore, 3),
                    beta=round(signal.beta, 4),
                    reason=signal.reason,
                )


async def main() -> None:
    bot = ProfitBot()
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _shutdown() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown)

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
