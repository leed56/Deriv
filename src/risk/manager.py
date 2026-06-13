from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone


@dataclass
class DailyPnLTracker:
    target_usd: float
    max_daily_loss_pct: float
    max_drawdown_pct: float
    max_lifetime_drawdown_pct: float
    max_consecutive_loss_days: int
    cooldown_hours: int
    anchor_balance: float
    peak_balance: float
    lifetime_peak_balance: float
    lifetime_anchor_balance: float
    realized_pnl_usd: float = 0.0
    trades_today: int = 0
    wins_today: int = 0
    losses_today: int = 0
    trading_day: date = field(default_factory=lambda: datetime.now(timezone.utc).date())
    halted: bool = False
    halt_reason: str = ""
    profit_day_locked: bool = False
    consecutive_loss_days: int = 0
    stake_multiplier: float = 1.0
    cooldown_until: datetime | None = None

    def sync_day(self, balance: float, today: date | None = None) -> bool:
        today = today or datetime.now(timezone.utc).date()
        if today == self.trading_day:
            self.peak_balance = max(self.peak_balance, balance)
            self.lifetime_peak_balance = max(self.lifetime_peak_balance, balance)
            return False

        if self.realized_pnl_usd < 0:
            self.consecutive_loss_days += 1
        elif self.realized_pnl_usd > 0:
            self.consecutive_loss_days = 0
            # Recovery: nudge stake back up after a green day
            self.stake_multiplier = min(1.0, self.stake_multiplier + 0.15)

        self.trading_day = today
        self.anchor_balance = balance
        self.peak_balance = balance
        self.realized_pnl_usd = 0.0
        self.trades_today = 0
        self.wins_today = 0
        self.losses_today = 0
        self.halted = False
        self.halt_reason = ""
        self.profit_day_locked = False

        self._apply_streak_cooldown()
        self._check_lifetime_soft(balance)
        return True

    @property
    def total_pnl_usd(self) -> float:
        return self.realized_pnl_usd

    @property
    def in_cooldown(self) -> bool:
        if self.cooldown_until is None:
            return False
        if datetime.now(timezone.utc) >= self.cooldown_until:
            self.cooldown_until = None
            return False
        return True

    @property
    def is_blocked(self) -> bool:
        if self.profit_day_locked:
            return True
        if self.in_cooldown:
            return True
        return self.halted

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
        if pnl_usd >= 0:
            self.wins_today += 1
            self.stake_multiplier = min(1.0, self.stake_multiplier + 0.08)
        else:
            self.losses_today += 1
            self.stake_multiplier = max(0.4, self.stake_multiplier * 0.7)
        self._check(balance)

    def check_equity(self, balance: float) -> None:
        self.bind_balance(balance)
        if self.profit_day_locked:
            return
        if self.in_cooldown:
            return
        if not self.halted:
            self._check(balance)

    def _check(self, balance: float) -> None:
        # PRIMARY GOAL: lock in daily profit
        if self.realized_pnl_usd >= self.target_usd:
            self.profit_day_locked = True
            self.halted = True
            self.halt_reason = f"profit_locked_{self.target_usd:.2f}"
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

        self._check_lifetime_soft(balance)

    def _check_lifetime_soft(self, balance: float) -> None:
        """Reduce size + short cooldown instead of permanent shutdown."""
        if self.lifetime_peak_balance > 0:
            lifetime_dd = (self.lifetime_peak_balance - balance) / self.lifetime_peak_balance
            if lifetime_dd >= self.max_lifetime_drawdown_pct:
                self.stake_multiplier = 0.4
                self._set_cooldown(hours=self.cooldown_hours * 2)
                return

        if self.consecutive_loss_days >= self.max_consecutive_loss_days:
            self._apply_streak_cooldown()

    def _apply_streak_cooldown(self) -> None:
        if self.consecutive_loss_days >= self.max_consecutive_loss_days:
            self.stake_multiplier = min(self.stake_multiplier, 0.5)
            self._set_cooldown(hours=self.cooldown_hours)

    def _set_cooldown(self, hours: int) -> None:
        until = datetime.now(timezone.utc) + timedelta(hours=hours)
        if self.cooldown_until is None or until > self.cooldown_until:
            self.cooldown_until = until

    def _halt_daily(self, reason: str) -> None:
        self.halted = True
        self.halt_reason = reason

    def can_trade(self, open_contracts: int, max_open: int) -> bool:
        return not self.is_blocked and open_contracts < max_open


@dataclass
class StakeSizer:
    max_stake_usd: float
    min_stake_usd: float
    stake_pct: float

    def stake(self, balance: float, confidence: float, multiplier: float = 1.0) -> float:
        raw = balance * self.stake_pct * min(1.0, confidence) * multiplier
        return round(max(self.min_stake_usd, min(self.max_stake_usd, raw)), 2)
