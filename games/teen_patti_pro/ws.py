#!/usr/bin/env python3
"""Minimal RFC6455 WebSocket server (stdlib only) for Teen Patti Pro push.

Protocol (JSON text frames):
  C->S {"action":"subscribe","session":"<session_id>"}
  C->S {"session_token":"gst_..."}            (provider session token)
  S->C {"kind":"snapshot","data":{...}}              (visibility-safe state)
  S->C {"kind":"event","data":{...round.tick|bet.accepted|...}}  (serverTime + seq)
  S->C {"kind":"error","data":{"code":...,"message":...}}
  Either side may send {"action":"ping"}/{"kind":"pong"} keepalive.

Bets are REST-only (safer: headers + idempotency keys). WS is push + snapshot.
Ticks: server broadcasts round.tick 1/s per room with an open round.
The room is always taken from the authenticated session, never from the client.
"""

PROVIDER_EVENTS = {
    "game.session.created": "game.session.created",
    "player.joined": "player.joined",
    "player.left": "player.left",
    "round.started": "game.started",
    "bet.accepted": "bet.placed",
    "bet.rejected": "bet.rejected",
    "betting.closed": "betting.closed",
    "result.published": "game.finished",
    "settlement.completed": "round.settled",
    "round.cancelled": "round.cancelled",
    "error": "game.error",
}
UNSUPPORTED_PROVIDER_EVENTS = (
    "turn.started", "player.folded", "show.requested",
)

from common.wire_events import ws_name  # noqa: E402  (needs PROVIDER_EVENTS above)
from games.teen_patti_pro.bot_manager import create_bot_manager
import argparse
import base64
import hashlib
import json
import logging
import socket
import struct
import threading
import time

log = logging.getLogger(__name__)
# The lifecycle clock has to be observable in production. With no handler
# Python drops everything below WARNING, so round transitions were invisible
# and a pump doing nothing looked identical to a healthy one.
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _accept(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def _send_frame(conn: socket.socket, text: str):
    data = text.encode()
    header = bytes([0x81])
    n = len(data)
    if n < 126:
        header += struct.pack("B", n)
    elif n < 65536:
        header += struct.pack("!BH", 126, n)
    else:
        header += struct.pack("!BQ", 127, n)
    conn.sendall(header + data)


def _recv_frame(conn: socket.socket):
    hdr = conn.recv(2)
    if len(hdr) < 2:
        return None
    fin_op, mask_len = hdr[0], hdr[1]
    if fin_op & 0x0F == 0x8:
        return None  # close
    length = mask_len & 0x7F
    if length == 126:
        length = struct.unpack("!H", conn.recv(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", conn.recv(8))[0]
    mask = conn.recv(4) if mask_len & 0x80 else b""
    payload = b""
    while len(payload) < length:
        chunk = conn.recv(length - len(payload))
        if not chunk:
            return None
        payload += chunk
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    if fin_op & 0x0F != 0x1:
        return None  # text only
    return payload.decode("utf-8", "replace")


def _looks_like_token(value: str) -> bool:
    """True for a provider session token, false for a plain session id.

    Session ids are opaque strings too, so we key off the minted prefix rather
    than trying to redeem every value: redeeming a session id would be a wasted
    lookup, and a wrong prefix should fall through to the session store.
    """
    return value.startswith("gst_")


# How long a disconnected player's seat is held before it is released.
#
# A release has to be delayed, not immediate: a phone dropping off WiFi mid
# round reconnects within seconds, and releasing the seat on the first dropped
# frame would take the player out of a round they are still in. The grace
# period is longer than a normal reconnect and shorter than a round, so an
# abandoned table empties without stranding anyone.
SEAT_GRACE_S = 45.0


class Hub:
    def __init__(self, svc, tokens=None):
        self.svc = svc
        self.tokens = tokens
        self.lock = threading.Lock()
        self.rooms = {}  # room_id -> set[(conn, player_id)]
        # Pending seat releases: (room_id, player_id) -> Timer
        self._pending_release = {}
        # Bot manager for demo mode
        self.bot_manager = create_bot_manager(svc) if svc else None

    def resolve_session(self, payload: dict):
        """Return (session, player_id) from a session id or provider token.

        The B2B shared secret is never accepted here; only a session id or a
        short-lived gst_ session token.

        The token may arrive as `session_token` (operator/SDK clients) or as
        `session` (our own player page, which forwards the `?session=` value
        the launch redirect handed it). Only `session_token` used to be
        redeemed, so every browser subscribe was rejected with
        "Unknown or expired session": the socket was open, so the HUD showed
        "live", but no seat, no snapshot and no round ever arrived -- which is
        exactly the "Waiting for players" report from the field. Both fields
        are now tried as a token first, then as a session id.
        """
        from provider.sessions import resolve as _resolve
        candidates = [str(payload.get("session_token") or "").strip(),
                      str(payload.get("session") or "").strip()]
        for value in candidates:
            if not value:
                continue
            if self.tokens is not None and _looks_like_token(value):
                record = _resolve(self.tokens, value)
                if record is not None:
                    session = self.svc.sessions.get(record["session_id"])
                    if session is not None:
                        return session, session.player_id
            session = self.svc.sessions.get(value)
            if session is not None:
                return session, session.player_id
        return None, ""
        session_id = str(payload.get("session") or token or "").strip()
        session = self.svc.sessions.get(session_id) if session_id else None
        if session is None:
            return None, ""
        return session, session.player_id

    def join(self, room, conn, player_id):
        with self.lock:
            self.rooms.setdefault(room, set()).add((conn, player_id))
            # The player is here, so any pending release for them is void. A
            # phone that reconnects inside the grace window keeps its seat and
            # its round, which is the whole point of the delay.
            self._cancel_release_locked(room, player_id)

    def _cancel_release_locked(self, room, player_id):
        key = (room, player_id)
        timer = self._pending_release.pop(key, None)
        if timer is not None:
            timer.cancel()

    def has_connection(self, room, player_id) -> bool:
        with self.lock:
            return any(pid == player_id
                       for _c, pid in self.rooms.get(room, set()))

    def leave(self, conn):
        """Drop a socket, and schedule the seat's release.

        The seat used to be held forever. Nothing called leave_table on
        disconnect, so every abandoned tab left a ghost member occupying a seat
        for the lifetime of the process. A demo table reached "full of ghosts"
        after a few refreshes, and because the bot fill stands down when it
        counts two or more players, the table then sat empty of bots and no
        longer looked joinable. The ghosts were also visible to real players as
        seated opponents who never acted.
        """
        released = []
        with self.lock:
            for room, members in list(self.rooms.items()):
                for m in [m for m in members if m[0] is conn]:
                    members.discard(m)
                    released.append((room, m[1]))
            for room, player_id in released:
                # A second tab for the same player is still connected, so the
                # seat must not be scheduled for release.
                if any(pid == player_id
                       for _c, pid in self.rooms.get(room, set())):
                    continue
                key = (room, player_id)
                if key in self._pending_release:
                    continue
                timer = threading.Timer(SEAT_GRACE_S,
                                        self._release_seat, args=(room, player_id))
                timer.daemon = True
                self._pending_release[key] = timer
                timer.start()
        return released

    def _release_seat(self, room, player_id):
        """Give up a seat whose player did not come back."""
        with self.lock:
            self._pending_release.pop((room, player_id), None)
            if any(pid == player_id for _c, pid in self.rooms.get(room, set())):
                return  # they came back
        try:
            self.svc.leave_table(room, player_id)
        except Exception:
            log.exception("teen_patti_seat_release_failed room=%s player=%s",
                          room, player_id)
            return
        try:
            if self.bot_manager is not None:
                self.bot_manager.on_player_leave(room, player_id)
        except Exception:
            log.exception("teen_patti_bot_leave_notify_failed room=%s", room)

    def push(self, room, event: dict):
        dead = []
        with self.lock:
            members = list(self.rooms.get(room, set()))
        for conn, _pid in members:
            try:
                _send_frame(conn, json.dumps({"kind": "event", "data": event}))
            except OSError:
                dead.append(conn)
        for conn in dead:
            self.leave(conn)

    def tick_loop(self):
        """Drive and publish every room once a second, forever.

        The pump is the only clock-driven transition in the game. It used to be
        wrapped in a bare `except Exception: pass`, which meant a failure here
        was invisible: the thread kept running, the page kept receiving
        round.tick frames, and no round ever advanced. Production sat on
        BETTING_OPEN with an expired deadline and no clue why.

        Both loops now log with full context -- room, round, phase, exception --
        and never swallow anything silently. No secrets, tokens or wallet
        values are logged: only identifiers and status.
        """
        while True:
            time.sleep(1)
            with self.lock:
                rooms = list(self.rooms)
            for room in rooms:
                try:
                    out = self.svc.pump(room)
                    if out and out.get("moved"):
                        log.info("teen_patti_round_advanced room=%s moved=%s",
                                 room, ",".join(out["moved"]))
                except Exception:
                    st = {}
                    try:
                        st = self.svc.state(room, "") or {}
                    except Exception:
                        pass
                    log.exception(
                        "teen_patti_pump_failed room=%s round_id=%s phase=%s "
                        "ts=%d",
                        room, st.get("round_id") or "-",
                        st.get("status") or "-", int(time.time() * 1000))
            for room in rooms:
                try:
                    st = self.svc.state(room, "")
                    if not st.get("round_id"):
                        continue
                    self.push(room, {"seq": -1, "kind": "round.tick",
                                     "serverTime": int(time.time() * 1000),
                                     "round_id": st.get("round_id"),
                                     "status": st.get("status"),
                                     "betting_end_at": st.get("betting_end_at")})
                except Exception:
                    log.exception("teen_patti_tick_push_failed room=%s", room)

def handle(conn: socket.socket, hub: Hub):
    try:
        req = conn.recv(4096).decode("latin-1")
        headers = {}
        for line in req.split("\r\n")[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        if "sec-websocket-key" not in headers:
            conn.close()
            return
        resp = ("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                "Connection: Upgrade\r\nSec-WebSocket-Accept: "
                + _accept(headers["sec-websocket-key"]) + "\r\n\r\n")
        conn.sendall(resp.encode())
        room, player = None, ""
        while True:
            msg = _recv_frame(conn)
            if msg is None:
                break
            try:
                m = json.loads(msg)
            except ValueError:
                continue
            if m.get("action") == "ping":
                _send_frame(conn, json.dumps({"kind": "pong"}))
            elif m.get("action") == "subscribe" or m.get("session_token"):
                sess, player = hub.resolve_session(m)
                if sess is None:
                    _send_frame(conn, json.dumps({"kind": "error", "data": {
                        "code": "UNAUTHENTICATED",
                        "message": "Unknown or expired session"}}))
                    continue
                room = sess.room_id  # server-authoritative, never client-supplied
                hub.svc.sessions.touch(sess.session_id)
                hub.join(room, conn, player)
                # Auto-claim a seat on subscribe so a player never has to find a
                # hidden API. Idempotent: reconnects keep their existing seat,
                # and a full table yields spectator instead of an error.
                # This goes through the service (not the room directly) so the
                # round bootstrap below runs on the very first connection --
                # previously the first player sat at a table with no round until
                # an operator ticker or the launch route happened to start one.
                try:
                    hub.svc.claim_seat(room, player, "auto")
                except Exception:
                    log.exception("teen_patti_subscribe_claim_failed room=%s", room)
                # Bootstrap a round when the table has seated players and none
                # is in flight. ensure_round is a no-op while a round is active,
                # so reconnecting players never disturb the current round.
                try:
                    out = hub.svc.ensure_round(room)
                    if out.get("started"):
                        log.info("teen_patti_round_bootstrapped room=%s "
                                 "round_id=%s", room, out.get("round_id"))
                except Exception:
                    log.exception("teen_patti_ensure_round_failed room=%s", room)

                # Notify bot manager of real player join (demo mode only)
                if hub.bot_manager:
                    hub.bot_manager.on_player_join(room, player, is_real=True)
                _send_frame(conn, json.dumps({
                    "kind": "snapshot",
                    "data": hub.svc.state(room, player)}))
                # Nudge everyone already watching so a round created by this
                # connection reaches the other seats immediately, not on the
                # next one-second tick.
                try:
                    with hub.lock:
                        others = [c for c, _p in hub.rooms.get(room, set())
                                  if c is not conn]
                    # Include balance in peer snapshots too
                    peer_state = hub.svc.state(room, player)
                    for other in others:
                        _send_frame(other, json.dumps({
                            "kind": "event",
                            "data": peer_state}))
                except Exception:
                    log.exception("teen_patti_peer_snapshot_failed room=%s", room)
    except OSError:
        pass
    finally:
        hub.leave(conn)
        try:
            conn.close()
        except OSError:
            pass


def start_background(svc, tokens=None, host="127.0.0.1", port=5003):
    """Serve WebSocket in the current process, sharing the REST service.

    Running the socket in its own process would give it a second in-memory
    game state, so the API server starts it in-process instead.
    """
    hub = Hub(svc, tokens)
    orig_fire = svc._fire

    def fanout(kind, data):
        ev = orig_fire(kind, data)
        room = data.get("room_id") or ""
        if room:
            # Three vocabularies travel together, deliberately:
            #   kind          internal, what skills and the event log use
            #   ws_event      SRS section 8 name, what a WebSocket client uses
            #   provider_event the live DearLive platform name
            # Renaming `kind` would break the existing client and the platform
            # integration; the SRS names are additive, so both hold.
            hub.push(room, {"seq": -1, "kind": kind,
                            "ws_event": ws_name(kind),
                            "provider_event": PROVIDER_EVENTS.get(kind, kind),
                            "serverTime": ev["serverTime"], **data})
        return ev

    svc._fire = fanout
    threading.Thread(target=hub.tick_loop, daemon=True).start()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(50)

    def accept_loop():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=handle, args=(conn, hub), daemon=True).start()

    threading.Thread(target=accept_loop, daemon=True).start()
    return hub, srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5003)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--api-port", type=int, default=5002)
    args = ap.parse_args()
    from .api import Handler
    from .config import DEFAULT_CONFIG
    from .service import TeenPattiService
    if Handler.svc is None:
        Handler.svc = TeenPattiService(config=DEFAULT_CONFIG)
    hub, srv = start_background(Handler.svc, getattr(Handler, "provider_tokens", None),
                                args.host, args.port)
    print(f"TeenPattiPro WS: ws://{args.host}:{args.port} (api :{args.api_port})", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
        srv.close()


if __name__ == "__main__":
    main()
