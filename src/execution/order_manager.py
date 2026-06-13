from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

import structlog

from src.api.client import DerivClient
from src.config import RiskConfig, StrategyConfig
from src.strategy.engine import TradeAction, VolPairSignal

log = structlog.get_logger()


@dataclass
class OpenContract:
    contract_id: str
    symbol: str
    contract_type: str
    stake: float
    entry_epoch: int
    paper: bool = False
    trade_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])


class ContractManager:
    """Execute Deriv CALL/PUT with fixed stake (= max loss per trade)."""

    def __init__(self, client: DerivClient, strategy: StrategyConfig, risk: RiskConfig) -> None:
        self.client = client
        self.strategy = strategy
        self.risk = risk
        self.open: OpenContract | None = None
        self._paper_balance_delta = 0.0

    @property
    def has_open(self) -> bool:
        return self.open is not None

    async def open_contract(self, signal: VolPairSignal, stake: float) -> OpenContract | None:
        if signal.action == TradeAction.FLAT or self.has_open:
            return None

        contract_type = "CALL" if signal.action == TradeAction.CALL else "PUT"
        duration = self.strategy.contract_duration
        unit = self.strategy.contract_duration_unit

        if self.client.paper_mode:
            c = OpenContract(
                contract_id=f"paper-{int(time.time())}",
                symbol=signal.symbol,
                contract_type=contract_type,
                stake=stake,
                entry_epoch=int(time.time()),
                paper=True,
            )
            self.open = c
            log.info("paper_buy", symbol=signal.symbol, type=contract_type, stake=stake)
            return c

        try:
            proposal = await self.client.get_proposal(
                signal.symbol, contract_type, stake, duration, unit, self.risk.currency
            )
            buy = await self.client.buy(proposal["id"], float(proposal["ask_price"]))
            c = OpenContract(
                contract_id=str(buy["contract_id"]),
                symbol=signal.symbol,
                contract_type=contract_type,
                stake=stake,
                entry_epoch=int(time.time()),
            )
            self.open = c
            log.info("live_buy", contract_id=c.contract_id, symbol=signal.symbol, stake=stake)
            return c
        except Exception as exc:
            log.error("buy_failed", error=str(exc))
            return None

    async def settle_paper(self, won: bool, payout: float = 0.0) -> float:
        if not self.open or not self.open.paper:
            return 0.0
        stake = self.open.stake
        pnl = (payout - stake) if won else -stake
        if payout == 0.0 and won:
            pnl = stake * 0.92  # typical ~92% return on win
        self._paper_balance_delta += pnl
        log.info("paper_settle", won=won, pnl=round(pnl, 4))
        self.open = None
        return pnl

    async def close_live(self, profit: float) -> float:
        if not self.open or self.open.paper:
            return 0.0
        try:
            await self.client.sell(int(self.open.contract_id))
        except Exception:
            pass
        self.open = None
        return profit

    def on_contract_update(self, data: dict) -> float | None:
        """Returns realized PnL when contract closes."""
        if not self.open or self.open.paper:
            return None
        if data.get("is_sold") or data.get("status") == "lost":
            profit = float(data.get("profit", 0))
            self.open = None
            return profit
        return None
