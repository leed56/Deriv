from __future__ import annotations

import math
from dataclasses import dataclass, field


# Annual vol fractions for Deriv synthetic indices
VOL_BY_SYMBOL: dict[str, float] = {
    "R_10": 0.10,
    "R_25": 0.25,
    "R_50": 0.50,
    "R_75": 0.75,
    "R_100": 1.00,
    "1HZ10V": 0.10,
    "1HZ25V": 0.25,
    "1HZ50V": 0.50,
    "1HZ75V": 0.75,
    "1HZ100V": 1.00,
}

# R_* = 2s ticks; 1HZ* = 1s ticks
TICK_INTERVAL_SEC: dict[str, float] = {
    "R_10": 2.0,
    "R_25": 2.0,
    "R_50": 2.0,
    "R_75": 2.0,
    "R_100": 2.0,
    "1HZ10V": 1.0,
    "1HZ25V": 1.0,
    "1HZ50V": 1.0,
    "1HZ75V": 1.0,
    "1HZ100V": 1.0,
}


@dataclass
class TickState:
    symbol: str
    price: float = 0.0
    prev_price: float = 0.0
    epoch: int = 0
    ewma_norm_return: float = 0.0
    vol_pct: float = 10.0

    def update(self, quote: float, epoch: int, alpha: float = 0.08) -> float | None:
        """Return vol-normalized log return for this tick, or None on first tick."""
        self.epoch = epoch
        if self.price <= 0:
            self.price = quote
            self.prev_price = quote
            return None
        self.prev_price = self.price
        self.price = quote
        log_ret = math.log(max(quote, 1e-12) / max(self.prev_price, 1e-12))
        sigma = VOL_BY_SYMBOL.get(self.symbol, self.vol_pct / 100.0)
        tick_sec = TICK_INTERVAL_SEC.get(self.symbol, 2.0)
        # Scale return by theoretical per-tick vol (√time scaling)
        expected_tick_vol = sigma * math.sqrt(tick_sec / (365.25 * 24 * 3600))
        norm = log_ret / max(expected_tick_vol, 1e-12)
        self.ewma_norm_return = (1 - alpha) * self.ewma_norm_return + alpha * norm
        return norm


@dataclass
class VolPairBook:
    """Live state for all volatility index legs."""

    legs: dict[str, TickState] = field(default_factory=dict)

    def ensure(self, symbol: str, vol_pct: float) -> TickState:
        if symbol not in self.legs:
            self.legs[symbol] = TickState(symbol=symbol, vol_pct=vol_pct)
        return self.legs[symbol]

    def ready(self, min_ticks: int = 30) -> bool:
        return all(leg.price > 0 and leg.prev_price > 0 for leg in self.legs.values())

    def momentum_spread(self, low_sym: str, high_sym: str) -> float:
        """High-vol minus low-vol normalized momentum — the synthetic vol pair."""
        low = self.legs[low_sym].ewma_norm_return
        high = self.legs[high_sym].ewma_norm_return
        return high - low

    def basket_pressure(self) -> dict[str, float]:
        """Per-symbol deviation from vol-weighted basket (for picking trade leg)."""
        if not self.legs:
            return {}
        weights = {s: 1.0 / max(VOL_BY_SYMBOL.get(s, 0.1), 0.01) for s in self.legs}
        wsum = sum(weights.values())
        basket = sum(self.legs[s].ewma_norm_return * weights[s] for s in self.legs) / wsum
        return {s: self.legs[s].ewma_norm_return - basket for s in self.legs}
