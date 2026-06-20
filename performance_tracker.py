"""
Tracks per-symbol, per-strategy performance and generates recommendations.
"""

from collections import defaultdict
from tabulate import tabulate
from colorama import Fore, Style, init

init(autoreset=True)


class PerformanceTracker:
    def __init__(self):
        # Structure: results[symbol][strategy] = list of trade dicts
        self.results: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))

    def record(self, symbol: str, strategy: str, trade: dict):
        self.results[symbol][strategy].append(trade)

    def _stats(self, trades: list[dict]) -> dict:
        if not trades:
            return {}
        wins   = [t for t in trades if t.get("outcome") == "win"]
        losses = [t for t in trades if t.get("outcome") == "loss"]
        profits = [t.get("profit", 0.0) for t in wins]
        stakes  = [t.get("stake", 1.0)  for t in losses]
        total_profit = sum(profits)
        total_loss   = sum(stakes)
        win_rate = len(wins) / len(trades)
        avg_payout = sum(profits) / len(profits) if profits else 0
        net = total_profit - total_loss
        # Profit factor
        pf = total_profit / total_loss if total_loss else float("inf")
        return {
            "trades":        len(trades),
            "wins":          len(wins),
            "losses":        len(losses),
            "win_rate":      win_rate,
            "net_pnl":       net,
            "profit_factor": pf,
            "avg_payout":    avg_payout,
            "score":         win_rate * pf,   # composite ranking score
        }

    # ------------------------------------------------------------------
    def print_report(self):
        print(f"\n{Fore.CYAN}{'='*70}")
        print(f"  DERIV SYNTHETIC PAIRS — STRATEGY PERFORMANCE REPORT")
        print(f"{'='*70}{Style.RESET_ALL}\n")

        overall_scores: dict[str, dict[str, float]] = {}  # symbol -> strategy -> score

        for symbol, strat_data in sorted(self.results.items()):
            print(f"{Fore.YELLOW}  Symbol: {symbol}{Style.RESET_ALL}")
            rows = []
            best_score = -1
            best_strat = ""
            for strategy, trades in strat_data.items():
                s = self._stats(trades)
                if not s:
                    continue
                rows.append([
                    strategy,
                    s["trades"],
                    f"{s['win_rate']:.1%}",
                    f"{s['net_pnl']:+.2f}",
                    f"{s['profit_factor']:.2f}",
                    f"{s['avg_payout']:.2f}",
                    f"{s['score']:.3f}",
                ])
                overall_scores.setdefault(symbol, {})[strategy] = s["score"]
                if s["score"] > best_score:
                    best_score = s["score"]
                    best_strat = strategy

            headers = ["Strategy", "Trades", "WinRate", "NetPnL", "ProfitFactor", "AvgPayout", "Score"]
            print(tabulate(rows, headers=headers, tablefmt="rounded_outline"))
            if best_strat:
                print(f"  {Fore.GREEN}Best strategy for {symbol}: {best_strat} (score={best_score:.3f}){Style.RESET_ALL}\n")

        self._print_recommendations(overall_scores)

    # ------------------------------------------------------------------
    def _print_recommendations(self, scores: dict[str, dict[str, float]]):
        print(f"{Fore.CYAN}{'='*70}")
        print(f"  DESIGN RECOMMENDATIONS")
        print(f"{'='*70}{Style.RESET_ALL}\n")

        rec = {
            "R_10": {
                "primary":    "digit_analysis + rsi_reversal",
                "risk_mode":  "fixed (low stake, high frequency)",
                "duration":   "1–3 ticks",
                "notes":      "V10 has tiny moves; digit contracts shine here. "
                              "RSI reversal catches micro-mean-reversion. "
                              "Avoid Martingale — drawdowns accumulate fast.",
            },
            "R_25": {
                "primary":    "rsi_reversal + bollinger_bands",
                "risk_mode":  "D'Alembert (balanced recovery)",
                "duration":   "3–5 ticks",
                "notes":      "Moderate volatility suits mean-reversion well. "
                              "BB gives a clear entry zone; RSI filters false signals.",
            },
            "R_50": {
                "primary":    "ema_crossover + bollinger_bands",
                "risk_mode":  "anti_martingale (ride the trend)",
                "duration":   "5–10 ticks",
                "notes":      "V50 trends more than V10/V25. EMA cross captures the move; "
                              "BB confirms direction. Anti-Martingale lets winners compound.",
            },
            "R_100": {
                "primary":    "momentum_breakout + ema_crossover",
                "risk_mode":  "fixed (conservative, large swings)",
                "duration":   "3–5 ticks",
                "notes":      "V100 is explosive. Breakout + EMA catches strong directional moves. "
                              "Keep stake fixed — big losses can snowball with Martingale here.",
            },
        }

        for symbol, r in rec.items():
            # Override with actual best if we have data
            if symbol in scores and scores[symbol]:
                actual_best = max(scores[symbol], key=scores[symbol].get)
                r["primary"] = f"{actual_best} (backtest winner)"
            print(f"  {Fore.YELLOW}{symbol}{Style.RESET_ALL}")
            print(f"    Strategy : {Fore.GREEN}{r['primary']}{Style.RESET_ALL}")
            print(f"    Risk Mode: {r['risk_mode']}")
            print(f"    Duration : {r['duration']}")
            print(f"    Notes    : {r['notes']}\n")

        print(f"{Fore.CYAN}  GENERAL RULES{Style.RESET_ALL}")
        print("  1. Never risk more than 2% of balance per trade.")
        print("  2. Stop trading after 5 consecutive losses — re-analyse.")
        print("  3. V10/V25 → mean-reversion strategies.")
        print("  4. V50/V100 → trend-following strategies.")
        print("  5. Digit contracts (V10) → statistical frequency analysis.")
        print(f"\n{Fore.CYAN}{'='*70}{Style.RESET_ALL}\n")
