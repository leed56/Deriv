"""
Async Deriv WebSocket API client.
"""

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import websockets
from websockets.exceptions import ConnectionClosed

from config import DERIV_WS_URL

logger = logging.getLogger("deriv_api")


class DerivAPI:
    def __init__(self, token: str = ""):
        self.token      = token
        self.ws         = None
        self._req_id    = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._subs: dict[str, asyncio.Queue]     = {}   # subscription_id -> Queue
        self._recv_task = None

    # ── connection ────────────────────────────────────────────────────
    async def connect(self):
        self.ws = await websockets.connect(DERIV_WS_URL, ping_interval=30)
        self._recv_task = asyncio.create_task(self._recv_loop())
        logger.info("Connected to Deriv WebSocket")

    async def disconnect(self):
        if self._recv_task:
            self._recv_task.cancel()
        if self.ws:
            await self.ws.close()
        logger.info("Disconnected")

    # ── low-level send / receive ───────────────────────────────────────
    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _send(self, payload: dict) -> dict:
        req_id = self._next_id()
        payload["req_id"] = req_id
        fut = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        await self.ws.send(json.dumps(payload))
        return await fut

    async def _recv_loop(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                req_id = msg.get("req_id")
                sub_id = msg.get("subscription", {}).get("id") if isinstance(msg.get("subscription"), dict) else None

                if sub_id and sub_id in self._subs:
                    await self._subs[sub_id].put(msg)
                elif req_id and req_id in self._pending:
                    fut = self._pending.pop(req_id)
                    if not fut.done():
                        fut.set_result(msg)
        except (ConnectionClosed, asyncio.CancelledError):
            pass

    # ── high-level API calls ───────────────────────────────────────────
    async def authorize(self) -> dict:
        resp = await self._send({"authorize": self.token})
        if resp.get("error"):
            raise RuntimeError(f"Auth error: {resp['error']['message']}")
        return resp["authorize"]

    async def get_balance(self) -> float:
        resp = await self._send({"balance": 1, "account": "current"})
        return float(resp["balance"]["balance"])

    async def get_ticks_history(self, symbol: str, count: int = 5000) -> list[float]:
        resp = await self._send({
            "ticks_history": symbol,
            "count":         count,
            "end":           "latest",
            "style":         "ticks",
        })
        history = resp.get("history", {})
        prices = [float(p) for p in history.get("prices", [])]
        return prices

    async def subscribe_ticks(self, symbol: str) -> tuple[str, asyncio.Queue]:
        """Subscribe to live ticks. Returns (sub_id, queue)."""
        req_id = self._next_id()
        payload = {"ticks": symbol, "subscribe": 1, "req_id": req_id}
        fut = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        await self.ws.send(json.dumps(payload))
        first = await fut
        sub_id = first["subscription"]["id"]
        q: asyncio.Queue = asyncio.Queue()
        self._subs[sub_id] = q
        # put the first tick into the queue
        await q.put(first)
        return sub_id, q

    async def unsubscribe(self, sub_id: str):
        await self._send({"forget": sub_id})
        self._subs.pop(sub_id, None)

    async def get_proposal(
        self,
        symbol: str,
        contract_type: str,
        stake: float,
        duration: int,
        duration_unit: str,
        barrier: str | None = None,
    ) -> dict:
        payload = {
            "proposal":        1,
            "amount":          stake,
            "basis":           "stake",
            "contract_type":   contract_type,
            "currency":        "USD",
            "duration":        duration,
            "duration_unit":   duration_unit,
            "symbol":          symbol,
        }
        if barrier is not None:
            payload["barrier"] = barrier
        resp = await self._send(payload)
        if resp.get("error"):
            raise RuntimeError(f"Proposal error: {resp['error']['message']}")
        return resp["proposal"]

    async def buy(self, proposal_id: str, price: float) -> dict:
        resp = await self._send({"buy": proposal_id, "price": price})
        if resp.get("error"):
            raise RuntimeError(f"Buy error: {resp['error']['message']}")
        return resp["buy"]

    async def sell(self, contract_id: int, price: float = 0) -> dict:
        resp = await self._send({"sell": contract_id, "price": price})
        if resp.get("error"):
            raise RuntimeError(f"Sell error: {resp['error']['message']}")
        return resp["sell"]

    async def get_open_contracts(self) -> list[dict]:
        resp = await self._send({"portfolio": 1})
        return resp.get("portfolio", {}).get("contracts", [])
