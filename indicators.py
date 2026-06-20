"""
Technical indicators — pure numpy, no extra dependencies.
"""

import numpy as np


def rsi(prices: list[float], period: int = 14) -> float:
    """Relative Strength Index (last value)."""
    if len(prices) < period + 1:
        return 50.0
    deltas = np.diff(prices[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = gains.mean()
    avg_loss = losses.mean()
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def ema(prices: list[float], period: int) -> list[float]:
    """Exponential Moving Average series."""
    if len(prices) < period:
        return [float("nan")] * len(prices)
    k = 2.0 / (period + 1)
    result = [float("nan")] * (period - 1)
    result.append(float(np.mean(prices[:period])))
    for p in prices[period:]:
        result.append(p * k + result[-1] * (1 - k))
    return result


def bollinger_bands(
    prices: list[float], period: int = 20, std_dev: float = 2.0
) -> tuple[float, float, float]:
    """Returns (upper, middle, lower) for the latest bar."""
    if len(prices) < period:
        mid = float(np.mean(prices))
        return mid, mid, mid
    window = prices[-period:]
    mid = float(np.mean(window))
    std = float(np.std(window, ddof=1))
    return mid + std_dev * std, mid, mid - std_dev * std


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Average True Range. For tick data we approximate using |high - low|."""
    if len(closes) < 2:
        return 0.0
    trs = []
    for i in range(1, min(period + 1, len(closes))):
        tr = max(
            highs[-i] - lows[-i],
            abs(highs[-i] - closes[-(i + 1)]),
            abs(lows[-i] - closes[-(i + 1)]),
        )
        trs.append(tr)
    return float(np.mean(trs)) if trs else 0.0


def last_digits(prices: list[float], n: int = 50) -> dict[int, float]:
    """
    Returns relative frequency of the last digit (0-9) in the last n prices.
    Key = digit, Value = frequency (0-1).
    """
    digits = [int(str(round(p, 2)).replace(".", "")[-1]) for p in prices[-n:]]
    counts = {d: 0 for d in range(10)}
    for d in digits:
        counts[d] += 1
    total = len(digits)
    return {d: c / total for d, c in counts.items()}
