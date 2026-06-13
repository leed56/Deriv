"""Real-time market data via ccxt (live exchange connection with fallbacks)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import ccxt
import pandas as pd

logger = logging.getLogger("profit_bot.data")

FALLBACK_EXCHANGES = ("okx", "kraken", "kucoin", "bitget", "binance")


class LiveDataFeed:
    """Fetches live OHLCV and order book data from a crypto exchange."""

    def __init__(
        self,
        exchange_id: str = "okx",
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = True,
        fallbacks: tuple[str, ...] = FALLBACK_EXCHANGES,
    ) -> None:
        self.exchange_id = exchange_id
        self.exchange = self._connect(exchange_id, api_key, api_secret, testnet, fallbacks)
        logger.info("Connected to %s (%d markets)", self.exchange_id, len(self.exchange.markets))

    def _connect(
        self,
        exchange_id: str,
        api_key: str,
        api_secret: str,
        testnet: bool,
        fallbacks: tuple[str, ...],
    ) -> ccxt.Exchange:
        tried = []
        for ex_id in (exchange_id, *fallbacks):
            if ex_id in tried:
                continue
            tried.append(ex_id)
            try:
                exchange_class = getattr(ccxt, ex_id)
                config: dict[str, Any] = {"enableRateLimit": True}
                if api_key and api_secret:
                    config["apiKey"] = api_key
                    config["secret"] = api_secret

                exchange: ccxt.Exchange = exchange_class(config)
                if testnet and ex_id == "binance":
                    exchange.set_sandbox_mode(True)

                exchange.load_markets()
                self.exchange_id = ex_id
                return exchange
            except Exception as e:
                logger.warning("Exchange %s unavailable: %s", ex_id, e)
        raise ConnectionError(f"Could not connect to any exchange. Tried: {tried}")

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 500,
    ) -> pd.DataFrame:
        raw = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        return df.astype(float)

    def fetch_multi_ohlcv(
        self,
        symbols: list[str],
        timeframe: str = "1m",
        limit: int = 500,
    ) -> dict[str, pd.DataFrame]:
        result: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            if sym not in self.exchange.markets:
                alt = sym.replace("BNB", "OKB") if self.exchange_id == "okx" else None
                if alt and alt in self.exchange.markets:
                    sym = alt
                else:
                    logger.warning("Symbol %s not on %s, skipping", sym, self.exchange_id)
                    continue
            result[sym] = self.fetch_ohlcv(sym, timeframe, limit)
        return result

    def fetch_ticker(self, symbol: str) -> dict[str, float]:
        t = self.exchange.fetch_ticker(symbol)
        return {
            "bid": float(t.get("bid") or t["last"]),
            "ask": float(t.get("ask") or t["last"]),
            "last": float(t["last"]),
            "volume": float(t.get("baseVolume") or 0),
        }

    def fetch_order_book_imbalance(self, symbol: str, depth: int = 20) -> float:
        """Microstructure signal: bid/ask volume imbalance in [-1, 1]."""
        book = self.exchange.fetch_order_book(symbol, limit=depth)
        bid_vol = sum(b[1] for b in book["bids"])
        ask_vol = sum(a[1] for a in book["asks"])
        total = bid_vol + ask_vol
        if total == 0:
            return 0.0
        return (bid_vol - ask_vol) / total

    def aligned_close_matrix(
        self,
        symbols: list[str],
        timeframe: str = "1m",
        limit: int = 500,
    ) -> pd.DataFrame:
        frames = self.fetch_multi_ohlcv(symbols, timeframe, limit)
        if not frames:
            raise ValueError("No symbols available on connected exchange")
        closes = pd.DataFrame({sym: df["close"] for sym, df in frames.items()})
        return closes.dropna()

    def now(self) -> datetime:
        return datetime.now(timezone.utc)
