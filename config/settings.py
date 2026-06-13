"""Bot configuration loaded from environment."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Exchange
    exchange: str = "okx"
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = True
    symbols: str = "BTC/USDT,ETH/USDT,SOL/USDT,OKB/USDT"
    timeframe: str = "1m"
    lookback_bars: int = 500

    # Portfolio / risk
    initial_capital_usd: float = 1000.0
    daily_profit_target_usd: float = 5.0
    max_daily_loss_usd: float = 15.0
    max_position_pct: float = 0.25
    min_trade_usd: float = 10.0

    # Engine
    poll_interval_seconds: int = 30
    log_level: str = "INFO"
    data_dir: Path = Field(default_factory=lambda: Path("data"))
    state_file: Path = Field(default_factory=lambda: Path("data/state.json"))

    @property
    def symbol_list(self) -> list[str]:
        return [s.strip() for s in self.symbols.split(",") if s.strip()]


settings = Settings()
