"""Demo portfolio and position tracking."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.strategies.base import Side


@dataclass
class Position:
    symbol: str
    side: Side
    quantity: float
    entry_price: float
    entry_time: str
    strategy: str
    pair_id: str = ""

    def unrealized_pnl(self, current_price: float) -> float:
        if self.side == Side.LONG:
            return (current_price - self.entry_price) * self.quantity
        return (self.entry_price - current_price) * self.quantity


@dataclass
class SpreadPosition:
    """Two-legged synthetic spread position."""

    pair_id: str
    leg_a: str
    leg_b: str
    side: Side  # LONG spread = long A, short B
    qty_a: float
    qty_b: float
    entry_a: float
    entry_b: float
    hedge_ratio: float
    strategy: str
    entry_time: str
    stop_z: float = 3.5
    entry_z: float = 0.0

    def unrealized_pnl(self, price_a: float, price_b: float) -> float:
        return (price_a - self.entry_a) * self.qty_a + (price_b - self.entry_b) * self.qty_b

    @property
    def id(self) -> str:
        return self.pair_id


@dataclass
class Portfolio:
    cash_usd: float
    positions: list[SpreadPosition] = field(default_factory=list)
    realized_pnl: float = 0.0
    trade_count: int = 0

    @property
    def equity(self) -> float:
        return self.cash_usd + self.realized_pnl

    def to_dict(self) -> dict[str, Any]:
        return {
            "cash_usd": self.cash_usd,
            "realized_pnl": self.realized_pnl,
            "trade_count": self.trade_count,
            "positions": [asdict(p) for p in self.positions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Portfolio:
        positions = []
        for p in data.get("positions", []):
            p["side"] = Side(p["side"])
            positions.append(SpreadPosition(**p))
        return cls(
            cash_usd=float(data.get("cash_usd", 1000)),
            positions=positions,
            realized_pnl=float(data.get("realized_pnl", 0)),
            trade_count=int(data.get("trade_count", 0)),
        )


class PortfolioManager:
    def __init__(self, path: Path, initial_capital: float) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.portfolio = self._load_or_create(initial_capital)

    def _load_or_create(self, initial: float) -> Portfolio:
        if self.path.exists():
            with self.path.open() as f:
                return Portfolio.from_dict(json.load(f))
        return Portfolio(cash_usd=initial)

    def save(self) -> None:
        with self.path.open("w") as f:
            json.dump(self.portfolio.to_dict(), f, indent=2, default=str)

    def has_open_position(self, pair_id: str) -> bool:
        return any(p.pair_id == pair_id for p in self.portfolio.positions)

    def open_spread(
        self,
        pair_id: str,
        leg_a: str,
        leg_b: str,
        side: Side,
        notional_usd: float,
        price_a: float,
        price_b: float,
        hedge_ratio: float,
        strategy: str,
        entry_z: float,
        stop_z: float,
    ) -> SpreadPosition | None:
        if self.has_open_position(pair_id):
            return None

        half = notional_usd / 2
        qty_a = half / price_a
        qty_b = (half / price_b) * hedge_ratio

        pos = SpreadPosition(
            pair_id=pair_id,
            leg_a=leg_a,
            leg_b=leg_b,
            side=side,
            qty_a=qty_a if side == Side.LONG else -qty_a,
            qty_b=-qty_b if side == Side.LONG else qty_b,
            entry_a=price_a,
            entry_b=price_b,
            hedge_ratio=hedge_ratio,
            strategy=strategy,
            entry_time=datetime.now(timezone.utc).isoformat(),
            entry_z=entry_z,
            stop_z=stop_z,
        )
        self.portfolio.positions.append(pos)
        self.portfolio.trade_count += 1
        self.save()
        return pos

    def close_spread(self, pair_id: str, price_a: float, price_b: float) -> float:
        for i, pos in enumerate(self.portfolio.positions):
            if pos.pair_id == pair_id:
                pnl = pos.unrealized_pnl(price_a, price_b)
                self.portfolio.realized_pnl += pnl
                self.portfolio.cash_usd += pnl
                del self.portfolio.positions[i]
                self.save()
                return pnl
        return 0.0

    def mark_to_market(self, prices: dict[str, float]) -> float:
        unrealized = 0.0
        for pos in self.portfolio.positions:
            pa = prices.get(pos.leg_a, pos.entry_a)
            pb = prices.get(pos.leg_b, pos.entry_b)
            unrealized += pos.unrealized_pnl(pa, pb)
        return self.portfolio.equity + unrealized
