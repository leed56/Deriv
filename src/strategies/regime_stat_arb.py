"""Regime-aware statistical arbitrage using variance break detection."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.strategies.base import Side, Signal, Strategy
from src.synthetic.pairs import SyntheticPair


class RegimeStatArbStrategy(Strategy):
    """
    Detects low-volatility mean-reverting regimes via rolling variance ratio.
    Trades only when spread is stationary and volatility regime favors reversion.
    """

    name = "regime_stat_arb"

    def __init__(
        self,
        entry_z: float = 1.8,
        var_ratio_threshold: float = 0.85,
        window: int = 60,
    ) -> None:
        self.entry_z = entry_z
        self.var_ratio_threshold = var_ratio_threshold
        self.window = window

    def _variance_ratio(self, spread: pd.Series, lag: int = 5) -> float:
        """Lo-MacKinlay style variance ratio — stationary if ~1."""
        s = spread.dropna()
        if len(s) < lag * 4:
            return 1.0
        returns = s.diff().dropna()
        var_1 = returns.var()
        var_lag = s.diff(lag).dropna().var() / lag
        if var_1 == 0:
            return 1.0
        return float(var_lag / var_1)

    def _regime_score(self, spread: pd.Series) -> float:
        """Score 0-1: higher = more mean-reverting regime."""
        vr = self._variance_ratio(spread)
        # VR < 1 suggests mean reversion
        reversion = max(0.0, 1.0 - vr) if vr < 1.0 else 0.0

        recent = spread.iloc[-self.window :]
        vol = recent.std()
        long_vol = spread.std()
        vol_compression = 1.0 - min(vol / long_vol, 1.0) if long_vol > 0 else 0.0

        return 0.6 * reversion + 0.4 * vol_compression

    def generate(
        self,
        prices: pd.DataFrame,
        pairs: list[SyntheticPair],
        context: dict | None = None,
    ) -> list[Signal]:
        signals: list[Signal] = []
        for pair in pairs:
            spread = pair.spread(prices)
            if len(spread) < self.window * 2:
                continue

            regime = self._regime_score(spread)
            if regime < self.var_ratio_threshold:
                continue

            recent = spread.iloc[-self.window :]
            z = (spread.iloc[-1] - recent.mean()) / recent.std()
            if np.isnan(z) or abs(z) < self.entry_z:
                continue

            side = Side.SHORT if z > 0 else Side.LONG
            strength = min(abs(z) / (self.entry_z * 1.5), 1.0) * regime

            signals.append(
                Signal(
                    strategy=self.name,
                    pair=pair,
                    side=side,
                    strength=float(strength),
                    entry_z=float(z),
                    metadata={"regime_score": regime},
                )
            )
        return signals
