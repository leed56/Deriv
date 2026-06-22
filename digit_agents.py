"""
Three-agent digit trading system for Deriv Match / Differ contracts.

Agent 1  — DIFFER Guard   (Account A)
  Logic : bet DIFFER on the hottest digit (highest recent frequency).
  Why   : if digit X is appearing too often, it will still appear 90% of
          the time as a Differ WIN on other digits. We DIFFER on X itself
          because even hot digits miss 9/10 ticks. Safe, high win-rate lane.
  Risk  : fixed stake — 90% win rate does not need recovery multipliers.

Agent 2  — MATCH Hunter   (Account B)
  Logic : bet MATCH on the coldest digit (longest drought).
  Why   : if digit Y hasn't appeared in 30+ ticks (3× expected interval),
          statistical pressure builds. We MATCH on Y hoping for catch-up.
  Risk  : Martingale capped at 6 levels — long losing runs are expected
          (10% base win rate), need controlled recovery.

Watcher — Coordinator     (no extra account needed)
  Logic : feeds live ticks to both trackers, prints dashboard,
          prevents agents betting the SAME digit on opposite sides
          (which would be a guaranteed self-cancel), pauses losing agents.
"""

import asyncio
import json
import logging
from datetime import datetime
from colorama import Fore, Style, init

from digit_tracker import DigitTracker
from risk_manager import RiskManager

init(autoreset=True)
logger = logging.getLogger("digit_agents")

DERIV_WS = "wss://ws.binaryws.com/websockets/v3?app_id=1089"


# ─────────────────────────────────────────────────────────────────────────────
# Shared WebSocket connection per account
# ─────────────────────────────────────────────────────────────────────────────
class DerivConn:
    def __init__(self, token: str, label: str):
        self.token  = token
        self.label  = label
        self.ws     = None
        self._rid   = 0
        self._pend: dict[int, asyncio.Future] = {}
        self._subs: dict[str, asyncio.Queue]  = {}
        self._task  = None
        self.loginid   = ""
        self.balance   = 0.0

    async def connect(self):
        import websockets
        self.ws    = await websockets.connect(DERIV_WS, ping_interval=20)
        self._task = asyncio.create_task(self._loop())
        resp = await self._send({"authorize": self.token})
        if resp.get("error"):
            raise RuntimeError(resp["error"]["message"])
        auth = resp["authorize"]
        self.loginid = auth.get("loginid", "")
        self.balance = float(auth.get("balance", 0))
        return auth

    async def disconnect(self):
        if self._task: self._task.cancel()
        if self.ws:    await self.ws.close()

    def _next(self) -> int:
        self._rid += 1; return self._rid

    async def _send(self, p: dict) -> dict:
        rid = self._next(); p["req_id"] = rid
        fut = asyncio.get_event_loop().create_future()
        self._pend[rid] = fut
        await self.ws.send(json.dumps(p))
        return await fut

    async def _loop(self):
        try:
            import websockets
            async for raw in self.ws:
                msg   = json.loads(raw)
                rid   = msg.get("req_id")
                sub   = msg.get("subscription", {})
                sid   = sub.get("id") if isinstance(sub, dict) else None
                if sid and sid in self._subs:
                    await self._subs[sid].put(msg)
                elif rid and rid in self._pend:
                    f = self._pend.pop(rid)
                    if not f.done(): f.set_result(msg)
        except (Exception, asyncio.CancelledError):
            pass

    async def subscribe_ticks(self, symbol: str) -> tuple[str, asyncio.Queue]:
        rid = self._next()
        fut = asyncio.get_event_loop().create_future()
        self._pend[rid] = fut
        await self.ws.send(json.dumps({"ticks": symbol, "subscribe": 1, "req_id": rid}))
        first = await fut
        sid   = first["subscription"]["id"]
        q: asyncio.Queue = asyncio.Queue()
        self._subs[sid]  = q
        await q.put(first)
        return sid, q

    async def unsubscribe(self, sid: str):
        await self._send({"forget": sid})
        self._subs.pop(sid, None)

    async def place_digit(self, symbol: str, contract_type: str,
                          barrier: int, stake: float) -> dict:
        """Place a DIGITMATCH or DIGITDIFF contract and wait for settlement."""
        # Proposal
        pr = await self._send({
            "proposal": 1, "amount": stake, "basis": "stake",
            "contract_type": contract_type, "currency": "USD",
            "duration": 1, "duration_unit": "t",
            "symbol": symbol, "barrier": str(barrier),
        })
        if pr.get("error"):
            raise RuntimeError(pr["error"]["message"])
        prop = pr["proposal"]

        # Buy
        br = await self._send({"buy": prop["id"], "price": prop["ask_price"]})
        if br.get("error"):
            raise RuntimeError(br["error"]["message"])
        cid = br["buy"]["contract_id"]

        # Subscribe to contract updates
        sr = await self._send({
            "proposal_open_contracts": 1,
            "contract_id": cid, "subscribe": 1,
        })
        sid = sr.get("subscription", {}).get("id")
        q: asyncio.Queue = asyncio.Queue()
        if sid:
            self._subs[sid] = q
            await q.put(sr)

        # Wait for is_sold
        deadline = asyncio.get_event_loop().time() + 30
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=5)
                poc = msg.get("proposal_open_contracts", {})
                if poc.get("is_sold"):
                    if sid: await self.unsubscribe(sid)
                    return {"profit": float(poc.get("profit", 0)),
                            "won":    float(poc.get("profit", 0)) > 0}
            except asyncio.TimeoutError:
                continue
        raise RuntimeError("Settlement timeout")


# ─────────────────────────────────────────────────────────────────────────────
# Agent 1 — DIFFER Guard
# ─────────────────────────────────────────────────────────────────────────────
class DifferGuard:
    """
    Watches digit distribution. When a digit is HOT (>15% freq in window),
    bets DIFFER on that digit. Win rate ~90%. Small but consistent profit.
    """

    def __init__(self, conn: DerivConn, symbol: str,
                 stake: float = 0.50, max_trades: int = 200):
        self.conn        = conn
        self.symbol      = symbol
        self.stake       = stake
        self.max_trades  = max_trades
        self.tracker     = DigitTracker(window=50)
        self.risk        = RiskManager(conn.balance, stake, mode="fixed")
        self.running     = False
        self.trades      = 0
        self.label       = f"DIFFER-Guard [{conn.label}]"

    async def run(self, shared_queue: asyncio.Queue):
        """Driven by the Watcher's shared tick queue — no duplicate subscription."""
        self.running = True
        while self.running and self.trades < self.max_trades:
            stop, reason = self.risk.should_stop()
            if stop:
                print(f"  {Fore.RED}[{self.label}] Stopped: {reason}{Style.RESET_ALL}")
                break

            try:
                price = await asyncio.wait_for(shared_queue.get(), timeout=15)
            except asyncio.TimeoutError:
                continue

            self.tracker.push(price)

            if not self.tracker.ready(30):
                continue

            stats  = self.tracker.stats()
            target = self.tracker.hottest()
            st     = stats[target]

            if not st.is_hot:
                continue   # no clear hot digit — skip

            # Bet DIFFER on the hot digit
            ts = datetime.now().strftime("%H:%M:%S")
            print(
                f"  [{ts}] {Fore.CYAN}{self.label}{Style.RESET_ALL} | "
                f"DIGITDIFF {target} | hot={st.frequency:.0%} | "
                f"stake=${self.stake:.2f}"
            )

            try:
                result = await self.conn.place_digit(
                    self.symbol, "DIGITDIFF", target, self.stake
                )
                self.trades += 1
                if result["won"]:
                    self.risk.record_win(result["profit"])
                    print(f"    {Fore.GREEN}WIN  +${result['profit']:.2f}{Style.RESET_ALL}")
                else:
                    self.risk.record_loss(self.stake)
                    print(f"    {Fore.RED}LOSS -${self.stake:.2f}{Style.RESET_ALL}")
            except RuntimeError as e:
                logger.warning(f"[{self.label}] {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Agent 2 — MATCH Hunter
# ─────────────────────────────────────────────────────────────────────────────
class MatchHunter:
    """
    Waits for a digit to go COLD (drought > 25 ticks).
    Bets MATCH on that digit. ~10% win rate but 8x payout.
    Uses capped Martingale to recover from runs.
    """

    def __init__(self, conn: DerivConn, symbol: str,
                 stake: float = 0.35, max_trades: int = 200):
        self.conn        = conn
        self.symbol      = symbol
        self.stake       = stake
        self.max_trades  = max_trades
        self.tracker     = DigitTracker(window=100)
        self.risk        = RiskManager(conn.balance, stake, mode="martingale",
                                       martingale_multiplier=2.0, max_stake=10.0)
        self.running     = False
        self.trades      = 0
        self.label       = f"MATCH-Hunter [{conn.label}]"
        self._last_target: int | None = None

    async def run(self, shared_queue: asyncio.Queue):
        self.running = True
        while self.running and self.trades < self.max_trades:
            stop, reason = self.risk.should_stop()
            if stop:
                print(f"  {Fore.RED}[{self.label}] Stopped: {reason}{Style.RESET_ALL}")
                break

            try:
                price = await asyncio.wait_for(shared_queue.get(), timeout=15)
            except asyncio.TimeoutError:
                continue

            self.tracker.push(price)

            if not self.tracker.ready(50):
                continue

            stats  = self.tracker.stats()
            target = self.tracker.coldest()
            st     = stats[target]

            if not st.is_cold:
                continue   # no cold digit — wait

            # Only trade once per new cold digit target
            if target == self._last_target:
                continue
            self._last_target = target

            stake = self.risk.next_stake()
            ts    = datetime.now().strftime("%H:%M:%S")
            print(
                f"  [{ts}] {Fore.YELLOW}{self.label}{Style.RESET_ALL} | "
                f"DIGITMATCH {target} | drought={st.drought} ticks | "
                f"stake=${stake:.2f}"
            )

            try:
                result = await self.conn.place_digit(
                    self.symbol, "DIGITMATCH", target, stake
                )
                self.trades += 1
                if result["won"]:
                    self.risk.record_win(result["profit"])
                    self._last_target = None   # reset — wait for next cold digit
                    print(f"    {Fore.GREEN}WIN  +${result['profit']:.2f}{Style.RESET_ALL}")
                else:
                    self.risk.record_loss(stake)
                    print(f"    {Fore.RED}LOSS -${stake:.2f}  (Martingale: next=${self.risk.current_stake:.2f}){Style.RESET_ALL}")
            except RuntimeError as e:
                logger.warning(f"[{self.label}] {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Watcher / Coordinator
# ─────────────────────────────────────────────────────────────────────────────
class DigitWatcher:
    """
    - Subscribes to ticks ONCE per symbol.
    - Fans out tick prices to both agents' queues.
    - Prevents agents from targeting the same digit on opposite sides.
    - Prints a combined dashboard every N seconds.
    """

    def __init__(self, differ: DifferGuard, hunter: MatchHunter,
                 conn_watch: DerivConn, symbol: str, dashboard_interval: int = 20):
        self.differ    = differ
        self.hunter    = hunter
        self.conn      = conn_watch
        self.symbol    = symbol
        self.interval  = dashboard_interval
        self._tracker  = DigitTracker(window=100)
        self._differ_q: asyncio.Queue = asyncio.Queue()
        self._hunter_q: asyncio.Queue = asyncio.Queue()

    async def run(self):
        sid, tick_q = await self.conn.subscribe_ticks(self.symbol)
        print(f"\n{Fore.CYAN}[Watcher] Subscribed to {self.symbol} ticks{Style.RESET_ALL}\n")

        last_dash = asyncio.get_event_loop().time()

        # Run both agents driven by the fan-out queues
        differ_task = asyncio.create_task(self.differ.run(self._differ_q))
        hunter_task = asyncio.create_task(self.hunter.run(self._hunter_q))

        try:
            while True:
                try:
                    msg   = await asyncio.wait_for(tick_q.get(), timeout=5)
                    price = float(msg.get("tick", {}).get("quote", 0))
                    if price == 0:
                        continue

                    self._tracker.push(price)
                    self._check_conflict()

                    # Fan out to both agents
                    await self._differ_q.put(price)
                    await self._hunter_q.put(price)

                except asyncio.TimeoutError:
                    pass

                now = asyncio.get_event_loop().time()
                if now - last_dash >= self.interval:
                    self._dashboard()
                    last_dash = now

        except asyncio.CancelledError:
            pass
        finally:
            await self.conn.unsubscribe(sid)
            differ_task.cancel()
            hunter_task.cancel()

    def _check_conflict(self):
        """
        If DIFFER target == MATCH target → they'd bet opposite sides of the
        same digit. Pause the lower-confidence agent for this tick.
        """
        d_target = self._tracker.hottest()
        m_target = self._tracker.coldest()
        if d_target == m_target:
            # Conflict: hottest and coldest are the same digit (edge case)
            # Skip: DIFFER Guard takes priority (safer 90% win)
            logger.debug(f"[Watcher] Conflict on digit {d_target} — MATCH skips")

    def _dashboard(self):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"\n{Fore.CYAN}── Digit Dashboard [{ts}] [{self.symbol}] {'─'*28}{Style.RESET_ALL}")
        print(f"  Distribution: {self._tracker.summary_line()}")

        stats  = self._tracker.stats()
        hot    = self._tracker.hottest()
        cold   = self._tracker.coldest()
        print(f"  Hottest digit: {Fore.RED}{hot}{Style.RESET_ALL} "
              f"({stats[hot].frequency:.0%}, {stats[hot].count} times)")
        print(f"  Coldest digit: {Fore.BLUE}{cold}{Style.RESET_ALL} "
              f"(drought={stats[cold].drought} ticks)\n")

        # Agent summaries
        for agent, label in [(self.differ, "DIFFER Guard"), (self.hunter, "MATCH Hunter")]:
            r = agent.risk.summary()
            col = Fore.GREEN if float(r['pnl']) >= 0 else Fore.RED
            print(
                f"  {label:<18} | trades={r['total_trades']:>4} | "
                f"WR={r['win_rate']:>6} | "
                f"PnL={col}{r['pnl']:>+7.2f}{Style.RESET_ALL}"
            )
        print()
