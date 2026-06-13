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
    wins_today: int
    losses_today: int
    halted: bool
    halt_reason: str
    profit_day_locked: bool
    paper_balance: float
    lifetime_peak_balance: float
    lifetime_anchor_balance: float
    consecutive_loss_days: int
    stake_multiplier: float
    cooldown_until: str


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
                    wins_today INTEGER NOT NULL DEFAULT 0,
                    losses_today INTEGER NOT NULL DEFAULT 0,
                    halted INTEGER NOT NULL DEFAULT 0,
                    halt_reason TEXT NOT NULL DEFAULT '',
                    profit_day_locked INTEGER NOT NULL DEFAULT 0,
                    paper_balance REAL NOT NULL DEFAULT 10000,
                    lifetime_peak_balance REAL NOT NULL DEFAULT 10000,
                    lifetime_anchor_balance REAL NOT NULL DEFAULT 10000,
                    consecutive_loss_days INTEGER NOT NULL DEFAULT 0,
                    stake_multiplier REAL NOT NULL DEFAULT 1.0,
                    cooldown_until TEXT NOT NULL DEFAULT ''
                )"""
            )
            await self._migrate_columns(db)
            await db.execute(
                """CREATE TABLE IF NOT EXISTS trades (
                    id TEXT, ts TEXT, symbol TEXT, result TEXT, pnl_usd REAL, paper INTEGER)"""
            )
            await db.execute(
                """CREATE TABLE IF NOT EXISTS snapshots (
                    ts TEXT, zscore REAL, spread REAL, pnl_usd REAL, signal TEXT)"""
            )
            await db.commit()

    async def _migrate_columns(self, db: aiosqlite.Connection) -> None:
        async with db.execute("PRAGMA table_info(risk_state)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        migrations = {
            "wins_today": "INTEGER NOT NULL DEFAULT 0",
            "losses_today": "INTEGER NOT NULL DEFAULT 0",
            "profit_day_locked": "INTEGER NOT NULL DEFAULT 0",
            "stake_multiplier": "REAL NOT NULL DEFAULT 1.0",
            "cooldown_until": "TEXT NOT NULL DEFAULT ''",
            "lifetime_peak_balance": "REAL NOT NULL DEFAULT 10000",
            "lifetime_anchor_balance": "REAL NOT NULL DEFAULT 10000",
            "consecutive_loss_days": "INTEGER NOT NULL DEFAULT 0",
        }
        for col, typedef in migrations.items():
            if col not in cols:
                await db.execute(f"ALTER TABLE risk_state ADD COLUMN {col} {typedef}")

    async def load_risk_state(self) -> RiskState | None:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT trading_day, anchor_balance, peak_balance, realized_pnl_usd, "
                "trades_today, wins_today, losses_today, halted, halt_reason, "
                "profit_day_locked, paper_balance, lifetime_peak_balance, "
                "lifetime_anchor_balance, consecutive_loss_days, stake_multiplier, "
                "cooldown_until FROM risk_state WHERE id = 1"
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
                    wins_today=int(row[5]),
                    losses_today=int(row[6]),
                    halted=bool(row[7]),
                    halt_reason=row[8] or "",
                    profit_day_locked=bool(row[9]),
                    paper_balance=float(row[10]),
                    lifetime_peak_balance=float(row[11]),
                    lifetime_anchor_balance=float(row[12]),
                    consecutive_loss_days=int(row[13]),
                    stake_multiplier=float(row[14]),
                    cooldown_until=row[15] or "",
                )

    async def save_risk_state(self, **kwargs: object) -> None:
        trading_day: date = kwargs["trading_day"]  # type: ignore[assignment]
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO risk_state(
                       id, trading_day, anchor_balance, peak_balance, realized_pnl_usd,
                       trades_today, wins_today, losses_today, halted, halt_reason,
                       profit_day_locked, paper_balance, lifetime_peak_balance,
                       lifetime_anchor_balance, consecutive_loss_days, stake_multiplier,
                       cooldown_until)
                   VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       trading_day=excluded.trading_day,
                       anchor_balance=excluded.anchor_balance,
                       peak_balance=excluded.peak_balance,
                       realized_pnl_usd=excluded.realized_pnl_usd,
                       trades_today=excluded.trades_today,
                       wins_today=excluded.wins_today,
                       losses_today=excluded.losses_today,
                       halted=excluded.halted,
                       halt_reason=excluded.halt_reason,
                       profit_day_locked=excluded.profit_day_locked,
                       paper_balance=excluded.paper_balance,
                       lifetime_peak_balance=excluded.lifetime_peak_balance,
                       lifetime_anchor_balance=excluded.lifetime_anchor_balance,
                       consecutive_loss_days=excluded.consecutive_loss_days,
                       stake_multiplier=excluded.stake_multiplier,
                       cooldown_until=excluded.cooldown_until""",
                (
                    trading_day.isoformat(),
                    kwargs["anchor_balance"],
                    kwargs["peak_balance"],
                    kwargs["realized_pnl_usd"],
                    kwargs["trades_today"],
                    kwargs["wins_today"],
                    kwargs["losses_today"],
                    int(kwargs["halted"]),
                    kwargs["halt_reason"],
                    int(kwargs["profit_day_locked"]),
                    kwargs["paper_balance"],
                    kwargs["lifetime_peak_balance"],
                    kwargs["lifetime_anchor_balance"],
                    kwargs["consecutive_loss_days"],
                    kwargs["stake_multiplier"],
                    kwargs["cooldown_until"],
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
