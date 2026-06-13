from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


class StateStore:
    def __init__(self, db_path: str) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_pnl (
                    day TEXT PRIMARY KEY,
                    realized_usd REAL,
                    trades INTEGER,
                    halted INTEGER,
                    halt_reason TEXT
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT PRIMARY KEY,
                    opened_at TEXT,
                    closed_at TEXT,
                    side TEXT,
                    btc_amount REAL,
                    eth_amount REAL,
                    entry_zscore REAL,
                    pnl_usd REAL,
                    paper INTEGER
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    ts TEXT,
                    zscore REAL,
                    beta REAL,
                    spread REAL,
                    pnl_usd REAL,
                    signal TEXT
                )
                """
            )
            await db.commit()

    async def save_daily(self, day: str, realized: float, trades: int, halted: bool, reason: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO daily_pnl(day, realized_usd, trades, halted, halt_reason)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(day) DO UPDATE SET
                    realized_usd=excluded.realized_usd,
                    trades=excluded.trades,
                    halted=excluded.halted,
                    halt_reason=excluded.halt_reason
                """,
                (day, realized, trades, int(halted), reason),
            )
            await db.commit()

    async def log_trade(
        self,
        trade_id: str,
        side: str,
        btc_amount: float,
        eth_amount: float,
        entry_zscore: float,
        pnl_usd: float,
        paper: bool,
        opened_at: float,
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO trades(id, opened_at, closed_at, side, btc_amount, eth_amount,
                                   entry_zscore, pnl_usd, paper)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id,
                    datetime.fromtimestamp(opened_at, tz=timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    side,
                    btc_amount,
                    eth_amount,
                    entry_zscore,
                    pnl_usd,
                    int(paper),
                ),
            )
            await db.commit()

    async def log_snapshot(self, zscore: float, beta: float, spread: float, pnl: float, signal: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO snapshots(ts, zscore, beta, spread, pnl_usd, signal) VALUES (?, ?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), zscore, beta, spread, pnl, signal),
            )
            await db.commit()

    async def load_today_pnl(self, day: str) -> float:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT realized_usd FROM daily_pnl WHERE day=?", (day,)) as cur:
                row = await cur.fetchone()
                return float(row[0]) if row else 0.0
