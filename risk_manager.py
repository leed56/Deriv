"""
Risk management layer — Martingale, Anti-Martingale, D'Alembert, Fixed.
"""

from config import MAX_DAILY_LOSS_PCT, MAX_CONSECUTIVE_LOSS, TAKE_PROFIT_PCT


class RiskManager:
    def __init__(
        self,
        starting_balance: float,
        base_stake: float = 1.0,
        mode: str = "fixed",           # fixed | martingale | anti_martingale | dalembert
        martingale_multiplier: float = 2.0,
        max_stake: float = 50.0,
    ):
        self.starting_balance    = starting_balance
        self.balance             = starting_balance
        self.base_stake          = base_stake
        self.current_stake       = base_stake
        self.mode                = mode
        self.multiplier          = martingale_multiplier
        self.max_stake           = max_stake
        self.consecutive_losses  = 0
        self.consecutive_wins    = 0
        self.daily_pnl           = 0.0
        self.total_trades        = 0
        self.wins                = 0
        self.losses              = 0

    # ------------------------------------------------------------------
    def next_stake(self) -> float:
        return min(self.current_stake, self.max_stake)

    def record_win(self, profit: float):
        self.balance            += profit
        self.daily_pnl          += profit
        self.total_trades       += 1
        self.wins               += 1
        self.consecutive_losses  = 0
        self.consecutive_wins   += 1
        self._adjust_stake(won=True)

    def record_loss(self, stake: float):
        self.balance            -= stake
        self.daily_pnl          -= stake
        self.total_trades       += 1
        self.losses             += 1
        self.consecutive_wins    = 0
        self.consecutive_losses += 1
        self._adjust_stake(won=False)

    def _adjust_stake(self, won: bool):
        if self.mode == "fixed":
            self.current_stake = self.base_stake
        elif self.mode == "martingale":
            # Double on loss, reset on win
            self.current_stake = self.base_stake if won else min(
                self.current_stake * self.multiplier, self.max_stake
            )
        elif self.mode == "anti_martingale":
            # Double on win, reset on loss
            self.current_stake = (
                min(self.current_stake * self.multiplier, self.max_stake) if won
                else self.base_stake
            )
        elif self.mode == "dalembert":
            # Increase by 1 unit on loss, decrease by 1 unit on win
            if won:
                self.current_stake = max(self.base_stake, self.current_stake - self.base_stake)
            else:
                self.current_stake = min(self.current_stake + self.base_stake, self.max_stake)

    # ------------------------------------------------------------------
    def should_stop(self) -> tuple[bool, str]:
        """Returns (should_stop, reason)."""
        drawdown = (self.starting_balance - self.balance) / self.starting_balance
        if drawdown >= MAX_DAILY_LOSS_PCT:
            return True, f"Daily loss limit reached ({drawdown:.1%})"
        if self.consecutive_losses >= MAX_CONSECUTIVE_LOSS:
            return True, f"{self.consecutive_losses} consecutive losses"
        gain = (self.balance - self.starting_balance) / self.starting_balance
        if gain >= TAKE_PROFIT_PCT:
            return True, f"Take-profit reached ({gain:.1%})"
        return False, ""

    # ------------------------------------------------------------------
    @property
    def win_rate(self) -> float:
        return self.wins / self.total_trades if self.total_trades else 0.0

    @property
    def pnl(self) -> float:
        return self.balance - self.starting_balance

    def summary(self) -> dict:
        return {
            "mode":               self.mode,
            "starting_balance":   self.starting_balance,
            "current_balance":    round(self.balance, 2),
            "pnl":                round(self.pnl, 2),
            "pnl_pct":            f"{self.pnl / self.starting_balance:.1%}",
            "total_trades":       self.total_trades,
            "wins":               self.wins,
            "losses":             self.losses,
            "win_rate":           f"{self.win_rate:.1%}",
            "consecutive_losses": self.consecutive_losses,
        }
