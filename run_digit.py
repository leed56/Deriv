"""
Digit Match / Differ Multi-Agent System
========================================
Account A  → DIFFER Guard   (bets DIFFER on hot digits, 90% win rate)
Account B  → MATCH Hunter   (bets MATCH on cold digits, ~10% win rate / 8x payout)
Watcher    → coordinates, fans out ticks, prevents conflicts, shows dashboard

Usage:
    python run_digit.py --token-a TOKEN_A --token-b TOKEN_B [--symbol R_10] [--live]

Recommended symbol: R_10 (slowest volatility = most stable digit distribution)
"""

import argparse, asyncio, os, sys, logging
from colorama import Fore, Style, init
from dotenv import load_dotenv
from digit_agents import DerivConn, DifferGuard, MatchHunter, DigitWatcher

init(autoreset=True)
load_dotenv()
logging.basicConfig(level=logging.WARNING)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--token-a", default=os.getenv("DERIV_TOKEN_A", ""))
    p.add_argument("--token-b", default=os.getenv("DERIV_TOKEN_B", ""))
    p.add_argument("--symbol",  default="R_10",
                   choices=["R_10","R_25","R_50","R_100"],
                   help="Symbol to trade (R_10 recommended for digit stability)")
    p.add_argument("--stake-differ", type=float, default=0.50)
    p.add_argument("--stake-match",  type=float, default=0.35)
    p.add_argument("--max-trades",   type=int,   default=200)
    p.add_argument("--live",    action="store_true",
                   help="Place real demo trades (default: print signals only)")
    return p.parse_args()


async def main():
    args = parse_args()

    if not args.token_a or not args.token_b:
        print(f"\n{Fore.RED}Need two demo API tokens.{Style.RESET_ALL}")
        print("Set DERIV_TOKEN_A and DERIV_TOKEN_B in .env\n")
        sys.exit(1)

    print(f"\n{Fore.CYAN}{'='*65}")
    print("  DERIV DIGIT MATCH/DIFFER — MULTI-AGENT SYSTEM")
    print(f"{'='*65}")
    print(f"  Symbol : {args.symbol}")
    print(f"  Agent A: DIFFER Guard  | stake ${args.stake_differ:.2f} | fixed risk")
    print(f"  Agent B: MATCH Hunter  | stake ${args.stake_match:.2f} | martingale")
    print(f"  Mode   : {'LIVE DEMO TRADES' if args.live else 'Signal-only (no trades placed)'}")
    print(f"{'='*65}{Style.RESET_ALL}\n")

    # Connect both accounts
    conn_a = DerivConn(args.token_a, "A")
    conn_b = DerivConn(args.token_b, "B")

    print("Connecting Account A (DIFFER Guard)...")
    auth_a = await conn_a.connect()
    print(f"  {Fore.GREEN}A: {auth_a.get('loginid')} | ${conn_a.balance:.2f}{Style.RESET_ALL}")

    print("Connecting Account B (MATCH Hunter)...")
    auth_b = await conn_b.connect()
    print(f"  {Fore.GREEN}B: {auth_b.get('loginid')} | ${conn_b.balance:.2f}{Style.RESET_ALL}\n")

    # Build agents
    differ = DifferGuard(conn_a, args.symbol, args.stake_differ, args.max_trades)
    hunter = MatchHunter(conn_b, args.symbol, args.stake_match,  args.max_trades)

    # Watcher subscribes to ticks and drives both agents
    # (conn_a used for the single tick subscription — only one needed)
    watcher = DigitWatcher(differ, hunter, conn_a, args.symbol)

    if not args.live:
        print(f"{Fore.YELLOW}Signal-only mode — no trades placed. Add --live to trade.{Style.RESET_ALL}\n")
        # In signal-only mode, override place_digit to be a no-op
        async def _noop(*a, **kw): return {"profit": 0, "won": False}
        conn_a.place_digit = _noop
        conn_b.place_digit = _noop

    try:
        await watcher.run()
    except KeyboardInterrupt:
        pass
    finally:
        print(f"\n{Fore.YELLOW}Shutting down...{Style.RESET_ALL}")
        await conn_a.disconnect()
        await conn_b.disconnect()

        print(f"\n{Fore.CYAN}── Final Summary {'─'*45}{Style.RESET_ALL}")
        for agent, name in [(differ, "DIFFER Guard"), (hunter, "MATCH Hunter")]:
            r = agent.risk.summary()
            print(f"  {name}: trades={r['total_trades']} WR={r['win_rate']} PnL=${r['pnl']}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Stopped.{Style.RESET_ALL}")
