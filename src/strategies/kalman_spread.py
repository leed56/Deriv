"""Kalman-filter dynamic spread strategy with microstructure confirmation."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.strategies.base import Side, Signal, Strategy
from src.synthetic.pairs import SyntheticPair, kalman_hedge_ratio


class KalmanSpreadStrategy(Strategy):
    """
    Adaptive hedge ratio via Kalman filter on log prices.
    Entry when filtered residual exceeds adaptive band.
  """

    name = "kalman_spread"

    def __init__(self, entry_sigma: float = 2.2, lookback: int = 120) -> None:
        self.entry_sigma = entry_sigma
        self.lookback = lookback

    def _filtered_residual(self, y: pd.Series, x: pd.Series) -> tuple[pd.Series, float]:
        n = len(y)
        if n < 30:
            return pd.Series(dtype=float), 0.0

        beta = kalman_hedge_ratio(y, x)
        residual = y - beta * x
        return residual, float(residual.iloc[-1])

    def generate(
        self,
        prices: pd.DataFrame,
        pairs: list[SyntheticPair],
        context: dict | None = None,
    ) -> list[Signal]:
        signals: list[Signal] = []
        imbalances: dict = (context or {}).get("imbalances", {})

        for pair in pairs:
            log_a = np.log(prices[pair.leg_a])
            log_b = np.log(prices[pair.leg_b])
            aligned = pd.concat([log_a, log_b], axis=1).dropna()
            if len(aligned) < self.lookback:
                continue

            recent = aligned.iloc[-self.lookback :]
            residual, current = self._filtered_residual(recent.iloc[:, 0], recent.iloc[:, 1])
            if residual.empty:
                continue

            mu = residual.mean()
            sigma = residual.std()
            if sigma == 0 or np.isnan(sigma):
                continue

            z = (current - mu) / sigma

            # Microstructure confirmation: require imbalance alignment
            imb_a = imbalances.get(pair.leg_a, 0.0)
            imb_b = imbalances.get(pair.leg_b, 0.0)
            micro_edge = imb_a - imb_b

            side = Side.FLAT
            if z > self.entry_sigma and micro_edge < 0.05:
                side = Side.SHORT
            elif z < -self.entry_sigma and micro_edge > -0.05:
                side = Side.LONG

            if side != Side.FLAT:
                strength = min(abs(z) / self.entry_sigma, 1.0) * 0.5 + min(abs(micro_edge) + 0.2, 1.0) * 0.5
                signals.append(
                    Signal(
                        strategy=self.name,
                        pair=pair,
                        side=side,
                        strength=float(strength),
                        entry_z=z,
                        metadata={"micro_edge": micro_edge, "sigma": sigma},
                    )
                )
        return signals
