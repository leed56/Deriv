from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Awaitable

import structlog
import websockets
from websockets.asyncio.client import ClientConnection

log = structlog.get_logger()


class DerivClient:
    """Deriv WebSocket API client for ticks and options trading."""

    def __init__(self, ws_url: str, app_id: int, api_token: str = "") -> None:
        self.ws_url = f"{ws_url}?app_id={app_id}"
        self.api_token = api_token
        self._ws: ClientConnection | None = None
        self._req_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._subscriptions: dict[str, list[Callable[[dict], Awaitable[None] | None]]] = {}
        self._reader_task: asyncio.Task | None = None
        self._connected = False
        self.balance: float = 10000.0
        self.currency: str = "USD"
        self.loginid: str = ""

    @property
    def paper_mode(self) -> bool:
        return not bool(self.api_token)

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def connect(self) -> None:
        self._ws = await websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20)
        self._connected = True
        self._reader_task = asyncio.create_task(self._read_loop())
        if self.api_token:
            await self.authorize()
        else:
            log.warning("paper_mode", msg="No DERIV_API_TOKEN — simulated contract fills")

    async def close(self) -> None:
        self._connected = False
        if self._reader_task:
            self._reader_task.cancel()
        if self._ws:
            await self._ws.close()
        self._ws = None
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                req_id = msg.get("req_id")
                if req_id and req_id in self._pending:
                    fut = self._pending.pop(req_id)
                    if not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(msg["error"].get("message", str(msg["error"]))))
                        else:
                            fut.set_result(msg)
                    continue

                msg_type = msg.get("msg_type", "")
                if msg_type == "tick":
                    symbol = msg.get("tick", {}).get("symbol", "")
                    for handler in self._subscriptions.get(f"tick:{symbol}", []):
                        result = handler(msg["tick"])
                        if asyncio.iscoroutine(result):
                            await result
                elif msg_type == "proposal_open_contract":
                    for handler in self._subscriptions.get("proposal_open_contract", []):
                        result = handler(msg.get("proposal_open_contract", {}))
                        if asyncio.iscoroutine(result):
                            await result
        except asyncio.CancelledError:
            pass

    async def send(self, payload: dict[str, Any], timeout: float = 30.0) -> dict:
        if self._ws is None:
            raise RuntimeError("Not connected")
        req_id = self._next_id()
        payload = {**payload, "req_id": req_id}
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        await self._ws.send(json.dumps(payload))
        return await asyncio.wait_for(fut, timeout=timeout)

    async def authorize(self) -> dict:
        resp = await self.send({"authorize": self.api_token})
        auth = resp["authorize"]
        self.balance = float(auth.get("balance", 0))
        self.currency = auth.get("currency", "USD")
        self.loginid = auth.get("loginid", "")
        log.info("authorized", loginid=self.loginid, balance=self.balance, currency=self.currency)
        return auth

    def on_tick(self, symbol: str, handler: Callable[[dict], Awaitable[None] | None]) -> None:
        self._subscriptions.setdefault(f"tick:{symbol}", []).append(handler)

    def on_contract(self, handler: Callable[[dict], Awaitable[None] | None]) -> None:
        self._subscriptions.setdefault("proposal_open_contract", []).append(handler)

    async def subscribe_ticks(self, symbols: list[str]) -> None:
        for symbol in symbols:
            await self.send({"ticks": symbol, "subscribe": 1})

    async def get_proposal(
        self,
        symbol: str,
        contract_type: str,
        amount: float,
        duration: int,
        duration_unit: str,
        currency: str = "USD",
    ) -> dict:
        resp = await self.send(
            {
                "proposal": 1,
                "amount": amount,
                "basis": "stake",
                "contract_type": contract_type,
                "currency": currency,
                "duration": duration,
                "duration_unit": duration_unit,
                "symbol": symbol,
            }
        )
        return resp["proposal"]

    async def buy(self, proposal_id: str, price: float) -> dict:
        resp = await self.send({"buy": proposal_id, "price": price})
        buy = resp["buy"]
        contract_id = buy["contract_id"]
        await self.send({"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1})
        return buy

    async def sell(self, contract_id: int, price: float = 0) -> dict:
        resp = await self.send({"sell": contract_id, "price": price})
        return resp.get("sell", {})
