from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class LoopPhase(Enum):
    SCAN = "scan"
    INSERT = "insert"
    IN_TRADE = "in_trade"
    ACCUMULATE = "accumulate"
    LOCKED = "locked"
    COOLDOWN = "cooldown"


@dataclass
class ProfitLoopConfig:
    enabled: bool = True
    reinvest_profit_pct: float = 0.5
    min_seconds_between_trades: float = 4.0
    compound_wins: bool = True


@dataclass
class ProfitLoop:
    """
    Explicit daily profit insertion loop:

      SCAN → INSERT trade → IN_TRADE → ACCUMULATE P&L → re-INSERT until +$target

    Wins are partially reinvested (inserted) into the next stake to reach
  the daily target faster.
    """

    config: ProfitLoopConfig
    target_usd: float
    phase: LoopPhase = LoopPhase.SCAN
    loop_count: int = 0
    inserted_profit_usd: float = 0.0
    last_trade_at: datetime | None = None

    def on_trade_opened(self) -> None:
        self.phase = LoopPhase.IN_TRADE
        self.last_trade_at = datetime.now(timezone.utc)

    def on_trade_settled(self, pnl: float, daily_pnl: float, profit_locked: bool) -> None:
        self.loop_count += 1
        if pnl > 0 and self.config.compound_wins:
            self.inserted_profit_usd += pnl * self.config.reinvest_profit_pct
        self.phase = LoopPhase.LOCKED if profit_locked else LoopPhase.ACCUMULATE

    def ready_to_insert(self, blocked: bool, has_open: bool) -> bool:
        if blocked or has_open:
            return False
        if self.phase not in (LoopPhase.SCAN, LoopPhase.ACCUMULATE):
            return False
        if self.last_trade_at is not None:
            elapsed = (datetime.now(timezone.utc) - self.last_trade_at).total_seconds()
            if elapsed < self.config.min_seconds_between_trades:
                return False
        return True

    def next_stake(self, base_stake: float, max_stake: float, daily_pnl: float) -> float:
        """Insert accumulated profit into the next contract stake."""
        insertion = max(0.0, daily_pnl) * self.config.reinvest_profit_pct
        insertion += self.inserted_profit_usd * 0.25
        stake = base_stake + insertion
        return round(min(max_stake, max(base_stake, stake)), 2)

    def progress_pct(self, daily_pnl: float) -> float:
        if self.target_usd <= 0:
            return 0.0
        return min(100.0, max(0.0, daily_pnl / self.target_usd * 100.0))

    def set_phase(self, phase: LoopPhase) -> None:
        self.phase = phase

    def snapshot(self, daily_pnl: float) -> dict:
        return {
            "phase": self.phase.value,
            "loop_count": self.loop_count,
            "progress_pct": round(self.progress_pct(daily_pnl), 1),
            "inserted_profit": round(self.inserted_profit_usd, 4),
        }
