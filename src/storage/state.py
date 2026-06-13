from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import aiosqlite


@dataclass
class RiskState:
    trading_day: str
    anchor_balance: float
    peak_balance: float
    realized_pnl_usd: float
    trades_today: int
    halted: bool
    halt_reason: str
    paper_balance: float


class StateStore:
    def __init__(self, db_path: str) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """CREATE TABLE IF NOT EXISTS risk_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    trading_day TEXT NOT NULL,
                    anchor_balance REAL NOT NULL,
                    peak_balance REAL NOT NULL,
                    realized_pnl_usd REAL NOT NULL DEFAULT 0,
                    trades_today INTEGER NOT NULL DEFAULT 0,
                    halted INTEGER NOT NULL DEFAULT 0,
                    halt_reason TEXT NOT NULL DEFAULT '',
                    paper_balance REAL NOT NULL DEFAULT 10000
                )"""
            )
            await db.execute(
                """CREATE TABLE IF NOT EXISTS trades (
                    id TEXT, ts TEXT, symbol TEXT, result TEXT, pnl_usd REAL, paper INTEGER)"""
            )
            await db.execute(
                """CREATE TABLE IF NOT EXISTS snapshots (
                    ts TEXT, zscore REAL, spread REAL, pnl_usd REAL, signal TEXT)"""
            )
            await db.commit()

    async def load_risk_state(self, default_paper_balance: float = 10000.0) -> RiskState | None:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT trading_day, anchor_balance, peak_balance, realized_pnl_usd, "
                "trades_today, halted, halt_reason, paper_balance FROM risk_state WHERE id = 1"
            ) as cur:
                row = await cur.fetchone()
                if not row:
                    return None
                return RiskState(
                    trading_day=row[0],
                    anchor_balance=float(row[1]),
                    peak_balance=float(row[2]),
                    realized_pnl_usd=float(row[3]),
                    trades_today=int(row[4]),
                    halted=bool(row[5]),
                    halt_reason=row[6] or "",
                    paper_balance=float(row[7]),
                )

    async def save_risk_state(
        self,
        trading_day: date,
        anchor_balance: float,
        peak_balance: float,
        realized_pnl_usd: float,
        trades_today: int,
        halted: bool,
        halt_reason: str,
        paper_balance: float,
    ) -> None:
        day_str = trading_day.isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO risk_state(
                       id, trading_day, anchor_balance, peak_balance, realized_pnl_usd,
                       trades_today, halted, halt_reason, paper_balance)
                   VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       trading_day=excluded.trading_day,
                       anchor_balance=excluded.anchor_balance,
                       peak_balance=excluded.peak_balance,
                       realized_pnl_usd=excluded.realized_pnl_usd,
                       trades_today=excluded.trades_today,
                       halted=excluded.halted,
                       halt_reason=excluded.halt_reason,
                       paper_balance=excluded.paper_balance""",
                (
                    day_str,
                    anchor_balance,
                    peak_balance,
                    realized_pnl_usd,
                    trades_today,
                    int(halted),
                    halt_reason,
                    paper_balance,
                ),
            )
            await db.commit()

    async def log_trade(self, trade_id: str, symbol: str, result: str, pnl: float, paper: bool) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO trades(id,ts,symbol,result,pnl_usd,paper) VALUES(?,?,?,?,?,?)",
                (trade_id, datetime.now(timezone.utc).isoformat(), symbol, result, pnl, int(paper)),
            )
            await db.commit()

    async def log_snapshot(self, zscore: float, spread: float, pnl: float, signal: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO snapshots(ts,zscore,spread,pnl_usd,signal) VALUES(?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), zscore, spread, pnl, signal),
            )
            await db.commit()
