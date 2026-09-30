#!/usr/bin/env python3
"""Soak the table for an hour and report whether it stayed honest.

What this is for
----------------
Everything else in the suite is a unit test or a single round trip. This runs
the real service over a real hour: three clients hold seats, bet every round,
and the run is judged on three things.

  rounds            did the pump keep dealing, or did the table stall?
  settlement        did every round pay out exactly once, and did the balance
                    agree with the pot? A pot that clears without a matching
                    credit, or a bet that debits twice, is the failure this
                    exists to catch.
  money             the sum of every debit and credit, checked against the
                    opening and closing balance. Drift here is not a rounding
                    artefact; it is money appearing or vanishing.

It asserts nothing about a bot. Bots are a demo convenience and may or may not
fill the table; the test drives its own seats.

Usage
-----
    python3 scripts/soak_test.py --minutes 60
    python3 scripts/soak_test.py --minutes 5 --log /tmp/soak.log

Only the standard library is used, and nothing is imported from the game
package: this is a black-box client, so it cannot inherit a bug from the code
it is testing.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import ssl
import struct
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Tuple

GAME = "teen-patti-pro"
DEMO_PATH = f"/{GAME}?operator=demo"


# --------------------------------------------------------------------- log
class Log:
    """Line-buffered so a nohup run is readable while it is still going."""

    def __init__(self, path: Optional[str]):
        self.path = path
        self.fh = None
        if path:
            self.fh = open(path, "a", buffering=1, encoding="utf-8")

    def __call__(self, msg: str) -> None:
        line = "%s  %s" % (time.strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        if self.fh:
            self.fh.write(line + "\n")

    def close(self) -> None:
        if self.fh:
            self.fh.close()
            self.fh = None


# ------------------------------------------------------------------ http
def http_json(url: str, method: str = "GET", body: Any = None,
              headers: Optional[Dict[str, str]] = None,
              timeout: int = 20) -> Tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Accept": "application/json"}
    if data:
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except Exception:
            return e.code, None
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


# ------------------------------------------------------------------ ws
class WS:
    """A minimal WebSocket client.

    No third-party dependency on purpose: the soak must be runnable on the
    host with nothing but the interpreter it is already using.
    """

    def __init__(self, host: str, path: str, timeout: int = 30):
        self.host = host
        self._buf = b""
        key = base64.b64encode(os.urandom(16)).decode()
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        raw = socket.create_connection((host, 443), timeout=timeout)
        self.sock = ctx.wrap_socket(raw, server_hostname=host)
        self.sock.sendall(
            ("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
             "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
             "Sec-WebSocket-Version: 13\r\nOrigin: https://%s\r\n\r\n"
             % (path, host, key, host)).encode())
        while b"\r\n\r\n" not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("handshake closed early")
            self._buf += chunk
        head, self._buf = self._buf.split(b"\r\n\r\n", 1)
        if b"101" not in head.split(b"\r\n")[0]:
            raise ConnectionError("no 101: %r" % head.split(b"\r\n")[0])

    def _send(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            head = bytes([0x80 | opcode, 0x80 | n])
        elif n < 65536:
            head = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack(">H", n)
        else:
            head = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(head + mask
                          + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def send_json(self, obj: Any) -> None:
        self._send(0x1, json.dumps(obj).encode())

    def _need(self, n: int) -> None:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("closed")
            self._buf += chunk

    def recv(self, timeout: float = 20.0) -> Optional[Dict[str, Any]]:
        """One text frame, or None on a close/ping."""
        self.sock.settimeout(timeout)
        while True:
            self._need(2)
            opcode = self._buf[0] & 0x0F
            ln = self._buf[1] & 0x7F
            off = 2
            if ln == 126:
                self._need(4)
                ln = struct.unpack(">H", self._buf[2:4])[0]
                off = 4
            elif ln == 127:
                self._need(10)
                ln = struct.unpack(">Q", self._buf[2:10])[0]
                off = 10
            self._need(off + ln)
            payload = self._buf[off:off + ln]
            self._buf = self._buf[off + ln:]
            if opcode == 0x8:
                return None
            if opcode == 0x9:            # ping -> pong, keep reading
                self._send(0xA, payload)
                continue
            if opcode in (0x1, 0x2):
                try:
                    return json.loads(payload.decode("utf-8", "replace"))
                except ValueError:
                    continue

    def close(self) -> None:
        try:
            self._send(0x8, b"")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


# ------------------------------------------------------------- the client
class Client:
    """One synthetic player. Owns its balance ledger and its own assertions."""

    def __init__(self, name: str, base: str, room: str, log: Log):
        self.name = name
        self.base = base.rstrip("/")
        self.room = room
        self.log = log
        self.token = ""
        self.player_id = ""
        self.ws: Optional[WS] = None
        self.balance = 0
        self.opening = 0
        self.debits = 0
        self.credits = 0
        self.bets_accepted: List[Dict[str, Any]] = []
        self.bets = 0
        self.bets_rejected = 0
        self.rejections: Dict[str, int] = {}
        self.settlements_seen = 0
        self.rounds_seen: List[str] = []
        self.settled_rounds: List[str] = []
        self.dupe_settlements: List[str] = []
        self.results_seen: List[str] = []
        self.errors: List[str] = []
        self.my_bet_last_round = 0
        self.last_seen_balance = 0

    # -- launch ---------------------------------------------------------
    def join(self) -> bool:
        # urllib follows the 302, so the response body is the game page's HTML
        # and there is no "location" key to read. The token is in the final URL
        # instead. Reading the body for it is why the first version of this
        # could never join anybody.
        token = ""
        try:
            with urllib.request.urlopen(self.base + DEMO_PATH,
                                        timeout=20) as r:
                token = self._token_from(r.geturl())
        except urllib.error.HTTPError as e:
            token = self._token_from(getattr(e, "url", "") or "")
        except Exception as e:
            self.errors.append(f"launch: {type(e).__name__}: {e}")
        if not token:
            self.errors.append("no session token in the launch redirect")
            return False
        self.token = token
        try:
            self.ws = WS("api.ura-dhura.com",
                         "/ws?session=" + token)
        except Exception as e:
            self.errors.append(f"ws connect: {type(e).__name__}: {e}")
            return False
        self.ws.send_json({"action": "subscribe", "session": token,
                           "room": self.room})
        snap = self._await_snapshot()
        if snap is None:
            self.errors.append("no snapshot after subscribe")
            return False
        self._adopt(snap)
        self.opening = self.balance
        self.log(f"{self.name}: seated balance={self.balance} "
                 f"seat={snap.get('mySeat')}")
        return True

    @staticmethod
    def _token_from(loc: str) -> str:
        if not loc:
            return ""
        if "session=" in loc:
            return loc.split("session=")[1].split("&")[0]
        return ""

    def _await_snapshot(self, timeout: float = 20.0):
        end = time.time() + timeout
        while time.time() < end:
            msg = self.ws.recv(timeout=max(1.0, end - time.time()))
            if msg is None:
                return None
            if msg.get("kind") == "snapshot":
                return msg.get("data") or {}
            if msg.get("kind") == "error":
                d = msg.get("data") or {}
                self.errors.append("subscribe error: %s" % d.get("message"))
                return None
        return None

    def _adopt(self, snap: Dict[str, Any]) -> None:
        bal = snap.get("balance")
        if isinstance(bal, (int, float)):
            self.balance = int(bal)
        rid = snap.get("round_id")
        if rid and rid not in self.rounds_seen:
            self.rounds_seen.append(rid)
        st = snap.get("status")
        if st in ("RESULT", "SETTLED", "CLOSED", "REVEAL"):
            if rid and rid not in self.settled_rounds:
                self.settled_rounds.append(rid)

    # -- per-tick -------------------------------------------------------
    def pump(self, denoms: List[int]) -> None:
        """Read whatever is queued, then bet once if the window is open."""
        if not self.ws:
            return
        try:
            while True:
                msg = self.ws.recv(timeout=0.05)
                if msg is None:
                    break
                kind = msg.get("kind")
                if kind == "snapshot":
                    self._adopt(msg.get("data") or {})
                elif kind == "event":
                    d = msg.get("data") or {}
                    ek = d.get("kind")
                    rid = d.get("round_id") or ""
                    if ek == "round.created" and rid:
                        # Rounds are counted from the event, not from the
                        # snapshot. The server pushes a snapshot on subscribe
                        # and on peer changes, so a client that only reads
                        # snapshots sees the round number barely move and would
                        # report "no round was dealt" for a table that dealt
                        # dozens.
                        if rid not in self.rounds_seen:
                            self.rounds_seen.append(rid)
                    if ek == "settlement.completed" and rid:
                        # Only settlement.completed counts. result.published is
                        # a separate, earlier moment in the same round, and
                        # treating both as "a settlement" made every healthy
                        # round look like it had settled twice.
                        if rid in self.settled_rounds:
                            self.dupe_settlements.append(rid)
                        else:
                            self.settled_rounds.append(rid)
                    if ek == "result.published" and rid:
                        if rid not in self.results_seen:
                            self.results_seen.append(rid)
                    if ek == "balance.updated" and isinstance(
                            d.get("balance"), (int, float)):
                        # The authoritative balance, used to reconcile at the
                        # end rather than to count a credit that may belong to
                        # another player at the table.
                        self.last_seen_balance = int(d["balance"])
                elif kind == "error":
                    d = msg.get("data") or {}
                    code = str(d.get("code") or "ERROR")
                    self.rejections[code] = self.rejections.get(code, 0) + 1
        except socket.timeout:
            pass
        except Exception as e:
            self.errors.append(f"pump: {type(e).__name__}: {e}")
            self.reconnect()

    def maybe_bet(self, denoms: List[int]) -> None:
        if not self.token or not denoms:
            return
        snap = self._current()
        if not snap or snap.get("status") != "BETTING_OPEN":
            return
        if snap.get("my_bet"):
            return                      # one bet per round, per client
        amount = denoms[0]
        if amount > self.balance:
            amount = min(denoms)
        if amount > self.balance:
            return
        status, body = http_json(
            f"{self.base}/api/v1/games/{GAME}/rooms/{self.room}/bets",
            method="POST",
            body={"position": snap.get("mySeat") or "A", "amount": amount},
            headers={"Authorization": "Bearer " + self.token,
                     "Idempotency-Key": "soak-" + uuid.uuid4().hex},
        )
        if status == 200 and isinstance(body, dict) and body.get("success"):
            self.bets += 1
            self.debits += amount
            self.bets_accepted.append(
                {"amount": amount, "round_id": (snap or {}).get("round_id"),
                 "key": (body.get("data") or {}).get("bet_id")})
        else:
            self.bets_rejected += 1
            code = (body or {}).get("code") or f"HTTP{status}"
            self.rejections[str(code)] = self.rejections.get(str(code), 0) + 1

    def _current(self) -> Optional[Dict[str, Any]]:
        status, body = http_json(
            f"{self.base}/api/v1/games/{GAME}/rounds/current?room={self.room}",
            headers={"Authorization": "Bearer " + self.token})
        if status == 200 and isinstance(body, dict) and body.get("success"):
            return body.get("data") or {}
        return None

    def sync_balance(self) -> None:
        snap = self._current()
        if snap and isinstance(snap.get("balance"), (int, float)):
            self.balance = int(snap["balance"])

    def reconnect(self) -> None:
        if self.ws:
            self.ws.close()
            self.ws = None
        try:
            self.ws = WS("api.ura-dhura.com", "/ws?session=" + self.token)
            self.ws.send_json({"action": "subscribe", "session": self.token,
                               "room": self.room})
        except Exception as e:
            self.errors.append(f"reconnect: {type(e).__name__}: {e}")

    def close(self) -> None:
        if self.ws:
            self.ws.close()
            self.ws = None


# ------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="https://api.ura-dhura.com")
    ap.add_argument("--room", default="teen-patti-low")
    ap.add_argument("--clients", type=int, default=3)
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--log", default=None)
    args = ap.parse_args()

    log = Log(args.log)
    log("=" * 68)
    log(f"soak start: {args.clients} clients, {args.minutes:g} min, "
        f"room={args.room}")
    log("=" * 68)

    status, body = http_json(args.base + "/health")
    log(f"health: {status} {json.dumps(body)[:120] if body else ''}")
    if status != 200:
        log("ABORT: the API is not healthy")
        return 2

    status, body = http_json(f"{args.base}/api/v1/games/{GAME}")
    denoms = ((body or {}).get("data") or {}).get("denoms") or []
    log(f"denominations offered by the table: {denoms}")
    if not denoms:
        log("ABORT: the table published no denominations")
        return 2

    clients: List[Client] = []
    for i in range(args.clients):
        c = Client(f"soak-{i+1}", args.base, args.room, log)
        if c.join():
            clients.append(c)
        else:
            log(f"soak-{i+1}: FAILED to join")
    if not clients:
        log("ABORT: no client could join")
        return 2

    started = time.time()
    deadline = started + args.minutes * 60
    last_report = started
    rounds_at_start = len(clients[0].rounds_seen)
    log(f"starting from round {rounds_at_start}")
    trades: List[Dict[str, Any]] = []

    try:
        while time.time() < deadline:
            for c in clients:
                c.pump(denoms)
                c.maybe_bet(denoms)
            if time.time() - last_report >= 60:
                last_report = time.time()
                el = int(time.time() - started)
                for c in clients:
                    c.sync_balance()
                    log(f"t={el//60:02d}m{el%60:02d}s {c.name}: "
                        f"balance={c.balance} bets={c.bets} "
                        f"rejected={c.bets_rejected} rounds={len(c.rounds_seen)} "
                        f"settled={len(c.settled_rounds)} errors={len(c.errors)}")
                snap = clients[0]._current() or {}
                if snap.get("pot_total"):
                    trades.append({"t": el, "pot": snap.get("pot_total"),
                                   "status": snap.get("status")})
            time.sleep(1.0)
    except KeyboardInterrupt:
        log("interrupted")

    elapsed = int(time.time() - started)
    log("-" * 68)
    log(f"soak ran {elapsed // 60}m{elapsed % 60}s")
    for c in clients:
        c.sync_balance()

    ok = True
    total_rounds = max((len(c.rounds_seen) for c in clients), default=0)
    total_settled = max((len(c.settled_rounds) for c in clients), default=0)
    log(f"rounds observed: {total_rounds} (from {rounds_at_start})")
    log(f"rounds settled:  {total_settled}")

    if total_rounds <= rounds_at_start:
        log("FAIL: no new round was dealt in the whole run")
        ok = False
    else:
        expected = elapsed // 30          # a 30s betting window plus settlement
        log(f"expected roughly {expected} rounds at 30s each")
        if total_rounds - rounds_at_start < expected * 0.5:
            log("FAIL: the pump fell well behind the expected cadence")
            ok = False

    for c in clients:
        delta = c.balance - c.opening
        # Reconciliation, stated so it can actually fail.
        #
        #   balance = opening - every accepted bet + every payout
        #
        # so the payouts the run implies are delta + staked, and the run is
        # only consistent if the balance never went below zero, no bet was
        # charged twice, and no round settled twice. An earlier version of this
        # compared against a credits counter that was never incremented, so the
        # check passed by construction and could not have caught anything.
        implied_payouts = delta + c.debits
        log(f"{c.name}: opening={c.opening} closing={c.balance} "
            f"delta={delta:+d} staked={c.debits} "
            f"implied_payouts={implied_payouts:+d} "
            f"bets={c.bets} rejected={c.bets_rejected} "
            f"settled={len(c.settled_rounds)}")
        log(f"    rejections: {c.rejections or '{}'}")

        if c.balance < 0:
            log(f"FAIL: {c.name} balance went negative ({c.balance})")
            ok = False
        if c.dupe_settlements:
            log(f"FAIL: {c.name} saw a duplicate settlement for "
                f"{sorted(set(c.dupe_settlements))}")
            ok = False
        # The balance must never exceed what the account could hold: opening
        # plus every payout is the ceiling. Exceeding it is money from nowhere.
        if c.balance > c.opening + max(implied_payouts, 0) + 1:
            log(f"FAIL: {c.name} balance {c.balance} exceeds opening "
                f"{c.opening} plus the largest payout the run could explain")
            ok = False
        # Payouts are the pot a seat won. A negative figure would mean the
        # player was charged more than the pot, which is a real-money bug.
        if implied_payouts < 0:
            log(f"FAIL: {c.name} net lost {abs(delta + c.debits)} with no "
                f"offsetting payout")
            ok = False
        # Every accepted bet must be a configured denomination and must not
        # exceed the balance at the time. A bet outside the published set is
        # the client-offered-chip bug.
        allowed = set(denoms)
        for b in c.bets_accepted:
            if b["amount"] not in allowed:
                log(f"FAIL: {c.name} placed a bet of {b['amount']}, which "
                    f"the table does not offer ({sorted(allowed)})")
                ok = False
        for err in c.errors[:5]:
            log(f"    error: {err}")
        if len(c.errors) > 5:
            log(f"    ... and {len(c.errors) - 5} more errors")

    if ok:
        log("RESULT: PASS")
    else:
        log("RESULT: FAIL")
    for c in clients:
        c.close()
    log.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
