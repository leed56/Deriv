"""Synthetic pair construction from correlated assets."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.tsa.stattools import adfuller, coint


@dataclass
class SyntheticPair:
    """A tradeable synthetic spread between two assets."""

    leg_a: str
    leg_b: str
    hedge_ratio: float
    name: str
    coint_pvalue: float
    half_life_bars: float

    def spread(self, prices: pd.DataFrame) -> pd.Series:
        log_a = np.log(prices[self.leg_a])
        log_b = np.log(prices[self.leg_b])
        return log_a - self.hedge_ratio * log_b

    def zscore(self, prices: pd.DataFrame, window: int = 60) -> float:
        s = self.spread(prices)
        if len(s) < window:
            return 0.0
        recent = s.iloc[-window:]
        mu, sigma = recent.mean(), recent.std()
        if sigma == 0 or np.isnan(sigma):
            return 0.0
        return float((s.iloc[-1] - mu) / sigma)


def estimate_half_life(spread: pd.Series) -> float:
    """Ornstein-Uhlenbeck half-life via AR(1) on spread differences."""
    lag = spread.shift(1).dropna()
    delta = spread.diff().dropna()
    aligned = pd.concat([lag, delta], axis=1).dropna()
    if len(aligned) < 30:
        return float("inf")
    x = aligned.iloc[:, 0].values
    y = aligned.iloc[:, 1].values
    slope, _, _, _, _ = stats.linregress(x, y)
    if slope >= 0:
        return float("inf")
    return float(-np.log(2) / slope)


def kalman_hedge_ratio(y: pd.Series, x: pd.Series) -> pd.Series:
    """Dynamic hedge ratio via rolling Kalman-style recursive least squares."""
    n = len(y)
    beta = np.zeros(n)
    P = 1.0
    Q = 1e-5
    R = 1e-3
    b = 1.0
    yv, xv = y.values, x.values
    for t in range(1, n):
        P = P + Q
        if xv[t] == 0:
            beta[t] = b
            continue
        K = P * xv[t] / (xv[t] ** 2 * P + R)
        b = b + K * (yv[t] - b * xv[t])
        P = (1 - K * xv[t]) * P
        beta[t] = b
    return pd.Series(beta, index=y.index)


def discover_pairs(
    prices: pd.DataFrame,
    max_pairs: int = 6,
    min_half_life: float = 5.0,
    max_half_life: float = 120.0,
    coint_threshold: float = 0.05,
) -> list[SyntheticPair]:
    """Find cointegrated synthetic pairs from price matrix."""
    symbols = list(prices.columns)
    candidates: list[SyntheticPair] = []

    for i, leg_a in enumerate(symbols):
        for leg_b in symbols[i + 1 :]:
            log_a = np.log(prices[leg_a].dropna())
            log_b = np.log(prices[leg_b].dropna())
            aligned = pd.concat([log_a, log_b], axis=1).dropna()
            if len(aligned) < 100:
                continue

            _, pvalue, _ = coint(aligned.iloc[:, 0], aligned.iloc[:, 1])
            if pvalue > coint_threshold:
                continue

            hedge = kalman_hedge_ratio(aligned.iloc[:, 0], aligned.iloc[:, 1]).iloc[-1]
            spread = aligned.iloc[:, 0] - hedge * aligned.iloc[:, 1]
            hl = estimate_half_life(spread)
            if not (min_half_life <= hl <= max_half_life):
                continue

            adf_p = adfuller(spread.dropna())[1]
            if adf_p > 0.05:
                continue

            candidates.append(
                SyntheticPair(
                    leg_a=leg_a,
                    leg_b=leg_b,
                    hedge_ratio=float(hedge),
                    name=f"{leg_a.split('/')[0]}/{leg_b.split('/')[0]}",
                    coint_pvalue=float(pvalue),
                    half_life_bars=float(hl),
                )
            )

    candidates.sort(key=lambda p: p.coint_pvalue)
    return candidates[:max_pairs]


def ratio_synthetic(prices: pd.DataFrame, leg_a: str, leg_b: str) -> pd.Series:
    """Price ratio synthetic (non-log)."""
    return prices[leg_a] / prices[leg_b]
