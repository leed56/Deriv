from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

import structlog

from src.api.client import DeribitClient
from src.config import ExecutionConfig
from src.strategy.engine import SignalSide

log = structlog.get_logger()


class PositionState(Enum):
    FLAT = "flat"
    LONG_SPREAD = "long_spread"
    SHORT_SPREAD = "short_spread"


@dataclass
class OpenPosition:
    state: PositionState
    btc_amount: float
    eth_amount: float
    entry_btc: float
    entry_eth: float
    entry_zscore: float
    opened_at: float = field(default_factory=time.time)
    paper: bool = False
    trade_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])


@dataclass
class PaperLedger:
    """Simulated fills when running without API keys."""

    balance_usd: float = 10000.0
    positions: list[OpenPosition] = field(default_factory=list)

    def open(self, pos: OpenPosition) -> None:
        self.positions.append(pos)

    def close(self, pos: OpenPosition, pnl: float) -> None:
        if pos in self.positions:
            self.positions.remove(pos)
        self.balance_usd += pnl


class OrderManager:
    def __init__(
        self,
        client: DeribitClient,
        execution: ExecutionConfig,
        leg_a: str,
        leg_b: str,
    ) -> None:
        self.client = client
        self.execution = execution
        self.leg_a = leg_a
        self.leg_b = leg_b
        self.position: OpenPosition | None = None
        self.paper = PaperLedger()

    @property
    def is_open(self) -> bool:
        return self.position is not None

    def unrealized_pnl(self, btc_price: float, eth_price: float) -> float:
        if not self.position:
            return 0.0
        p = self.position
        btc_pnl = 0.0
        eth_pnl = 0.0
        if p.state == PositionState.LONG_SPREAD:
            btc_pnl = (btc_price - p.entry_btc) / p.entry_btc * p.btc_amount
            eth_pnl = (p.entry_eth - eth_price) / p.entry_eth * p.eth_amount
        elif p.state == PositionState.SHORT_SPREAD:
            btc_pnl = (p.entry_btc - btc_price) / p.entry_btc * p.btc_amount
            eth_pnl = (eth_price - p.entry_eth) / p.entry_eth * p.eth_amount
        return btc_pnl + eth_pnl

    async def _limit_price(self, instrument: str, buy: bool) -> float:
        book = await self.client.get_order_book(instrument, depth=1)
        tick_size = 0.5 if "BTC" in instrument else 0.05
        if buy:
            price = book["bids"][0][0] + self.execution.tick_offset * tick_size
        else:
            price = book["asks"][0][0] - self.execution.tick_offset * tick_size
        return round(price, 1 if "BTC" in instrument else 2)

    async def open_spread(
        self,
        side: SignalSide,
        btc_amount: float,
        eth_amount: float,
        btc_price: float,
        eth_price: float,
        zscore: float,
    ) -> OpenPosition | None:
        if side == SignalSide.FLAT or self.is_open:
            return None

        state = (
            PositionState.LONG_SPREAD
            if side == SignalSide.LONG_SPREAD
            else PositionState.SHORT_SPREAD
        )

        if self.client.paper_mode:
            pos = OpenPosition(
                state=state,
                btc_amount=btc_amount,
                eth_amount=eth_amount,
                entry_btc=btc_price,
                entry_eth=eth_price,
                entry_zscore=zscore,
                paper=True,
            )
            self.position = pos
            self.paper.open(pos)
            log.info("paper_open", state=state.value, btc=btc_amount, eth=eth_amount, zscore=zscore)
            return pos

        try:
            if state == PositionState.LONG_SPREAD:
                btc_price_limit = await self._limit_price(self.leg_a, buy=True)
                eth_price_limit = await self._limit_price(self.leg_b, buy=False)
                await self.client.buy(self.leg_a, btc_amount, "limit", btc_price_limit, self.execution.post_only)
                await self.client.sell(self.leg_b, eth_amount, "limit", eth_price_limit, self.execution.post_only)
            else:
                btc_price_limit = await self._limit_price(self.leg_a, buy=False)
                eth_price_limit = await self._limit_price(self.leg_b, buy=True)
                await self.client.sell(self.leg_a, btc_amount, "limit", btc_price_limit, self.execution.post_only)
                await self.client.buy(self.leg_b, eth_amount, "limit", eth_price_limit, self.execution.post_only)

            pos = OpenPosition(
                state=state,
                btc_amount=btc_amount,
                eth_amount=eth_amount,
                entry_btc=btc_price,
                entry_eth=eth_price,
                entry_zscore=zscore,
            )
            self.position = pos
            log.info("live_open", state=state.value, btc=btc_amount, eth=eth_amount)
            return pos
        except Exception as exc:
            log.error("open_failed", error=str(exc))
            await self.client.cancel_all()
            return None

    async def close_spread(self, btc_price: float, eth_price: float) -> float:
        if not self.position:
            return 0.0

        pnl = self.unrealized_pnl(btc_price, eth_price)
        pos = self.position

        if not self.client.paper_mode:
            try:
                if pos.state == PositionState.LONG_SPREAD:
                    await self.client.sell(self.leg_a, pos.btc_amount, "market")
                    await self.client.buy(self.leg_b, pos.eth_amount, "market")
                else:
                    await self.client.buy(self.leg_a, pos.btc_amount, "market")
                    await self.client.sell(self.leg_b, pos.eth_amount, "market")
            except Exception as exc:
                log.error("close_failed", error=str(exc))
                return 0.0
        else:
            self.paper.close(pos, pnl)

        log.info("position_closed", pnl_usd=round(pnl, 4), state=pos.state.value)
        self.position = None
        return pnl
