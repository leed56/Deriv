# Deriv Synthetic Pairs Trading Bot

Automated strategy tester and trader for Deriv synthetic (volatility) indices.

## Supported Pairs

| Symbol | Name | Best Strategies |
|--------|------|-----------------|
| R_10 | Volatility 10 Index | Digit Analysis, RSI Reversal |
| R_25 | Volatility 25 Index | RSI Reversal, Bollinger Bands |
| R_50 | Volatility 50 Index | EMA Crossover, Bollinger Bands |
| R_100 | Volatility 100 Index | Momentum Breakout, EMA Crossover |

## Strategies

| Strategy | Type | How it works |
|----------|------|--------------|
| `rsi_reversal` | Mean-reversion | Buy CALL when RSI < 30, PUT when RSI > 70 |
| `bollinger_bands` | Mean-reversion | Buy at band extremes |
| `ema_crossover` | Trend-following | Trade on fast/slow EMA cross |
| `digit_analysis` | Statistical | Bet on statistically underrepresented last digits |
| `momentum_breakout` | Trend-following | Enter when price breaks ATR band |

## Risk Modes

| Mode | Description |
|------|-------------|
| `fixed` | Same stake every trade (safest) |
| `martingale` | Double stake on loss, reset on win |
| `anti_martingale` | Double stake on win, reset on loss |
| `dalembert` | +1 unit on loss, -1 unit on win |

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your demo API token from https://app.deriv.com/account/api-token
```

## Usage

```bash
# Backtest all strategies on historical data (recommended first step)
python run.py --token YOUR_TOKEN --mode backtest

# Live signal mode (no trades placed)
python run.py --token YOUR_TOKEN --mode live

# Live demo trading (places real demo trades)
python run.py --token YOUR_TOKEN --mode trade --stake 1.0 --risk dalembert

# Target specific pairs and strategies
python run.py --token YOUR_TOKEN --mode trade \
  --symbols R_10 R_25 \
  --strategies rsi_reversal bollinger_bands \
  --stake 0.50 --risk fixed
```

## File Structure

```
deriv_api.py          WebSocket API client
strategies.py         All 5 strategy implementations
indicators.py         RSI, EMA, Bollinger Bands, ATR, digit analysis
risk_manager.py       Fixed / Martingale / Anti-Martingale / D'Alembert
backtest.py           Historical backtest runner
bot.py                Live trading bot
performance_tracker.py Results + design recommendations
config.py             All parameters in one place
run.py                CLI entry point
```

## Design Recommendations (pre-backtest defaults)

- **V10**: Use `digit_analysis` + `rsi_reversal`, fixed stake, 1–3 tick duration
- **V25**: Use `rsi_reversal` + `bollinger_bands`, D'Alembert risk, 3–5 ticks
- **V50**: Use `ema_crossover` + `bollinger_bands`, anti-martingale, 5–10 ticks
- **V100**: Use `momentum_breakout` + `ema_crossover`, fixed stake, 3–5 ticks

**Always run a backtest first.** The bot will override these defaults with the actual winning strategy from your backtest data.

> **Warning**: Trading involves risk. This bot is for educational and demo purposes. Never trade with money you can't afford to lose.
