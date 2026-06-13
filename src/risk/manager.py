from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone


@dataclass
class DailyPnLTracker:
    target_usd: float
    max_daily_loss_pct: float
    max_drawdown_pct: float
    max_lifetime_drawdown_pct: float
    max_consecutive_loss_days: int
    anchor_balance: float
    peak_balance: float
    lifetime_peak_balance: float
    lifetime_anchor_balance: float
    realized_pnl_usd: float = 0.0
    trades_today: int = 0
    trading_day: date = field(default_factory=lambda: datetime.now(timezone.utc).date())
    halted: bool = False
    halt_reason: str = ""
    lifetime_halted: bool = False
    lifetime_halt_reason: str = ""
    consecutive_loss_days: int = 0

    def sync_day(self, balance: float, today: date | None = None) -> bool:
        """
        Roll to a new UTC day. Daily counters reset; lifetime limits never reset.
        Returns True if the calendar day rolled.
        """
        today = today or datetime.now(timezone.utc).date()
        if today == self.trading_day:
            self.peak_balance = max(self.peak_balance, balance)
            self.lifetime_peak_balance = max(self.lifetime_peak_balance, balance)
            return False

        # Close out prior day for streak tracking
        if self.realized_pnl_usd < 0:
            self.consecutive_loss_days += 1
        elif self.realized_pnl_usd > 0:
            self.consecutive_loss_days = 0

        self.trading_day = today
        self.anchor_balance = balance
        self.peak_balance = balance
        self.realized_pnl_usd = 0.0
        self.trades_today = 0

        # Daily halt clears on new day — lifetime halt does not
        if not self.lifetime_halted:
            self.halted = False
            self.halt_reason = ""

        self._check_lifetime(balance)
        return True

    @property
    def total_pnl_usd(self) -> float:
        return self.realized_pnl_usd

    @property
    def is_blocked(self) -> bool:
        return self.halted or self.lifetime_halted

    _current_balance_hint: float = field(default=0.0, repr=False)

    def bind_balance(self, balance: float) -> None:
        self._current_balance_hint = balance
        self.peak_balance = max(self.peak_balance, balance)
        self.lifetime_peak_balance = max(self.lifetime_peak_balance, balance)

    def record(self, pnl_usd: float, balance: float) -> None:
        self.sync_day(balance)
        self.bind_balance(balance)
        self.realized_pnl_usd += pnl_usd
        self.trades_today += 1
        self._check(balance)

    def check_equity(self, balance: float) -> None:
        self.bind_balance(balance)
        if self.lifetime_halted:
            return
        if not self.halted:
            self._check(balance)
        else:
            self._check_lifetime(balance)

    def _check(self, balance: float) -> None:
        if self.lifetime_halted:
            return

        if self.realized_pnl_usd >= self.target_usd:
            self._halt_daily(f"target_reached_{self.target_usd:.2f}")
            return

        daily_loss_cap = self.anchor_balance * self.max_daily_loss_pct
        if self.realized_pnl_usd <= -daily_loss_cap:
            self._halt_daily(f"daily_loss_{self.max_daily_loss_pct:.0%}")
            return

        if self.peak_balance > 0:
            dd_pct = (self.peak_balance - balance) / self.peak_balance
            if dd_pct >= self.max_drawdown_pct:
                self._halt_daily(f"intraday_drawdown_{self.max_drawdown_pct:.0%}")
                return

        self._check_lifetime(balance)

    def _check_lifetime(self, balance: float) -> None:
        if self.lifetime_peak_balance > 0:
            lifetime_dd = (self.lifetime_peak_balance - balance) / self.lifetime_peak_balance
            if lifetime_dd >= self.max_lifetime_drawdown_pct:
                self._halt_lifetime(
                    f"lifetime_drawdown_{self.max_lifetime_drawdown_pct:.0%}"
                )
                return

        if self.consecutive_loss_days >= self.max_consecutive_loss_days:
            self._halt_lifetime(
                f"consecutive_loss_days_{self.consecutive_loss_days}"
            )

    def _halt_daily(self, reason: str) -> None:
        self.halted = True
        self.halt_reason = reason

    def _halt_lifetime(self, reason: str) -> None:
        self.lifetime_halted = True
        self.lifetime_halt_reason = reason
        self.halted = True
        self.halt_reason = reason

    def can_trade(self, open_contracts: int, max_open: int) -> bool:
        return not self.is_blocked and open_contracts < max_open


@dataclass
class StakeSizer:
    max_stake_usd: float
    min_stake_usd: float
    stake_pct: float

    def stake(self, balance: float, confidence: float) -> float:
        raw = balance * self.stake_pct * min(1.0, confidence)
        return round(max(self.min_stake_usd, min(self.max_stake_usd, raw)), 2)
