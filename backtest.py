"""
Backtest mode — fetches historical ticks and simulates all strategies.
No live trades placed.
"""

import asyncio
import logging
from colorama import Fore, Style, init

from config import SYMBOLS, STRATEGY_PARAMS, BACKTEST_TICK_COUNT
from deriv_api import DerivAPI
from strategies import STRATEGIES
from risk_manager import RiskManager
from performance_tracker import PerformanceTracker

init(autoreset=True)
logger = logging.getLogger("backtest")


async def run_backtest(token: str, stake: float = 1.0, payout_ratio: float = 0.85):
    """
    payout_ratio: Deriv typically pays ~85–95% for Rise/Fall contracts.
                  Set accurately for realistic results.
    """
    api     = DerivAPI(token)
    tracker = PerformanceTracker()

    await api.connect()
    account = await api.authorize()
    print(f"\n{Fore.GREEN}Backtest mode | Account: {account.get('loginid')}{Style.RESET_ALL}")
    print(f"Fetching {BACKTEST_TICK_COUNT} ticks per symbol...\n")

    for symbol, info in SYMBOLS.items():
        print(f"{Fore.YELLOW}  [{symbol}] {info['name']}{Style.RESET_ALL}")
        prices = await api.get_ticks_history(symbol, BACKTEST_TICK_COUNT)
        if len(prices) < 100:
            print(f"    Not enough data ({len(prices)} ticks). Skipping.")
            continue
        print(f"    Received {len(prices)} ticks. Running strategies...")

        for strat_name, strat_fn in STRATEGIES.items():
            params = STRATEGY_PARAMS.get(strat_name, {})
            risk   = RiskManager(starting_balance=1000.0, base_stake=stake, mode="fixed")
            signals = 0

            for i in range(50, len(prices) - 1):
                window = prices[:i]
                signal = strat_fn(window, symbol, params)
                if signal is None:
                    continue

                signals += 1
                duration = signal.get("duration", 5)
                # Simulate: check price direction after `duration` ticks
                if i + duration >= len(prices):
                    continue

                entry     = prices[i]
                exit_price = prices[i + duration]
                ct        = signal["contract_type"]

                if ct == "CALL":
                    win = exit_price > entry
                elif ct == "PUT":
                    win = exit_price < entry
                elif ct in ("DIGITOVER", "DIGITUNDER", "DIGITMATCH", "DIGITDIFF"):
                    # Simplified digit simulation
                    last_digit = int(str(round(exit_price, 2)).replace(".", "")[-1])
                    barrier = int(signal.get("barrier", "7"))
                    if ct == "DIGITOVER":
                        win = last_digit > barrier
                    elif ct == "DIGITUNDER":
                        win = last_digit < barrier
                    else:
                        win = last_digit == barrier
                else:
                    win = exit_price > entry

                if win:
                    profit = stake * payout_ratio
                    risk.record_win(profit)
                    tracker.record(symbol, strat_name, {
                        "outcome": "win", "profit": profit, "stake": stake, "contract_type": ct
                    })
                else:
                    risk.record_loss(stake)
                    tracker.record(symbol, strat_name, {
                        "outcome": "loss", "profit": 0, "stake": stake, "contract_type": ct
                    })

            summary = risk.summary()
            color = Fore.GREEN if summary["pnl"] > 0 else Fore.RED
            print(
                f"    {strat_name:<22} signals={signals:>4}  "
                f"W/L={summary['wins']}/{summary['losses']}  "
                f"WR={summary['win_rate']}  "
                f"PnL={color}{summary['pnl']:+.2f}{Style.RESET_ALL}"
            )

    await api.disconnect()
    tracker.print_report()


if __name__ == "__main__":
    import sys
    import os
    from dotenv import load_dotenv
    load_dotenv()

    token = os.getenv("DERIV_API_TOKEN", "")
    if not token and len(sys.argv) > 1:
        token = sys.argv[1]
    if not token:
        print(f"{Fore.RED}Error: No API token provided.{Style.RESET_ALL}")
        print("Usage: python backtest.py <YOUR_DEMO_API_TOKEN>")
        print("   or: set DERIV_API_TOKEN=... in .env file")
        sys.exit(1)

    asyncio.run(run_backtest(token, stake=1.0))
