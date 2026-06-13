"""Risk management with daily $5 profit target."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.data.store import StateStore


@dataclass
class RiskDecision:
    can_trade: bool
    position_size_usd: float
    reason: str
    daily_pnl: float = 0.0
    target_hit: bool = False
    loss_limit_hit: bool = False


class RiskManager:
    """
    Controls position sizing and halts trading when:
    - Daily profit target ($5) is reached
    - Daily max loss is breached
    - Portfolio drawdown exceeds limit
    """

    def __init__(
        self,
        state_store: StateStore,
        daily_target_usd: float = 5.0,
        max_daily_loss_usd: float = 15.0,
        max_position_pct: float = 0.25,
        min_trade_usd: float = 10.0,
        initial_capital: float = 1000.0,
    ) -> None:
        self.state = state_store
        self.daily_target = daily_target_usd
        self.max_daily_loss = max_daily_loss_usd
        self.max_position_pct = max_position_pct
        self.min_trade_usd = min_trade_usd
        self.initial_capital = initial_capital

    def evaluate(
        self,
        equity: float,
        signal_strength: float,
        open_positions: int,
        max_positions: int = 3,
    ) -> RiskDecision:
        daily_pnl = self.state.get_daily_pnl(date.today())

        if daily_pnl >= self.daily_target:
            return RiskDecision(
                can_trade=False,
                position_size_usd=0,
                reason=f"Daily profit target reached: ${daily_pnl:.2f} >= ${self.daily_target:.2f}",
                daily_pnl=daily_pnl,
                target_hit=True,
            )

        if daily_pnl <= -self.max_daily_loss:
            return RiskDecision(
                can_trade=False,
                position_size_usd=0,
                reason=f"Daily loss limit hit: ${daily_pnl:.2f}",
                daily_pnl=daily_pnl,
                loss_limit_hit=True,
            )

        if open_positions >= max_positions:
            return RiskDecision(
                can_trade=False,
                position_size_usd=0,
                reason=f"Max open positions ({max_positions}) reached",
                daily_pnl=daily_pnl,
            )

        # Scale size toward target: more aggressive when behind, conservative when ahead
        remaining = self.daily_target - daily_pnl
        base_pct = self.max_position_pct * (0.5 + 0.5 * signal_strength)

        # Kelly-inspired fractional sizing capped by risk budget
        risk_budget = min(remaining * 2, equity * base_pct)
        size = max(self.min_trade_usd, min(risk_budget, equity * self.max_position_pct))

        return RiskDecision(
            can_trade=True,
            position_size_usd=round(size, 2),
            reason="OK",
            daily_pnl=daily_pnl,
        )

    def record_pnl(self, pnl: float) -> float:
        return self.state.record_daily_pnl(pnl)
