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
    spread_pair_mid_low: str
    spread_pair_mid_high: str
    kalman: KalmanConfig
    entry_zscore: float = 2.0
    exit_zscore: float = 0.35
    min_confidence: float = 0.58
    min_payout_ratio: float = 1.85
    warmup_ticks: int = 80
    contract_duration: int = 5
    contract_duration_unit: str = "t"


@dataclass
class RiskConfig:
    daily_profit_target_usd: float = 5.0
    max_daily_loss_pct: float = 0.05
    max_drawdown_pct: float = 0.10
    max_lifetime_drawdown_pct: float = 0.25
    max_consecutive_loss_days: int = 4
    cooldown_hours: int = 8
    max_stake_usd: float = 2.0
    min_stake_usd: float = 0.35
    stake_pct_of_balance: float = 0.025
    max_open_contracts: int = 1
    currency: str = "USD"


from src.loop.profit_loop import ProfitLoopConfig


@dataclass
class BotConfig:
    state_db: str = "data/bot_state.db"
    log_level: str = "INFO"
    reconcile_interval_sec: int = 30
    profit_loop: ProfitLoopConfig = field(default_factory=ProfitLoopConfig)


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
    s = raw["strategy"]
    strategy = StrategyConfig(
        spread_pair_low=s["spread_pair_low"],
        spread_pair_high=s["spread_pair_high"],
        spread_pair_mid_low=s["spread_pair_mid_low"],
        spread_pair_mid_high=s["spread_pair_mid_high"],
        kalman=KalmanConfig(**kalman_raw),
        entry_zscore=s["entry_zscore"],
        exit_zscore=s["exit_zscore"],
        min_confidence=s["min_confidence"],
        min_payout_ratio=s.get("min_payout_ratio", 1.85),
        warmup_ticks=s["warmup_ticks"],
        contract_duration=s["contract_duration"],
        contract_duration_unit=s["contract_duration_unit"],
    )

    vol_pairs = [VolPairLeg(**leg) for leg in raw["vol_pairs"]]

    bot_raw = raw.get("bot", {})
    pl_raw = bot_raw.get("profit_loop", {})
    bot = BotConfig(
        state_db=bot_raw.get("state_db", "data/bot_state.db"),
        log_level=bot_raw.get("log_level", "INFO"),
        reconcile_interval_sec=bot_raw.get("reconcile_interval_sec", 30),
        profit_loop=ProfitLoopConfig(
            enabled=pl_raw.get("enabled", True),
            reinvest_profit_pct=pl_raw.get("reinvest_profit_pct", 0.5),
            min_seconds_between_trades=pl_raw.get("min_seconds_between_trades", 4),
            compound_wins=pl_raw.get("compound_wins", True),
        ),
    )

    return Settings(
        ws_url=deriv["ws_url"],
        app_id=app_id,
        api_token=os.getenv("DERIV_API_TOKEN", ""),
        account_type=os.getenv("DERIV_ACCOUNT", "demo").lower(),
        vol_pairs=vol_pairs,
        strategy=strategy,
        risk=RiskConfig(**risk),
        bot=bot,
    )
