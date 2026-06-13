from __future__ import annotations

from dataclasses import dataclass, field
import math


@dataclass
class KalmanSpreadFilter:
    """
    Online Kalman filter estimating dynamic hedge ratio (beta) and
    synthetic spread fair value. Uses innovation variance for signal
    generation — not lagging technical indicators.
    """

    process_noise: float = 1e-5
    observation_noise: float = 1e-3
    beta_init: float = 1.35

    beta: float = field(init=False)
    spread_mean: float = field(init=False, default=0.0)
    spread_var: float = field(init=False, default=1.0)
    beta_var: float = field(init=False, default=0.01)
    initialized: bool = field(init=False, default=False)
    tick_count: int = field(init=False, default=0)
    last_innovation: float = field(init=False, default=0.0)
    last_zscore: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        self.beta = self.beta_init

    def update(self, log_btc: float, log_eth: float) -> tuple[float, float, float]:
        """
        Returns (spread, innovation, zscore).
        Spread = log_btc - beta * log_eth
        """
        self.tick_count += 1
        spread = log_btc - self.beta * log_eth

        if not self.initialized:
            self.spread_mean = spread
            self.initialized = True
            self.last_innovation = 0.0
            self.last_zscore = 0.0
            return spread, 0.0, 0.0

        # Predict
        pred_spread = self.spread_mean
        pred_var = self.spread_var + self.process_noise

        # Innovation
        innovation = spread - pred_spread
        obs_var = self.observation_noise + pred_var
        kalman_gain = pred_var / obs_var

        # Update spread state
        self.spread_mean = pred_spread + kalman_gain * innovation
        self.spread_var = (1 - kalman_gain) * pred_var

        # Update beta via gradient on innovation (adaptive hedge ratio)
        beta_gain = self.beta_var / (self.beta_var + 0.1)
        eth_contribution = log_eth
        beta_update = beta_gain * innovation * eth_contribution * 0.001
        self.beta = max(0.5, min(3.0, self.beta + beta_update))
        self.beta_var = (1 - beta_gain) * self.beta_var + self.process_noise

        zscore = innovation / math.sqrt(max(obs_var, 1e-12))
        self.last_innovation = innovation
        self.last_zscore = zscore
        return spread, innovation, zscore

    @property
    def fair_spread(self) -> float:
        return self.spread_mean

    @property
    def ready(self) -> bool:
        return self.tick_count >= 30
