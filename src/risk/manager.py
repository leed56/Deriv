from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone


@dataclass
class DailyPnLTracker:
    target_usd: float
    max_daily_loss_pct: float
    max_drawdown_pct: float
    session_start_balance: float
    realized_pnl_usd: float = 0.0
    trades_today: int = 0
    trading_day: date = field(default_factory=lambda: datetime.now(timezone.utc).date())
    halted: bool = False
    halt_reason: str = ""

    def reset_if_new_day(self, balance: float) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self.trading_day:
            self.trading_day = today
            self.realized_pnl_usd = 0.0
            self.trades_today = 0
            self.halted = False
            self.halt_reason = ""
            self.session_start_balance = balance

    @property
    def total_pnl_usd(self) -> float:
        return self.realized_pnl_usd

    def record(self, pnl_usd: float, balance: float) -> None:
        self.reset_if_new_day(balance)
        self.realized_pnl_usd += pnl_usd
        self.trades_today += 1
        self._check(balance)

    def _check(self, balance: float) -> None:
        if self.realized_pnl_usd >= self.target_usd:
            self.halted = True
            self.halt_reason = f"target_reached_{self.target_usd:.2f}"
            return
        loss_limit = self.session_start_balance * self.max_daily_loss_pct
        if self.realized_pnl_usd <= -loss_limit:
            self.halted = True
            self.halt_reason = f"daily_loss_{self.max_daily_loss_pct:.0%}"
            return
        dd_limit = self.session_start_balance * self.max_drawdown_pct
        if self.realized_pnl_usd <= -dd_limit:
            self.halted = True
            self.halt_reason = f"max_drawdown_{self.max_drawdown_pct:.0%}"

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
