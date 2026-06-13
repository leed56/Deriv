from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OrderBookSnapshot:
    bids: list[list[float]] = field(default_factory=list)
    asks: list[list[float]] = field(default_factory=list)
    timestamp: float = 0.0

    @classmethod
    def from_deribit(cls, data: dict) -> "OrderBookSnapshot":
        def _levels(raw: list) -> list[list[float]]:
            return [[float(p), float(a)] for p, a in raw]

        return cls(
            bids=_levels(data.get("bids", [])),
            asks=_levels(data.get("asks", [])),
            timestamp=float(data.get("timestamp", 0)) / 1000.0,
        )


def order_book_imbalance(book: OrderBookSnapshot, levels: int = 5) -> float:
    """
    Microstructure signal: volume imbalance in top-of-book.
    Range [-1, 1]. Positive = bid pressure.
    """
    bid_vol = sum(level[1] for level in book.bids[:levels])
    ask_vol = sum(level[1] for level in book.asks[:levels])
    total = bid_vol + ask_vol
    if total <= 0:
        return 0.0
    return (bid_vol - ask_vol) / total


def mid_price(book: OrderBookSnapshot) -> float | None:
    if not book.bids or not book.asks:
        return None
    return (book.bids[0][0] + book.asks[0][0]) / 2.0


def spread_bps(book: OrderBookSnapshot) -> float:
    mid = mid_price(book)
    if mid is None or mid <= 0:
        return 999.0
    return (book.asks[0][0] - book.bids[0][0]) / mid * 10000


@dataclass
class MicrostructureState:
    obi_a: float = 0.0
    obi_b: float = 0.0
    spread_a_bps: float = 0.0
    spread_b_bps: float = 0.0

    def composite_pressure(self) -> float:
        """Synthetic pair pressure: BTC bid pressure minus ETH bid pressure."""
        return self.obi_a - self.obi_b

    def liquidity_ok(self, max_spread_bps: float = 8.0) -> bool:
        return self.spread_a_bps < max_spread_bps and self.spread_b_bps < max_spread_bps
