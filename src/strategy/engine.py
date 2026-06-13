from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from src.config import StrategyConfig, VolPairLeg
from src.strategy.kalman import KalmanSpreadFilter
from src.strategy.vol_normalize import VolPairBook


class TradeAction(Enum):
    FLAT = "flat"
    CALL = "call"
    PUT = "put"


@dataclass
class VolPairSignal:
    action: TradeAction
    symbol: str
    confidence: float
    zscore: float
    spread: float
    fair_spread: float
    reason: str


class VolPairEngine:
    """
    Synthetic volatility pair strategy across Deriv V10/V25/V50/V75.

    Builds spread = normalized_momentum(R_75) - normalized_momentum(R_10)
    scaled by each index's fixed vol parameter (10%, 25%, 50%, 75%).

    Uses Kalman innovation z-scores — not RSI/MACD/MA.
  """

    def __init__(self, config: StrategyConfig, legs: list[VolPairLeg]) -> None:
        self.config = config
        self.legs = legs
        self.book = VolPairBook()
        for leg in legs:
            self.book.ensure(leg.symbol, leg.vol_pct)
        self.kalman = KalmanSpreadFilter(
            process_noise=config.kalman.process_noise,
            observation_noise=config.kalman.observation_noise,
            beta_init=1.0,
        )
        self._tick_count = 0
        self._in_contract = False

    def set_in_contract(self, active: bool) -> None:
        self._in_contract = active

    def on_tick(self, symbol: str, quote: float, epoch: int) -> VolPairSignal | None:
        leg = next((l for l in self.legs if l.symbol == symbol), None)
        if not leg:
            return None
        state = self.book.ensure(leg.symbol, leg.vol_pct)
        norm = state.update(quote, epoch)
        if norm is None:
            return None
        self._tick_count += 1
        return self.evaluate()

    def evaluate(self) -> VolPairSignal:
        low = self.config.spread_pair_low
        high = self.config.spread_pair_high
        spread = self.book.momentum_spread(low, high)
        _, _, zscore = self.kalman.update(spread, 0.0)  # univariate spread filter

        if self._tick_count < self.config.warmup_ticks or not self.book.ready():
            return VolPairSignal(TradeAction.FLAT, "", 0.0, zscore, spread, self.kalman.fair_spread, "warmup")

        if self._in_contract:
            if abs(zscore) < self.config.exit_zscore:
                return VolPairSignal(TradeAction.FLAT, "", 0.9, zscore, spread, self.kalman.fair_spread, "revert_exit")
            return VolPairSignal(TradeAction.FLAT, "", 0.0, zscore, spread, self.kalman.fair_spread, "hold_contract")

        confidence = min(1.0, abs(zscore) / self.config.entry_zscore)
        if abs(zscore) < self.config.entry_zscore or confidence < self.config.min_confidence:
            return VolPairSignal(TradeAction.FLAT, "", confidence, zscore, spread, self.kalman.fair_spread, "no_edge")

        # Pick the most misaligned leg to trade (max drawdown control: one contract)
        pressure = self.book.basket_pressure()
        if zscore > 0:
            # Spread too high: high-vol index overshooting → PUT it, or CALL low-vol
            sym = high if pressure.get(high, 0) > abs(pressure.get(low, 0)) else low
            action = TradeAction.PUT if sym == high else TradeAction.CALL
            reason = "high_vol_overextended"
        else:
            sym = low if pressure.get(low, 0) < -abs(pressure.get(high, 0)) else high
            action = TradeAction.PUT if sym == high else TradeAction.CALL
            reason = "low_vol_underextended"

        return VolPairSignal(action, sym, confidence, zscore, spread, self.kalman.fair_spread, reason)
