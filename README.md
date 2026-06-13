# Deriv Autonomous Volatility Pair Bot

Fully autonomous demo trading bot for [Deriv](https://deriv.com) **synthetic volatility indices** — **Volatility 10, 25, 50, 75** (`R_10`, `R_25`, `R_50`, `R_75`). Uses **real tick data** via WebSocket. Targets **$5/day profit** with **low worst-case drawdown** through fixed-stake contracts.

> **Not Deribit.** This bot connects to Deriv's API (`ws.derivws.com`), not the crypto exchange Deribit.

## Synthetic Volatility Pairs

Deriv generates these indices algorithmically at **fixed volatility levels**:

| Symbol | Index | Volatility | Tick speed |
|--------|-------|------------|------------|
| `R_10` | Volatility 10 | 10% | every 2s |
| `R_25` | Volatility 25 | 25% | every 2s |
| `R_50` | Volatility 50 | 50% | every 2s |
| `R_75` | Volatility 75 | 75% | every 2s |

1-second variants: `1HZ10V`, `1HZ25V`, `1HZ50V`, `1HZ75V`.

## Strategy (no RSI / MACD / MA)

**Vol-normalized pair spread** between high-vol and low-vol indices:

```
spread = EWMA_norm_return(R_75) − EWMA_norm_return(R_10)
```

Each return is scaled by that index's theoretical vol (10%, 25%, etc.) so moves are comparable across pairs.

| Layer | Method |
|-------|--------|
| Fair value | **Kalman filter** on spread |
| Entry | Innovation **z-score** > 1.8 |
| Leg selection | Most misaligned index in basket |
| Execution | **CALL/PUT** over 5 ticks — stake = max loss |

### Why lower drawdown than perps

Deriv options use **fixed stake** = **maximum loss per trade** (e.g. $0.35–$2). Combined with:

- 5% daily loss limit (% of balance)
- 10% max drawdown circuit breaker
- One open contract at a time
- **Restart-safe limits** — halt state, daily P&L, anchor balance, and peak balance persist in `data/bot_state.db`. Restarting the bot on the same UTC day cannot bypass a halt.

To manually reset after a halt: `rm data/bot_state.db`

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env
# Optional: DERIV_API_TOKEN from https://app.deriv.com/account/api-token
./scripts/run_bot.sh
```

Without API token → **paper mode** (real ticks, simulated wins/losses).

## Configuration

`config/settings.yaml`:

| Key | Default | Description |
|-----|---------|-------------|
| `risk.daily_profit_target_usd` | 5.0 | Halt after +$5 |
| `risk.max_daily_loss_pct` | 0.05 | 5% of balance/day |
| `risk.max_drawdown_pct` | 0.10 | 10% session drawdown halt |
| `risk.max_stake_usd` | 2.0 | Max loss per contract |
| `strategy.spread_pair_low` | R_10 | Low-vol leg |
| `strategy.spread_pair_high` | R_75 | High-vol leg |

## Architecture

```
src/
├── main.py                 # Autonomous loop
├── api/client.py           # Deriv WebSocket API
├── strategy/
│   ├── vol_normalize.py    # Vol-scaled returns across V10–V75
│   ├── kalman.py           # Spread fair value
│   └── engine.py           # Signal + leg picker
├── risk/manager.py         # % equity limits + stake sizing
└── execution/order_manager.py  # proposal → buy
```

## Risk Disclaimer

Demo/educational use only. Synthetic indices are RNG-driven; no strategy guarantees profit. Never trade live without thorough testing.
