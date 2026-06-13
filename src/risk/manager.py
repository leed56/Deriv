from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone


@dataclass
class DailyPnLTracker:
    target_usd: float
    max_daily_loss_pct: float
    max_drawdown_pct: float
    anchor_balance: float
    peak_balance: float
    realized_pnl_usd: float = 0.0
    trades_today: int = 0
    trading_day: date = field(default_factory=lambda: datetime.now(timezone.utc).date())
    halted: bool = False
    halt_reason: str = ""

    def sync_day(self, balance: float, today: date | None = None) -> bool:
        """
        Roll to a new UTC day if needed. Returns True if day rolled (fresh daily counters).
        Anchor balance is set once per day and never reset on bot restart.
        """
        today = today or datetime.now(timezone.utc).date()
        if today == self.trading_day:
            self.peak_balance = max(self.peak_balance, balance)
            return False

        self.trading_day = today
        self.anchor_balance = balance
        self.peak_balance = balance
        self.realized_pnl_usd = 0.0
        self.trades_today = 0
        self.halted = False
        self.halt_reason = ""
        return True

    @property
    def total_pnl_usd(self) -> float:
        return self.realized_pnl_usd

    @property
    def drawdown_usd(self) -> float:
        return max(0.0, self.peak_balance - self._current_balance_hint)

    _current_balance_hint: float = field(default=0.0, repr=False)

    def bind_balance(self, balance: float) -> None:
        self._current_balance_hint = balance
        self.peak_balance = max(self.peak_balance, balance)

    def record(self, pnl_usd: float, balance: float) -> None:
        self.sync_day(balance)
        self.bind_balance(balance)
        self.realized_pnl_usd += pnl_usd
        self.trades_today += 1
        self._check(balance)

    def check_equity(self, balance: float) -> None:
        """Re-evaluate halt on startup/restart from persisted state + live balance."""
        self.bind_balance(balance)
        if self.halted:
            return
        self._check(balance)

    def _check(self, balance: float) -> None:
        if self.realized_pnl_usd >= self.target_usd:
            self._halt(f"target_reached_{self.target_usd:.2f}")
            return

        daily_loss_cap = self.anchor_balance * self.max_daily_loss_pct
        if self.realized_pnl_usd <= -daily_loss_cap:
            self._halt(f"daily_loss_{self.max_daily_loss_pct:.0%}")
            return

        # Drawdown from intraday peak — survives restarts because peak is persisted
        if self.peak_balance > 0:
            dd_pct = (self.peak_balance - balance) / self.peak_balance
            if dd_pct >= self.max_drawdown_pct:
                self._halt(f"max_drawdown_{self.max_drawdown_pct:.0%}")

    def _halt(self, reason: str) -> None:
        self.halted = True
        self.halt_reason = reason

    def can_trade(self, open_contracts: int, max_open: int) -> bool:
        return not self.halted and open_contracts < max_open


@dataclass
class StakeSizer:
    max_stake_usd: float
    min_stake_usd: float
    stake_pct: float

    def stake(self, balance: float, confidence: float) -> float:
        raw = balance * self.stake_pct * min(1.0, confidence)
        return round(max(self.min_stake_usd, min(self.max_stake_usd, raw)), 2)
