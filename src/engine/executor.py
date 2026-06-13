"""Paper trade execution against live bid/ask."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.engine.portfolio import PortfolioManager
from src.engine.risk import RiskManager
from src.strategies.base import Side, Signal
from src.synthetic.pairs import SyntheticPair

if TYPE_CHECKING:
    from src.data.feed import LiveDataFeed

logger = logging.getLogger("profit_bot.executor")


class PaperExecutor:
    """Simulates fills at live market prices (demo mode)."""

    def __init__(
        self,
        feed: LiveDataFeed,
        portfolio: PortfolioManager,
        risk: RiskManager,
        slippage_bps: float = 2.0,
    ) -> None:
        self.feed = feed
        self.portfolio = portfolio
        self.risk = risk
        self.slippage_bps = slippage_bps

    def _apply_slippage(self, price: float, side: Side, is_leg_a: bool) -> float:
        slip = price * (self.slippage_bps / 10_000)
        # Buying = pay ask + slip; selling = receive bid - slip
        if side == Side.LONG:
            return price + slip if is_leg_a else price - slip
        return price - slip if is_leg_a else price + slip

    def _pair_id(self, pair: SyntheticPair) -> str:
        return f"{pair.leg_a}:{pair.leg_b}"

    def try_open(self, signal: Signal, position_size_usd: float) -> bool:
        if not signal.pair or not signal.is_actionable:
            return False

        pair = signal.pair
        pair_id = self._pair_id(pair)

        ta = self.feed.fetch_ticker(pair.leg_a)
        tb = self.feed.fetch_ticker(pair.leg_b)

        price_a = self._apply_slippage(
            ta["ask"] if signal.side == Side.LONG else ta["bid"], signal.side, True
        )
        price_b = self._apply_slippage(
            tb["bid"] if signal.side == Side.LONG else tb["ask"], signal.side, False
        )

        pos = self.portfolio.open_spread(
            pair_id=pair_id,
            leg_a=pair.leg_a,
            leg_b=pair.leg_b,
            side=signal.side,
            notional_usd=position_size_usd,
            price_a=price_a,
            price_b=price_b,
            hedge_ratio=pair.hedge_ratio,
            strategy=signal.strategy,
            entry_z=signal.entry_z,
            stop_z=signal.stop_z,
        )
        if pos:
            logger.info(
                "OPEN %s %s %s | size=$%.2f z=%.2f [%s]",
                signal.side.value.upper(),
                pair.name,
                pair_id,
                position_size_usd,
                signal.entry_z,
                signal.strategy,
            )
            return True
        return False

    def check_exits(self, prices: dict[str, float], pairs: list[SyntheticPair]) -> float:
        """Close positions on mean reversion (z -> 0) or stop."""
        total_pnl = 0.0
        import pandas as pd
        from src.synthetic.pairs import SyntheticPair as SP

        price_df = pd.DataFrame([prices], columns=list(prices.keys()))
        # Need history for z — caller passes via check_exits_with_history

        for pos in list(self.portfolio.portfolio.positions):
            pa = prices.get(pos.leg_a, pos.entry_a)
            pb = prices.get(pos.leg_b, pos.entry_b)
            pnl = pos.unrealized_pnl(pa, pb)
            should_close = False
            reason = ""

            # Stop loss in USD (2% of notional estimate)
            notional = abs(pos.qty_a * pos.entry_a) + abs(pos.qty_b * pos.entry_b)
            if pnl < -notional * 0.02:
                should_close = True
                reason = "stop_loss"

            if should_close:
                closed = self.portfolio.close_spread(pos.pair_id, pa, pb)
                self.risk.record_pnl(closed)
                total_pnl += closed
                logger.info("CLOSE %s pnl=$%.2f [%s]", pos.pair_id, closed, reason)

        return total_pnl

    def check_exits_with_spreads(
        self,
        prices: dict[str, float],
        spread_zscores: dict[str, float],
    ) -> float:
        total_pnl = 0.0
        for pos in list(self.portfolio.portfolio.positions):
            pa = prices.get(pos.leg_a, pos.entry_a)
            pb = prices.get(pos.leg_b, pos.entry_b)
            z = spread_zscores.get(pos.pair_id, 0.0)
            pnl = pos.unrealized_pnl(pa, pb)
            notional = abs(pos.qty_a * pos.entry_a) + abs(pos.qty_b * pos.entry_b)

            exit_signal = False
            reason = ""

            # Mean reversion exit: z crossed toward zero
            if pos.side == Side.LONG and z >= -0.3:
                exit_signal = True
                reason = "target_reversion"
            elif pos.side == Side.SHORT and z <= 0.3:
                exit_signal = True
                reason = "target_reversion"
            elif abs(z) >= pos.stop_z:
                exit_signal = True
                reason = "stop_z"
            elif pnl < -notional * 0.025:
                exit_signal = True
                reason = "stop_loss"

            if exit_signal:
                closed = self.portfolio.close_spread(pos.pair_id, pa, pb)
                self.risk.record_pnl(closed)
                total_pnl += closed
                logger.info("CLOSE %s pnl=$%.2f z=%.2f [%s]", pos.pair_id, closed, z, reason)

        return total_pnl
