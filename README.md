# Deribit Autonomous Profit Bot

Fully autonomous demo trading bot for [Deribit testnet](https://test.deribit.com) with **real market data** over WebSocket. Targets **$5/day profit** (configurable) using advanced statistical methods — **no lagging indicators** (no RSI, MACD, moving averages).

## Strategy

The bot trades a **synthetic BTC/ETH log-spread** pair:

```
spread = log(BTC) − β × log(ETH)
```

| Component | Method |
|-----------|--------|
| Fair value & hedge ratio | Online **Kalman filter** (dynamic β) |
| Entry/exit | Innovation **z-score** mean reversion |
| Timing | **Order-book imbalance** microstructure |
| Carry bias | **Funding rate** differential between perps |

When the spread deviates from Kalman fair value beyond a threshold, the bot opens a hedged pair trade (long BTC / short ETH or vice versa) and closes on mean reversion or daily risk limits.

## Features

- Real-time Deribit WebSocket (tickers + order books)
- Testnet live orders or **paper mode** without API keys
- Daily P&L tracking with $5 profit target and loss limit
- SQLite persistence for trades and snapshots
- Autonomous loop: subscribe → signal → execute → reconcile

## Quick Start

### 1. Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure (optional for paper mode)

```bash
cp .env.example .env
```

Get testnet API keys at [test.deribit.com](https://test.deribit.com) → Account → API.

```env
DERIBIT_CLIENT_ID=your_client_id
DERIBIT_CLIENT_SECRET=your_client_secret
DERIBIT_ENV=testnet
DAILY_PROFIT_TARGET_USD=5.0
```

Without keys, the bot runs in **paper mode**: real data, simulated fills.

### 3. Run

```bash
chmod +x scripts/run_bot.sh
./scripts/run_bot.sh
```

Or:

```bash
PYTHONPATH=. python -m src.main
```

Press `Ctrl+C` to stop gracefully (open positions are closed).

## Configuration

Edit `config/settings.yaml`:

| Key | Default | Description |
|-----|---------|-------------|
| `risk.daily_profit_target_usd` | 5.0 | Stop new trades after reaching target |
| `risk.daily_loss_limit_usd` | 15.0 | Halt trading on max daily loss |
| `strategy.entry_zscore` | 2.0 | Min z-score to enter |
| `strategy.exit_zscore` | 0.4 | Exit when spread reverts |
| `risk.max_position_usd` | 500 | Max notional per leg |

## Architecture

```
src/
├── main.py              # Autonomous event loop
├── api/client.py        # Deribit WebSocket JSON-RPC
├── strategy/
│   ├── kalman.py        # Dynamic hedge ratio filter
│   ├── microstructure.py# Order-book imbalance
│   └── engine.py        # Signal fusion
├── risk/manager.py      # Daily target & position sizing
├── execution/order_manager.py
└── storage/state.py     # SQLite persistence
```

## Risk Disclaimer

This is for **Deribit testnet / educational use only**. Past simulated performance does not guarantee future results. Crypto derivatives involve substantial risk. Never deploy unreviewed bots with real funds.

## License

MIT
