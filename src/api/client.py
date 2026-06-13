from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Awaitable

import aiohttp
import structlog
import websockets
from websockets.asyncio.client import ClientConnection

log = structlog.get_logger()


class DeribitClient:
    """Async Deribit JSON-RPC client over WebSocket + HTTP fallback."""

    def __init__(self, http_url: str, ws_url: str, client_id: str, client_secret: str) -> None:
        self.http_url = http_url
        self.ws_url = ws_url
        self.client_id = client_id
        self.client_secret = client_secret
        self._ws: ClientConnection | None = None
        self._msg_id = 0
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expires_at = 0.0
        self._pending: dict[int, asyncio.Future] = {}
        self._handlers: dict[str, list[Callable[[dict], Awaitable[None] | None]]] = {}
        self._reader_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._connected = False

    @property
    def authenticated(self) -> bool:
        return bool(self._access_token)

    @property
    def paper_mode(self) -> bool:
        return not (self.client_id and self.client_secret)

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def connect(self) -> None:
        self._ws = await websockets.connect(
            self.ws_url,
            ping_interval=20,
            ping_timeout=20,
            max_size=2**22,
        )
        self._connected = True
        self._reader_task = asyncio.create_task(self._read_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        if not self.paper_mode:
            await self.authenticate()
        else:
            log.warning("paper_mode", msg="No API keys — live orders disabled, paper execution enabled")

    async def close(self) -> None:
        self._connected = False
        for task in (self._reader_task, self._heartbeat_task):
            if task:
                task.cancel()
        if self._ws:
            await self._ws.close()
        self._ws = None
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    async def _heartbeat_loop(self) -> None:
        while self._connected:
            try:
                await self.call("public/test", {})
            except Exception:
                pass
            await asyncio.sleep(10)

    async def _read_loop(self) -> None:
        assert self._ws is not None
        async for raw in self._ws:
            msg = json.loads(raw)
            if "id" in msg and msg["id"] in self._pending:
                fut = self._pending.pop(msg["id"])
                if not fut.done():
                    if "error" in msg:
                        fut.set_exception(RuntimeError(str(msg["error"])))
                    else:
                        fut.set_result(msg.get("result"))
                continue
            if msg.get("method") == "subscription":
                channel = msg["params"]["channel"]
                data = msg["params"]["data"]
                for handler in self._handlers.get(channel, []):
                    result = handler(data)
                    if asyncio.iscoroutine(result):
                        await result

    async def call(self, method: str, params: dict[str, Any] | None = None, private: bool = False) -> Any:
        if self._ws is None:
            raise RuntimeError("WebSocket not connected")
        if private and self._access_token:
            params = {**(params or {}), "access_token": self._access_token}
        msg_id = self._next_id()
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": msg_id}
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = fut
        await self._ws.send(json.dumps(payload))
        try:
            return await asyncio.wait_for(fut, timeout=30)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise

    async def http_call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.http_url}/{method}"
        headers = {}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params or {}, headers=headers) as resp:
                body = await resp.json()
                if "error" in body:
                    raise RuntimeError(str(body["error"]))
                return body.get("result")

    async def authenticate(self) -> None:
        result = await self.call(
            "public/auth",
            {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        self._access_token = result["access_token"]
        self._refresh_token = result.get("refresh_token")
        self._token_expires_at = time.time() + result.get("expires_in", 3600) - 60
        log.info("authenticated", scope=result.get("scope"))

    async def ensure_auth(self) -> None:
        if self.paper_mode:
            return
        if time.time() >= self._token_expires_at and self._refresh_token:
            result = await self.call(
                "public/auth",
                {"grant_type": "refresh_token", "refresh_token": self._refresh_token},
            )
            self._access_token = result["access_token"]
            self._refresh_token = result.get("refresh_token", self._refresh_token)
            self._token_expires_at = time.time() + result.get("expires_in", 3600) - 60

    def on_channel(self, channel: str, handler: Callable[[dict], Awaitable[None] | None]) -> None:
        self._handlers.setdefault(channel, []).append(handler)

    async def subscribe(self, channels: list[str]) -> None:
        await self.call("public/subscribe", {"channels": channels})

    async def get_ticker(self, instrument: str) -> dict:
        return await self.call("public/ticker", {"instrument_name": instrument})

    async def get_order_book(self, instrument: str, depth: int = 10) -> dict:
        return await self.call("public/get_order_book", {"instrument_name": instrument, "depth": depth})

    async def get_account_summary(self, currency: str = "BTC") -> dict:
        await self.ensure_auth()
        return await self.call("private/get_account_summary", {"currency": currency}, private=True)

    async def get_positions(self, currency: str = "BTC", kind: str = "future") -> list[dict]:
        await self.ensure_auth()
        return await self.call(
            "private/get_positions", {"currency": currency, "kind": kind}, private=True
        )

    async def buy(
        self,
        instrument: str,
        amount: float,
        order_type: str = "limit",
        price: float | None = None,
        post_only: bool = True,
        label: str = "profit-bot",
    ) -> dict:
        await self.ensure_auth()
        params: dict[str, Any] = {
            "instrument_name": instrument,
            "amount": amount,
            "type": order_type,
            "label": label,
        }
        if price is not None:
            params["price"] = price
        if post_only and order_type == "limit":
            params["post_only"] = True
        return await self.call("private/buy", params, private=True)

    async def sell(
        self,
        instrument: str,
        amount: float,
        order_type: str = "limit",
        price: float | None = None,
        post_only: bool = True,
        label: str = "profit-bot",
    ) -> dict:
        await self.ensure_auth()
        params: dict[str, Any] = {
            "instrument_name": instrument,
            "amount": amount,
            "type": order_type,
            "label": label,
        }
        if price is not None:
            params["price"] = price
        if post_only and order_type == "limit":
            params["post_only"] = True
        return await self.call("private/sell", params, private=True)

    async def cancel_all(self, currency: str = "BTC") -> int:
        await self.ensure_auth()
        return await self.call("private/cancel_all_by_currency", {"currency": currency}, private=True)
