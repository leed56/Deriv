from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "settings.yaml"


@dataclass
class KalmanConfig:
    process_noise: float = 1e-5
    observation_noise: float = 1e-3
    beta_init: float = 1.35


@dataclass
class StrategyConfig:
    kalman: KalmanConfig = field(default_factory=KalmanConfig)
    entry_zscore: float = 2.0
    exit_zscore: float = 0.4
    obi_weight: float = 0.35
    funding_weight: float = 0.15
    min_confidence: float = 0.55
    warmup_ticks: int = 120


@dataclass
class RiskConfig:
    daily_profit_target_usd: float = 5.0
    daily_loss_limit_usd: float = 15.0
    max_position_usd: float = 500.0
    max_open_trades: int = 2
    stop_loss_pct: float = 0.8


@dataclass
class ExecutionConfig:
    order_type: str = "limit"
    post_only: bool = True
    tick_offset: int = 1
    reconcile_interval_sec: int = 30


@dataclass
class BotConfig:
    heartbeat_sec: int = 10
    state_db: str = "data/bot_state.db"
    log_level: str = "INFO"


@dataclass
class Settings:
    http_url: str
    ws_url: str
    leg_a: str
    leg_b: str
    currency: str
    client_id: str
    client_secret: str
    strategy: StrategyConfig
    risk: RiskConfig
    execution: ExecutionConfig
    bot: BotConfig
    env: str


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    with CONFIG_PATH.open() as f:
        raw = yaml.safe_load(f)

    env = os.getenv("DERIBIT_ENV", "testnet").lower()
    endpoints = raw["deribit"][env]
    inst = raw["instruments"]

    risk = raw.get("risk", {})
    if os.getenv("DAILY_PROFIT_TARGET_USD"):
        risk = {**risk, "daily_profit_target_usd": float(os.getenv("DAILY_PROFIT_TARGET_USD", "5"))}

    kalman_raw = raw["strategy"]["kalman"]
    strategy = StrategyConfig(
        kalman=KalmanConfig(**kalman_raw),
        entry_zscore=raw["strategy"]["entry_zscore"],
        exit_zscore=raw["strategy"]["exit_zscore"],
        obi_weight=raw["strategy"]["obi_weight"],
        funding_weight=raw["strategy"]["funding_weight"],
        min_confidence=raw["strategy"]["min_confidence"],
        warmup_ticks=raw["strategy"]["warmup_ticks"],
    )

    return Settings(
        http_url=endpoints["http"],
        ws_url=endpoints["ws"],
        leg_a=inst["leg_a"],
        leg_b=inst["leg_b"],
        currency=inst["currency"],
        client_id=os.getenv("DERIBIT_CLIENT_ID", ""),
        client_secret=os.getenv("DERIBIT_CLIENT_SECRET", ""),
        strategy=strategy,
        risk=RiskConfig(**risk),
        execution=ExecutionConfig(**raw["execution"]),
        bot=BotConfig(**raw["bot"]),
        env=env,
    )
