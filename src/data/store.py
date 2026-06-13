"""Local persistence for OHLCV snapshots and bot state."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd


class StateStore:
  """Persists portfolio state across restarts."""

  def __init__(self, path: Path) -> None:
    self.path = path
    self.path.parent.mkdir(parents=True, exist_ok=True)

  def load(self) -> dict[str, Any]:
    if not self.path.exists():
      return {}
    with self.path.open() as f:
      return json.load(f)

  def save(self, state: dict[str, Any]) -> None:
    with self.path.open("w") as f:
      json.dump(state, f, indent=2, default=str)

  def get_daily_pnl(self, today: date | None = None) -> float:
    today = today or date.today()
    state = self.load()
    daily = state.get("daily_pnl", {})
    return float(daily.get(str(today), 0.0))

  def record_daily_pnl(self, pnl_delta: float, today: date | None = None) -> float:
    today = today or date.today()
    state = self.load()
    daily = state.setdefault("daily_pnl", {})
    key = str(today)
    daily[key] = float(daily.get(key, 0.0)) + pnl_delta
    state["last_updated"] = datetime.utcnow().isoformat()
    self.save(state)
    return daily[key]


def cache_ohlcv(df: pd.DataFrame, path: Path) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  df.to_parquet(path)
