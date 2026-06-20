"""
Main entry point.

Usage:
    python run.py --token <API_TOKEN> [options]

Modes:
    backtest   Fetch historical data, simulate strategies, print report.
    live       Connect to live tick stream, generate signals (no real trades).
    trade      Connect to live tick stream AND place demo trades.
"""

import argparse
import asyncio
import os
import sys
import logging

from colorama import Fore, Style, init
from dotenv import load_dotenv

from config import SYMBOLS, STRATEGY_PARAMS, DEFAULT_STAKE

init(autoreset=True)
load_dotenv()

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


def parse_args():
    p = argparse.ArgumentParser(description="Deriv Synthetic Pairs Trading Bot")
    p.add_argument("--token",     default=os.getenv("DERIV_API_TOKEN", ""),
                   help="Deriv API demo token (or set DERIV_API_TOKEN in .env)")
    p.add_argument("--mode",      choices=["backtest", "live", "trade"], default="backtest",
                   help="backtest=simulate on history | live=signals only | trade=real demo trades")
    p.add_argument("--symbols",   nargs="+", default=list(SYMBOLS.keys()),
                   choices=list(SYMBOLS.keys()),
                   help="Symbols to run (default: all)")
    p.add_argument("--strategies", nargs="+", default=list(STRATEGY_PARAMS.keys()),
                   choices=list(STRATEGY_PARAMS.keys()),
                   help="Strategies to run (default: all)")
    p.add_argument("--stake",     type=float, default=DEFAULT_STAKE,
                   help="Base stake per trade in USD")
    p.add_argument("--risk",      choices=["fixed", "martingale", "anti_martingale", "dalembert"],
                   default="fixed", help="Risk management mode")
    p.add_argument("--max-trades", type=int, default=50,
                   help="Max trades per symbol (live/trade mode)")
    return p.parse_args()


async def main():
    args = parse_args()

    if not args.token:
        print(f"\n{Fore.RED}No API token found.{Style.RESET_ALL}")
        print("Get your demo API token from: https://app.deriv.com/account/api-token")
        print("Then run:")
        print(f"  python run.py --token YOUR_TOKEN --mode backtest\n")
        sys.exit(1)

    print(f"\n{Fore.CYAN}{'='*60}")
    print(f"  Deriv Synthetic Pairs Bot")
    print(f"  Mode      : {args.mode.upper()}")
    print(f"  Symbols   : {', '.join(args.symbols)}")
    print(f"  Strategies: {', '.join(args.strategies)}")
    print(f"  Stake     : ${args.stake:.2f}")
    print(f"  Risk Mode : {args.risk}")
    print(f"{'='*60}{Style.RESET_ALL}\n")

    if args.mode == "backtest":
        from backtest import run_backtest
        await run_backtest(args.token, stake=args.stake)

    elif args.mode in ("live", "trade"):
        from bot import DerivBot
        bot = DerivBot(
            token=args.token,
            symbols=args.symbols,
            strategy_names=args.strategies,
            stake=args.stake,
            risk_mode=args.risk,
            max_trades=args.max_trades,
            live_trade=(args.mode == "trade"),
        )
        try:
            await bot.start()
        except KeyboardInterrupt:
            pass
        finally:
            await bot.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Bot stopped by user.{Style.RESET_ALL}")
