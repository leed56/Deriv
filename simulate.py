"""
Offline simulation using synthetic price data modelled on each volatility index.
Produces the same output as the live backtest — no network required.
"""

import numpy as np
import random
from colorama import Fore, Style, init

from config import SYMBOLS, STRATEGY_PARAMS
from strategies import STRATEGIES
from risk_manager import RiskManager
from performance_tracker import PerformanceTracker

init(autoreset=True)
random.seed(42)
np.random.seed(42)

TICK_COUNT    = 5000
PAYOUT_RATIO  = 0.87   # typical Deriv Rise/Fall payout
STAKE         = 1.0


def generate_prices(symbol: str, n: int = TICK_COUNT) -> list[float]:
    """
    Simulate realistic tick prices for each volatility index.
    Vol is scaled to match the index number (V10 = 10% annualised ≈ 0.003 per tick, etc.)
    """
    vol_map = {"R_10": 0.0003, "R_25": 0.0008, "R_50": 0.0015, "R_100": 0.003}
    drift   = 0.000001      # tiny upward drift
    vol     = vol_map.get(symbol, 0.001)
    start   = {"R_10": 10000.0, "R_25": 2500.0, "R_50": 800.0, "R_100": 300.0}.get(symbol, 1000.0)

    prices = [start]
    for _ in range(n - 1):
        chg = drift + vol * np.random.randn()
        prices.append(max(prices[-1] * (1 + chg), 0.01))
    return prices


def run_simulation():
    tracker = PerformanceTracker()

    print(f"\n{Fore.CYAN}{'='*70}")
    print(f"  DERIV SYNTHETIC PAIRS — OFFLINE STRATEGY SIMULATION")
    print(f"  ({TICK_COUNT} synthetic ticks per symbol, payout={PAYOUT_RATIO*100:.0f}%)")
    print(f"{'='*70}{Style.RESET_ALL}\n")

    for symbol, info in SYMBOLS.items():
        print(f"{Fore.YELLOW}  [{symbol}] {info['name']}{Style.RESET_ALL}")
        prices = generate_prices(symbol, TICK_COUNT)

        for strat_name, strat_fn in STRATEGIES.items():
            params  = STRATEGY_PARAMS.get(strat_name, {})
            risk    = RiskManager(starting_balance=1000.0, base_stake=STAKE, mode="fixed")
            signals = 0

            for i in range(60, len(prices) - 10):
                window = prices[:i]
                signal = strat_fn(window, symbol, params)
                if signal is None:
                    continue

                signals += 1
                duration = signal.get("duration", 5)
                end_idx  = min(i + duration, len(prices) - 1)
                entry    = prices[i]
                exit_p   = prices[end_idx]
                ct       = signal["contract_type"]

                # Determine win
                if ct == "CALL":
                    win = exit_p > entry
                elif ct == "PUT":
                    win = exit_p < entry
                elif ct == "DIGITOVER":
                    last_d = int(str(round(exit_p, 2)).replace(".", "")[-1])
                    win    = last_d > int(signal.get("barrier", "7"))
                elif ct == "DIGITUNDER":
                    last_d = int(str(round(exit_p, 2)).replace(".", "")[-1])
                    win    = last_d < int(signal.get("barrier", "2"))
                else:
                    win = exit_p > entry

                if win:
                    profit = STAKE * PAYOUT_RATIO
                    risk.record_win(profit)
                    tracker.record(symbol, strat_name, {
                        "outcome": "win", "profit": profit, "stake": STAKE, "contract_type": ct
                    })
                else:
                    risk.record_loss(STAKE)
                    tracker.record(symbol, strat_name, {
                        "outcome": "loss", "profit": 0, "stake": STAKE, "contract_type": ct
                    })

            s = risk.summary()
            col = Fore.GREEN if s["pnl"] > 0 else Fore.RED
            print(
                f"    {strat_name:<22} signals={signals:>4}  "
                f"W/L={s['wins']}/{s['losses']}  "
                f"WR={s['win_rate']}  "
                f"PnL={col}{s['pnl']:+.2f}{Style.RESET_ALL}"
            )
        print()

    tracker.print_report()


if __name__ == "__main__":
    run_simulation()
