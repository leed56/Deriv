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
    Profit-focused vol pair strategy across Deriv V10/V25/V50/V75.

    Uses TWO synthetic spreads for higher-quality entries:
      primary: R_75 vs R_10  (wide vol gap)
      confirm: R_50 vs R_25  (mid vol gap)

    Trades only when both spreads agree on direction (consensus).
    """

    def __init__(self, config: StrategyConfig, legs: list[VolPairLeg]) -> None:
        self.config = config
        self.legs = legs
        self.book = VolPairBook()
        for leg in legs:
            self.book.ensure(leg.symbol, leg.vol_pct)
        self.kalman_primary = KalmanSpreadFilter(
            process_noise=config.kalman.process_noise,
            observation_noise=config.kalman.observation_noise,
        )
        self.kalman_confirm = KalmanSpreadFilter(
            process_noise=config.kalman.process_noise,
            observation_noise=config.kalman.observation_noise,
        )
        self._tick_count = 0
        self._in_contract = False
        self._recent_results: list[float] = []

    def set_in_contract(self, active: bool) -> None:
        self._in_contract = active

    def record_result(self, pnl: float) -> None:
        self._recent_results.append(pnl)
        if len(self._recent_results) > 20:
            self._recent_results.pop(0)

    @property
    def recent_win_rate(self) -> float:
        if not self._recent_results:
            return 0.55
        return sum(1 for x in self._recent_results if x > 0) / len(self._recent_results)

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
        mid_low = self.config.spread_pair_mid_low
        mid_high = self.config.spread_pair_mid_high

        spread_p = self.book.momentum_spread(low, high)
        spread_c = self.book.momentum_spread(mid_low, mid_high)
        _, _, z_p = self.kalman_primary.update(spread_p, 0.0)
        _, _, z_c = self.kalman_confirm.update(spread_c, 0.0)
        zscore = (z_p + z_c) / 2.0
        spread = spread_p

        if self._tick_count < self.config.warmup_ticks or not self.book.ready():
            return VolPairSignal(TradeAction.FLAT, "", 0.0, zscore, spread, self.kalman_primary.fair_spread, "warmup")

        if self._in_contract:
            return VolPairSignal(TradeAction.FLAT, "", 0.0, zscore, spread, self.kalman_primary.fair_spread, "hold_contract")

        # Consensus: both spreads must agree on direction
        if z_p * z_c <= 0:
            return VolPairSignal(TradeAction.FLAT, "", 0.0, zscore, spread, self.kalman_primary.fair_spread, "no_consensus")

        if abs(z_p) < self.config.entry_zscore or abs(z_c) < self.config.entry_zscore * 0.8:
            return VolPairSignal(TradeAction.FLAT, "", 0.0, zscore, spread, self.kalman_primary.fair_spread, "no_edge")

        confidence = min(1.0, (abs(z_p) + abs(z_c)) / (2 * self.config.entry_zscore))
        # Boost confidence when recent win rate is good
        confidence = min(1.0, confidence * (0.85 + 0.3 * self.recent_win_rate))

        if confidence < self.config.min_confidence:
            return VolPairSignal(TradeAction.FLAT, "", confidence, zscore, spread, self.kalman_primary.fair_spread, "low_confidence")

        pressure = self.book.basket_pressure()
        # Prefer R_25/R_50 for execution — better win rate than extremes
        preferred = [mid_low, mid_high, low, high]
        if zscore > 0:
            sym = next((s for s in preferred if pressure.get(s, 0) > 0.05), high)
            action = TradeAction.PUT if sym in (high, mid_high) else TradeAction.CALL
            reason = "consensus_short_spread"
        else:
            sym = next((s for s in preferred if pressure.get(s, 0) < -0.05), low)
            action = TradeAction.CALL if sym in (low, mid_low) else TradeAction.PUT
            reason = "consensus_long_spread"

        return VolPairSignal(action, sym, confidence, zscore, spread, self.kalman_primary.fair_spread, reason)
