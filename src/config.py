from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "settings.yaml"


@dataclass
class VolPairLeg:
    symbol: str
    vol_pct: float
    weight: float = 1.0


@dataclass
class KalmanConfig:
    process_noise: float = 1e-4
    observation_noise: float = 1e-2


@dataclass
class StrategyConfig:
    spread_pair_low: str
    spread_pair_high: str
    kalman: KalmanConfig
    entry_zscore: float = 1.8
    exit_zscore: float = 0.35
    min_confidence: float = 0.52
    warmup_ticks: int = 60
    contract_duration: int = 5
    contract_duration_unit: str = "t"


@dataclass
class RiskConfig:
    daily_profit_target_usd: float = 5.0
    max_daily_loss_pct: float = 0.05
    max_drawdown_pct: float = 0.10
    max_stake_usd: float = 2.0
    min_stake_usd: float = 0.35
    stake_pct_of_balance: float = 0.02
    max_open_contracts: int = 1
    currency: str = "USD"


@dataclass
class BotConfig:
    state_db: str = "data/bot_state.db"
    log_level: str = "INFO"
    reconcile_interval_sec: int = 30


@dataclass
class Settings:
    ws_url: str
    app_id: int
    api_token: str
    account_type: str
    vol_pairs: list[VolPairLeg]
    strategy: StrategyConfig
    risk: RiskConfig
    bot: BotConfig


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    with CONFIG_PATH.open() as f:
        raw = yaml.safe_load(f)

    deriv = raw["deriv"]
    app_id = int(os.getenv("DERIV_APP_ID", deriv.get("app_id", 1089)))

    risk = raw.get("risk", {})
    if os.getenv("DAILY_PROFIT_TARGET_USD"):
        risk = {**risk, "daily_profit_target_usd": float(os.getenv("DAILY_PROFIT_TARGET_USD", "5"))}

    kalman_raw = raw["strategy"]["kalman"]
    strategy = StrategyConfig(
        spread_pair_low=raw["strategy"]["spread_pair_low"],
        spread_pair_high=raw["strategy"]["spread_pair_high"],
        kalman=KalmanConfig(**kalman_raw),
        entry_zscore=raw["strategy"]["entry_zscore"],
        exit_zscore=raw["strategy"]["exit_zscore"],
        min_confidence=raw["strategy"]["min_confidence"],
        warmup_ticks=raw["strategy"]["warmup_ticks"],
        contract_duration=raw["strategy"]["contract_duration"],
        contract_duration_unit=raw["strategy"]["contract_duration_unit"],
    )

    vol_pairs = [VolPairLeg(**leg) for leg in raw["vol_pairs"]]

    return Settings(
        ws_url=deriv["ws_url"],
        app_id=app_id,
        api_token=os.getenv("DERIV_API_TOKEN", ""),
        account_type=os.getenv("DERIV_ACCOUNT", "demo").lower(),
        vol_pairs=vol_pairs,
        strategy=strategy,
        risk=RiskConfig(**risk),
        bot=BotConfig(**raw["bot"]),
    )
