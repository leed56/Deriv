"""
Deriv Bot Configuration
Set your API token in .env file or pass directly.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# --- Deriv API ---
DERIV_WS_URL = "wss://ws.binaryws.com/websockets/v3?app_id=1089"
API_TOKEN = os.getenv("DERIV_API_TOKEN", "")  # Set in .env or pass at runtime

# --- Synthetic pairs to test ---
SYMBOLS = {
    "R_10":  {"name": "Volatility 10 Index",  "volatility": 10},
    "R_25":  {"name": "Volatility 25 Index",  "volatility": 25},
    "R_50":  {"name": "Volatility 50 Index",  "volatility": 50},
    "R_100": {"name": "Volatility 100 Index", "volatility": 100},
}

# --- Contract types supported ---
CONTRACT_TYPES = ["CALL", "PUT"]          # Rise / Fall
DIGIT_CONTRACT_TYPES = ["DIGITOVER", "DIGITUNDER", "DIGITMATCH", "DIGITDIFF"]

# --- Default trade settings ---
DEFAULT_STAKE     = 1.0       # USD per trade
DEFAULT_DURATION  = 5         # ticks
DEFAULT_DURATION_UNIT = "t"   # t=ticks, s=seconds, m=minutes

# --- Risk management ---
MAX_DAILY_LOSS_PCT  = 0.20    # stop trading after 20% drawdown of starting balance
MAX_CONSECUTIVE_LOSS = 5      # stop if 5 losses in a row
TAKE_PROFIT_PCT      = 0.30   # take profit at 30% gain

# --- Strategy parameters ---
STRATEGY_PARAMS = {
    "rsi_reversal": {
        "rsi_period":   14,
        "oversold":     30,
        "overbought":   70,
        "duration":      5,   # ticks
        "best_for":     ["R_10", "R_25"],
    },
    "bollinger_bands": {
        "period":       20,
        "std_dev":       2.0,
        "duration":      5,
        "best_for":     ["R_10", "R_25", "R_50", "R_100"],
    },
    "ema_crossover": {
        "fast_period":   5,
        "slow_period":  20,
        "duration":      3,
        "best_for":     ["R_50", "R_100"],
    },
    "digit_analysis": {
        "lookback":     50,    # last N ticks to analyse digit distribution
        "threshold":   0.08,   # bet when digit freq drops below 8% (expected 10%)
        "contract":    "DIGITOVER",
        "barrier":      "7",
        "duration":      1,
        "best_for":     ["R_10"],
    },
    "momentum_breakout": {
        "period":       10,
        "multiplier":    1.5,  # ATR multiplier for breakout level
        "duration":      5,
        "best_for":     ["R_50", "R_100"],
    },
}

# --- Backtesting ---
BACKTEST_TICK_COUNT = 5000    # historical ticks to fetch per symbol
