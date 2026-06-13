from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from src.config import StrategyConfig
from src.strategy.kalman import KalmanSpreadFilter
from src.strategy.microstructure import MicrostructureState


class SignalSide(Enum):
    FLAT = "flat"
    LONG_SPREAD = "long_spread"   # long BTC, short ETH (spread too low)
    SHORT_SPREAD = "short_spread"  # short BTC, long ETH (spread too high)


@dataclass
class MarketTick:
    btc_price: float
    eth_price: float
    btc_funding: float = 0.0
    eth_funding: float = 0.0
    micro: MicrostructureState = field(default_factory=MicrostructureState)
    timestamp: float = 0.0


@dataclass
class StrategySignal:
    side: SignalSide
    confidence: float
    zscore: float
    beta: float
    spread: float
    fair_spread: float
    reason: str


class SyntheticSpreadEngine:
    """
    Autonomous strategy combining:
    - Kalman-filtered synthetic BTC/ETH log-spread
    - Order-book microstructure (no RSI/MACD/MA)
    - Funding rate differential for carry bias
    """

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        self.kalman = KalmanSpreadFilter(
            process_noise=config.kalman.process_noise,
            observation_noise=config.kalman.observation_noise,
            beta_init=config.kalman.beta_init,
        )
        self._position_side = SignalSide.FLAT
        self._entry_zscore = 0.0

    @property
    def position_side(self) -> SignalSide:
        return self._position_side

    def set_position(self, side: SignalSide, entry_zscore: float = 0.0) -> None:
        self._position_side = side
        self._entry_zscore = entry_zscore

    def evaluate(self, tick: MarketTick) -> StrategySignal:
        log_btc = math.log(max(tick.btc_price, 1e-8))
        log_eth = math.log(max(tick.eth_price, 1e-8))
        spread, innovation, zscore = self.kalman.update(log_btc, log_eth)

        if self.kalman.tick_count < self.config.warmup_ticks:
            return StrategySignal(
                side=SignalSide.FLAT,
                confidence=0.0,
                zscore=zscore,
                beta=self.kalman.beta,
                spread=spread,
                fair_spread=self.kalman.fair_spread,
                reason="warmup",
            )

        # Funding differential: positive = BTC more expensive to hold long
        funding_edge = tick.btc_funding - tick.eth_funding
        funding_signal = -math.tanh(funding_edge * 500)  # mean-revert carry

        micro_pressure = tick.micro.composite_pressure()
        micro_signal = math.tanh(micro_pressure * 2)

        # Exit logic for open positions
        if self._position_side != SignalSide.FLAT:
            revert = abs(zscore) < self.config.exit_zscore
            flip = (
                self._position_side == SignalSide.LONG_SPREAD and zscore > self.config.entry_zscore
            ) or (
                self._position_side == SignalSide.SHORT_SPREAD and zscore < -self.config.entry_zscore
            )
            if revert:
                return StrategySignal(
                    side=SignalSide.FLAT,
                    confidence=0.9,
                    zscore=zscore,
                    beta=self.kalman.beta,
                    spread=spread,
                    fair_spread=self.kalman.fair_spread,
                    reason="mean_reversion_exit",
                )
            if flip:
                return StrategySignal(
                    side=SignalSide.FLAT,
                    confidence=0.85,
                    zscore=zscore,
                    beta=self.kalman.beta,
                    spread=spread,
                    fair_spread=self.kalman.fair_spread,
                    reason="stop_flip",
                )
            return StrategySignal(
                side=self._position_side,
                confidence=0.5,
                zscore=zscore,
                beta=self.kalman.beta,
                spread=spread,
                fair_spread=self.kalman.fair_spread,
                reason="hold",
            )

        if not tick.micro.liquidity_ok():
            return StrategySignal(
                side=SignalSide.FLAT,
                confidence=0.0,
                zscore=zscore,
                beta=self.kalman.beta,
                spread=spread,
                fair_spread=self.kalman.fair_spread,
                reason="illiquid",
            )

        # Entry: z-score driven with microstructure + funding confirmation
        stat_signal = -zscore  # mean revert: short spread when z high
        combined = (
            stat_signal * (1 - self.config.obi_weight - self.config.funding_weight)
            + micro_signal * self.config.obi_weight
            + funding_signal * self.config.funding_weight
        )
        confidence = min(1.0, abs(combined) / self.config.entry_zscore)

        if abs(zscore) < self.config.entry_zscore or confidence < self.config.min_confidence:
            return StrategySignal(
                side=SignalSide.FLAT,
                confidence=confidence,
                zscore=zscore,
                beta=self.kalman.beta,
                spread=spread,
                fair_spread=self.kalman.fair_spread,
                reason="no_edge",
            )

        if combined > 0:
            side = SignalSide.LONG_SPREAD
            reason = "spread_below_fair"
        else:
            side = SignalSide.SHORT_SPREAD
            reason = "spread_above_fair"

        return StrategySignal(
            side=side,
            confidence=confidence,
            zscore=zscore,
            beta=self.kalman.beta,
            spread=spread,
            fair_spread=self.kalman.fair_spread,
            reason=reason,
        )
