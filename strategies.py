"""
Strategy library for Deriv synthetic pairs.

Each strategy exposes:
    signal(prices, symbol, params) -> dict | None
        Returns a trade dict {"contract_type", "duration", "duration_unit", "barrier"(opt)}
        or None if no signal.
"""

from indicators import rsi, ema, bollinger_bands, atr, last_digits


# ─────────────────────────────────────────────────────────────────────────────
# 1. RSI Reversal  (best: V10, V25)
#    Buy CALL when oversold, PUT when overbought.
# ─────────────────────────────────────────────────────────────────────────────
def rsi_reversal(prices: list[float], symbol: str, params: dict) -> dict | None:
    if len(prices) < params["rsi_period"] + 5:
        return None
    r = rsi(prices, params["rsi_period"])
    if r < params["oversold"]:
        return {"contract_type": "CALL", "duration": params["duration"], "duration_unit": "t",
                "signal_strength": (params["oversold"] - r) / params["oversold"]}
    if r > params["overbought"]:
        return {"contract_type": "PUT", "duration": params["duration"], "duration_unit": "t",
                "signal_strength": (r - params["overbought"]) / (100 - params["overbought"])}
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 2. Bollinger Bands Reversal  (all pairs)
#    Buy CALL when price < lower band, PUT when price > upper band.
# ─────────────────────────────────────────────────────────────────────────────
def bollinger_bands_strategy(prices: list[float], symbol: str, params: dict) -> dict | None:
    if len(prices) < params["period"] + 1:
        return None
    upper, mid, lower = bollinger_bands(prices, params["period"], params["std_dev"])
    price = prices[-1]
    if price < lower:
        dist = (lower - price) / (upper - lower + 1e-9)
        return {"contract_type": "CALL", "duration": params["duration"], "duration_unit": "t",
                "signal_strength": min(dist, 1.0)}
    if price > upper:
        dist = (price - upper) / (upper - lower + 1e-9)
        return {"contract_type": "PUT", "duration": params["duration"], "duration_unit": "t",
                "signal_strength": min(dist, 1.0)}
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 3. EMA Crossover  (best: V50, V100)
#    Buy CALL when fast EMA crosses above slow EMA, PUT on downward cross.
# ─────────────────────────────────────────────────────────────────────────────
def ema_crossover(prices: list[float], symbol: str, params: dict) -> dict | None:
    need = max(params["fast_period"], params["slow_period"]) + 3
    if len(prices) < need:
        return None
    fast = ema(prices, params["fast_period"])
    slow = ema(prices, params["slow_period"])
    # need at least 2 valid values on both series
    f_curr, f_prev = fast[-1], fast[-2]
    s_curr, s_prev = slow[-1], slow[-2]
    if any(v != v for v in [f_curr, f_prev, s_curr, s_prev]):  # nan check
        return None
    bullish_cross = f_prev <= s_prev and f_curr > s_curr
    bearish_cross = f_prev >= s_prev and f_curr < s_curr
    if bullish_cross:
        return {"contract_type": "CALL", "duration": params["duration"], "duration_unit": "t",
                "signal_strength": abs(f_curr - s_curr) / (s_curr + 1e-9)}
    if bearish_cross:
        return {"contract_type": "PUT", "duration": params["duration"], "duration_unit": "t",
                "signal_strength": abs(f_curr - s_curr) / (s_curr + 1e-9)}
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 4. Digit Analysis  (best: V10)
#    Bet DIGITOVER 7 when low-digit distribution is statistically skewed.
# ─────────────────────────────────────────────────────────────────────────────
def digit_analysis(prices: list[float], symbol: str, params: dict) -> dict | None:
    if len(prices) < params["lookback"]:
        return None
    freqs = last_digits(prices, params["lookback"])
    # Count how many digits 0-7 appear (for DIGITOVER 7, we need last digit > 7)
    high_digit_freq = freqs.get(8, 0) + freqs.get(9, 0)
    low_digit_freq  = sum(freqs.get(d, 0) for d in range(8))
    # If last digit ≤ 7 has been dominant, expect reversion to digits 8-9
    if low_digit_freq > (1 - params["threshold"]):
        return {"contract_type": params["contract"], "duration": params["duration"],
                "duration_unit": "t", "barrier": params["barrier"],
                "signal_strength": low_digit_freq - 0.80}
    # Inverse: too many high digits → bet DIGITUNDER 2
    if high_digit_freq > params["threshold"] * 3:
        return {"contract_type": "DIGITUNDER", "duration": params["duration"],
                "duration_unit": "t", "barrier": "2",
                "signal_strength": high_digit_freq - 0.10}
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 5. Momentum Breakout  (best: V50, V100)
#    Buy in direction of momentum when price breaks ATR-based band.
# ─────────────────────────────────────────────────────────────────────────────
def momentum_breakout(prices: list[float], symbol: str, params: dict) -> dict | None:
    period = params["period"]
    if len(prices) < period + 2:
        return None
    window = prices[-period:]
    mean_p = sum(window) / len(window)
    at = atr(window, window, prices[-(period + 1):], period)
    band = at * params["multiplier"]
    price = prices[-1]
    prev  = prices[-2]
    if prev <= mean_p + band < price:
        return {"contract_type": "CALL", "duration": params["duration"], "duration_unit": "t",
                "signal_strength": (price - (mean_p + band)) / (at + 1e-9)}
    if prev >= mean_p - band > price:
        return {"contract_type": "PUT", "duration": params["duration"], "duration_unit": "t",
                "signal_strength": ((mean_p - band) - price) / (at + 1e-9)}
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────
STRATEGIES = {
    "rsi_reversal":       rsi_reversal,
    "bollinger_bands":    bollinger_bands_strategy,
    "ema_crossover":      ema_crossover,
    "digit_analysis":     digit_analysis,
    "momentum_breakout":  momentum_breakout,
}
