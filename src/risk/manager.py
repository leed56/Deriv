from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from src.config import RiskConfig


@dataclass
class DailyPnLTracker:
    target_usd: float
    loss_limit_usd: float
    realized_pnl_usd: float = 0.0
    unrealized_pnl_usd: float = 0.0
    trades_today: int = 0
    trading_day: date = field(default_factory=lambda: datetime.now(timezone.utc).date())
    halted: bool = False
    halt_reason: str = ""

    def reset_if_new_day(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self.trading_day:
            self.trading_day = today
            self.realized_pnl_usd = 0.0
            self.unrealized_pnl_usd = 0.0
            self.trades_today = 0
            self.halted = False
            self.halt_reason = ""

    @property
    def total_pnl_usd(self) -> float:
        return self.realized_pnl_usd + self.unrealized_pnl_usd

    @property
    def target_reached(self) -> bool:
        return self.total_pnl_usd >= self.target_usd

    @property
    def loss_exceeded(self) -> bool:
        return self.total_pnl_usd <= -self.loss_limit_usd

    def record_trade_pnl(self, pnl_usd: float) -> None:
        self.reset_if_new_day()
        self.realized_pnl_usd += pnl_usd
        self.trades_today += 1
        self._check_halt()

    def update_unrealized(self, pnl_usd: float) -> None:
        self.reset_if_new_day()
        self.unrealized_pnl_usd = pnl_usd
        self._check_halt()

    def _check_halt(self) -> None:
        if self.target_reached:
            self.halted = True
            self.halt_reason = f"daily_target_reached_{self.target_usd:.2f}_usd"
        elif self.loss_exceeded:
            self.halted = True
            self.halt_reason = f"daily_loss_limit_{self.loss_limit_usd:.2f}_usd"

    def can_open_trade(self, max_open: int, open_count: int) -> bool:
        self.reset_if_new_day()
        if self.halted:
            return False
        return open_count < max_open


@dataclass
class PositionSizer:
    max_position_usd: float
    stop_loss_pct: float

    def size_usd(self, confidence: float, account_equity_usd: float) -> float:
        """Kelly-inspired fractional sizing capped by risk limits."""
        base = min(self.max_position_usd, account_equity_usd * 0.15)
        sized = base * min(1.0, confidence)
        return max(50.0, sized)  # minimum notional for meaningful testnet fills

    def btc_contracts(self, notional_usd: float, btc_price: float) -> float:
        """Deribit BTC-PERPETUAL: amount in USD."""
        return round(max(10.0, notional_usd), 0)

    def eth_contracts(self, notional_usd: float, eth_price: float, beta: float) -> float:
        """ETH-PERPETUAL: amount in USD, hedged by beta."""
        hedged = notional_usd * beta * (eth_price / max(btc_price, 1))
        return round(max(10.0, hedged), 0)


def build_risk(config: RiskConfig) -> tuple[DailyPnLTracker, PositionSizer]:
    tracker = DailyPnLTracker(
        target_usd=config.daily_profit_target_usd,
        loss_limit_usd=config.daily_loss_limit_usd,
    )
    sizer = PositionSizer(
        max_position_usd=config.max_position_usd,
        stop_loss_pct=config.stop_loss_pct,
    )
    return tracker, sizer
