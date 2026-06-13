"""Autonomous trading bot orchestrator."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import pandas as pd
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config.settings import settings
from src.data.feed import LiveDataFeed
from src.data.store import StateStore
from src.engine.executor import PaperExecutor
from src.engine.portfolio import PortfolioManager
from src.engine.risk import RiskManager
from src.strategies.base import Signal
from src.strategies.kalman_spread import KalmanSpreadStrategy
from src.strategies.ou_mean_reversion import OUMeanReversionStrategy
from src.strategies.regime_stat_arb import RegimeStatArbStrategy
from src.synthetic.pairs import SyntheticPair, discover_pairs

logger = logging.getLogger("profit_bot")


class ProfitBot:
    """
    Fully autonomous demo trading bot:
    - Live Binance data
    - Synthetic cointegrated pairs
    - Statistical strategies (no traditional indicators)
    - Paper execution with daily $5 target
    """

    def __init__(self) -> None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)

        self.feed = LiveDataFeed(
            exchange_id=settings.exchange,
            api_key=settings.binance_api_key,
            api_secret=settings.binance_api_secret,
            testnet=settings.binance_testnet,
        )
        self.state = StateStore(settings.state_file)
        self.portfolio = PortfolioManager(
            settings.data_dir / "portfolio.json",
            settings.initial_capital_usd,
        )
        self.risk = RiskManager(
            state_store=self.state,
            daily_target_usd=settings.daily_profit_target_usd,
            max_daily_loss_usd=settings.max_daily_loss_usd,
            max_position_pct=settings.max_position_pct,
            min_trade_usd=settings.min_trade_usd,
            initial_capital=settings.initial_capital_usd,
        )
        self.executor = PaperExecutor(self.feed, self.portfolio, self.risk)

        self.strategies = [
            OUMeanReversionStrategy(),
            KalmanSpreadStrategy(),
            RegimeStatArbStrategy(),
        ]
        self.pairs: list[SyntheticPair] = []
        self._cycle = 0

    def _refresh_pairs(self, prices: pd.DataFrame) -> None:
        discovered = discover_pairs(prices)
        if discovered:
            self.pairs = discovered
            logger.info(
                "Discovered %d synthetic pairs: %s",
                len(self.pairs),
                ", ".join(p.name for p in self.pairs),
            )

    def _current_prices(self) -> dict[str, float]:
        prices = {}
        for sym in settings.symbol_list:
            t = self.feed.fetch_ticker(sym)
            prices[sym] = t["last"]
        return prices

    def _spread_zscores(self, prices: pd.DataFrame) -> dict[str, float]:
        zscores = {}
        for pair in self.pairs:
            pid = f"{pair.leg_a}:{pair.leg_b}"
            zscores[pid] = pair.zscore(prices)
        return zscores

    def _aggregate_signals(self, all_signals: list[Signal]) -> list[Signal]:
        """Merge signals per pair, keep strongest."""
        by_pair: dict[str, Signal] = {}
        for sig in all_signals:
            if not sig.pair:
                continue
            key = f"{sig.pair.leg_a}:{sig.pair.leg_b}"
            if key not in by_pair or sig.strength > by_pair[key].strength:
                by_pair[key] = sig
        return sorted(by_pair.values(), key=lambda s: s.strength, reverse=True)

    def run_cycle(self) -> None:
        self._cycle += 1
        logger.info("── Cycle %d ──", self._cycle)

        try:
            prices_df = self.feed.aligned_close_matrix(
                settings.symbol_list,
                settings.timeframe,
                settings.lookback_bars,
            )
        except Exception as e:
            logger.error("Data fetch failed: %s", e)
            return

        if len(prices_df) < 100:
            logger.warning("Insufficient data (%d bars)", len(prices_df))
            return

        if self._cycle % 10 == 1 or not self.pairs:
            self._refresh_pairs(prices_df)

        if not self.pairs:
            logger.warning("No tradeable synthetic pairs found")
            return

        live_prices = self._current_prices()
        equity = self.portfolio.mark_to_market(live_prices)
        zscores = self._spread_zscores(prices_df)

        # Check exits first
        exit_pnl = self.executor.check_exits_with_spreads(live_prices, zscores)
        if exit_pnl:
            logger.info("Exit PnL this cycle: $%.2f", exit_pnl)

        # Microstructure context
        imbalances = {}
        for sym in settings.symbol_list:
            try:
                imbalances[sym] = self.feed.fetch_order_book_imbalance(sym)
            except Exception:
                imbalances[sym] = 0.0

        context = {"imbalances": imbalances}

        all_signals: list[Signal] = []
        for strategy in self.strategies:
            try:
                signals = strategy.generate(prices_df, self.pairs, context)
                all_signals.extend(signals)
            except Exception as e:
                logger.warning("Strategy %s error: %s", strategy.name, e)

        best = self._aggregate_signals(all_signals)
        open_count = len(self.portfolio.portfolio.positions)
        daily_pnl = self.state.get_daily_pnl()

        logger.info(
            "Equity=$%.2f | Daily PnL=$%.2f / $%.2f target | Positions=%d | Signals=%d",
            equity,
            daily_pnl,
            settings.daily_profit_target_usd,
            open_count,
            len(best),
        )

        risk = self.risk.evaluate(
            equity=equity,
            signal_strength=best[0].strength if best else 0,
            open_positions=open_count,
        )

        if not risk.can_trade:
            logger.info("Trading paused: %s", risk.reason)
            return

        for signal in best[:2]:
            if self.portfolio.has_open_position(f"{signal.pair.leg_a}:{signal.pair.leg_b}"):
                continue
            risk = self.risk.evaluate(equity, signal.strength, open_count)
            if not risk.can_trade:
                break
            if self.executor.try_open(signal, risk.position_size_usd):
                open_count += 1

    def run_once(self) -> None:
        self.run_cycle()

    def run_forever(self) -> None:
        logger.info(
            "Starting autonomous bot | target=$%.2f/day | capital=$%.2f | interval=%ds",
            settings.daily_profit_target_usd,
            settings.initial_capital_usd,
            settings.poll_interval_seconds,
        )
        scheduler = BlockingScheduler(timezone="UTC")
        scheduler.add_job(
            self.run_cycle,
            trigger=IntervalTrigger(seconds=settings.poll_interval_seconds),
            id="trading_cycle",
            max_instances=1,
            coalesce=True,
        )
        # Run immediately on start
        self.run_cycle()
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Bot stopped.")

    def status(self) -> dict:
        live_prices = self._current_prices()
        equity = self.portfolio.mark_to_market(live_prices)
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "equity_usd": round(equity, 2),
            "daily_pnl_usd": round(self.state.get_daily_pnl(), 2),
            "daily_target_usd": settings.daily_profit_target_usd,
            "open_positions": len(self.portfolio.portfolio.positions),
            "pairs": [p.name for p in self.pairs],
            "trade_count": self.portfolio.portfolio.trade_count,
        }
