"""Ornstein-Uhlenbeck mean reversion on synthetic spreads."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.strategies.base import Side, Signal, Strategy
from src.synthetic.pairs import SyntheticPair, estimate_half_life


class OUMeanReversionStrategy(Strategy):
    """
    Trades synthetic pairs when spread deviates from OU equilibrium.
    Uses maximum-likelihood OU parameters — no RSI/MACD/EMA.
    """

    name = "ou_mean_reversion"

    def __init__(
        self,
        entry_z: float = 2.0,
        exit_z: float = 0.3,
        min_half_life: float = 5.0,
        max_half_life: float = 90.0,
    ) -> None:
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.min_half_life = min_half_life
        self.max_half_life = max_half_life

    def _ou_params(self, spread: pd.Series) -> tuple[float, float, float]:
        """Estimate theta (mean reversion speed), mu, sigma via discrete OU."""
        s = spread.dropna()
        if len(s) < 50:
            return 0.0, float(s.iloc[-1]), 1.0

        dt = 1.0
        s_lag = s.shift(1).dropna()
        s_now = s.iloc[1:]
        s_lag = s_lag.iloc[: len(s_now)]

        slope, intercept, _, _, _ = __import__("scipy.stats", fromlist=["linregress"]).linregress(
            s_lag.values, s_now.values
        )
        theta = -np.log(max(slope, 1e-6)) / dt
        mu = intercept / (1 - slope) if abs(1 - slope) > 1e-6 else float(s.mean())
        residuals = s_now.values - (intercept + slope * s_lag.values)
        sigma = float(np.std(residuals))
        return float(theta), float(mu), sigma

    def generate(
        self,
        prices: pd.DataFrame,
        pairs: list[SyntheticPair],
        context: dict | None = None,
    ) -> list[Signal]:
        signals: list[Signal] = []
        for pair in pairs:
            spread = pair.spread(prices)
            hl = estimate_half_life(spread)
            if not (self.min_half_life <= hl <= self.max_half_life):
                continue

            theta, mu, sigma = self._ou_params(spread)
            if theta <= 0 or sigma == 0:
                continue

            current = float(spread.iloc[-1])
            z = (current - mu) / sigma

            side = Side.FLAT
            strength = min(abs(z) / self.entry_z, 1.0)

            if z > self.entry_z:
                # Spread too high: short spread = short A, long B
                side = Side.SHORT
            elif z < -self.entry_z:
                side = Side.LONG

            if side != Side.FLAT:
                signals.append(
                    Signal(
                        strategy=self.name,
                        pair=pair,
                        side=side,
                        strength=strength,
                        entry_z=z,
                        target_z=0.0,
                        stop_z=self.entry_z * 1.75,
                        metadata={
                            "theta": theta,
                            "mu": mu,
                            "sigma": sigma,
                            "half_life": hl,
                        },
                    )
                )
        return signals
