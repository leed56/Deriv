"""
Multi-Agent Deriv Trading System
=================================
Two AI bots on two separate demo accounts + one Watcher agent.

Usage:
    python run_multi.py --token-a TOKEN_A --token-b TOKEN_B [options]

Or set in .env:
    DERIV_TOKEN_A=...
    DERIV_TOKEN_B=...
"""

import argparse
import asyncio
import os
import sys
import logging

from colorama import Fore, Style, init
from dotenv import load_dotenv

from bot_agent import BotAgent
from watcher import WatcherAgent

init(autoreset=True)
load_dotenv()

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")


def parse_args():
    p = argparse.ArgumentParser(description="Deriv Multi-Agent System")
    p.add_argument("--token-a",  default=os.getenv("DERIV_TOKEN_A", ""),
                   help="API token for Demo Account A")
    p.add_argument("--token-b",  default=os.getenv("DERIV_TOKEN_B", ""),
                   help="API token for Demo Account B")
    p.add_argument("--stake",    type=float, default=1.0)
    p.add_argument("--max-trades", type=int, default=50,
                   help="Max trades per symbol per bot")
    p.add_argument("--live",     action="store_true",
                   help="Place real demo trades (default: signal-only)")
    p.add_argument("--watch-interval", type=int, default=30,
                   help="Watcher dashboard refresh seconds")
    return p.parse_args()


async def main():
    args = parse_args()

    if not args.token_a or not args.token_b:
        print(f"\n{Fore.RED}Two demo API tokens required.{Style.RESET_ALL}")
        print("Set in .env:")
        print("  DERIV_TOKEN_A=your_first_demo_token")
        print("  DERIV_TOKEN_B=your_second_demo_token")
        print("\nOr pass as flags:")
        print("  python run_multi.py --token-a TOKEN_A --token-b TOKEN_B --live\n")
        sys.exit(1)

    print(f"\n{Fore.CYAN}{'='*65}")
    print(f"  DERIV MULTI-AGENT SYSTEM")
    print(f"{'='*65}")
    print(f"  Bot A  →  Mean-Reversion  (RSI + Bollinger)   on V10, V25")
    print(f"  Bot B  →  Trend-Following (EMA + Momentum)    on V50, V100")
    print(f"  Watcher→  Compares bots, controls copy-trading")
    print(f"  Mode   →  {'LIVE DEMO TRADING' if args.live else 'Signal-only (no trades)'}")
    print(f"  Stake  →  ${args.stake:.2f} per trade")
    print(f"{'='*65}{Style.RESET_ALL}\n")

    # ── Bot A: mean-reversion on low-volatility pairs ──────────────
    bot_a = BotAgent(
        label="A",
        token=args.token_a,
        symbols=["R_10", "R_25"],
        strategy_names=["rsi_reversal", "bollinger_bands"],
        stake=args.stake,
        risk_mode="dalembert",
        max_trades=args.max_trades,
        live_trade=args.live,
    )

    # ── Bot B: trend-following on high-volatility pairs ────────────
    bot_b = BotAgent(
        label="B",
        token=args.token_b,
        symbols=["R_50", "R_100"],
        strategy_names=["ema_crossover", "momentum_breakout"],
        stake=args.stake,
        risk_mode="anti_martingale",
        max_trades=args.max_trades,
        live_trade=args.live,
    )

    # ── Watcher ────────────────────────────────────────────────────
    watcher = WatcherAgent(bot_a, bot_b, interval=args.watch_interval)

    print(f"{Fore.CYAN}Connecting both agents...{Style.RESET_ALL}\n")

    try:
        await asyncio.gather(
            bot_a.start(),
            bot_b.start(),
            watcher.run(),
        )
    except KeyboardInterrupt:
        pass
    finally:
        print(f"\n{Fore.YELLOW}Shutting down...{Style.RESET_ALL}")
        await watcher.stop()
        await bot_a.stop()
        await bot_b.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Stopped.{Style.RESET_ALL}")
