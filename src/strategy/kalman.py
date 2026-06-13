from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class KalmanSpreadFilter:
    """Univariate Kalman filter for synthetic vol-pair spread."""

    process_noise: float = 1e-4
    observation_noise: float = 1e-2
    beta_init: float = 1.0

    spread_mean: float = field(init=False, default=0.0)
    spread_var: float = field(init=False, default=1.0)
    initialized: bool = field(init=False, default=False)
    tick_count: int = field(init=False, default=0)
    last_zscore: float = field(init=False, default=0.0)

    def update(self, spread: float, _: float = 0.0) -> tuple[float, float, float]:
        self.tick_count += 1
        if not self.initialized:
            self.spread_mean = spread
            self.initialized = True
            return spread, 0.0, 0.0

        pred_var = self.spread_var + self.process_noise
        innovation = spread - self.spread_mean
        obs_var = self.observation_noise + pred_var
        gain = pred_var / obs_var
        self.spread_mean = self.spread_mean + gain * innovation
        self.spread_var = (1 - gain) * pred_var
        zscore = innovation / math.sqrt(max(obs_var, 1e-12))
        self.last_zscore = zscore
        return spread, innovation, zscore

    @property
    def fair_spread(self) -> float:
        return self.spread_mean
