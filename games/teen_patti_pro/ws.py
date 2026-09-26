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
import argparse
import base64
import hashlib
import json
import socket
import struct
import threading
import time

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


class Hub:
    def __init__(self, svc, tokens=None):
        self.svc = svc
        self.tokens = tokens
        self.lock = threading.Lock()
        self.rooms = {}  # room_id -> set[(conn, player_id)]

    def resolve_session(self, payload: dict):
        """Return (session, player_id) from a session id or provider token.

        The B2B shared secret is never accepted here; only a session id or a
        short-lived gst_ session token.
        """
        token = str(payload.get("session_token") or "").strip()
        if token and self.tokens is not None:
            from provider.sessions import resolve as _resolve
            record = _resolve(self.tokens, token)
            if record is None:
                return None, ""
            session = self.svc.sessions.get(record["session_id"])
            if session is None:
                return None, ""
            return session, session.player_id
        session_id = str(payload.get("session") or token or "").strip()
        session = self.svc.sessions.get(session_id) if session_id else None
        if session is None:
            return None, ""
        return session, session.player_id

    def join(self, room, conn, player_id):
        with self.lock:
            self.rooms.setdefault(room, set()).add((conn, player_id))

    def leave(self, conn):
        with self.lock:
            for members in self.rooms.values():
                for m in [m for m in members if m[0] is conn]:
                    members.discard(m)

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
        while True:
            time.sleep(1)
            with self.lock:
                rooms = list(self.rooms)
            for room in rooms:
                try:
                    st = self.svc.state(room, "")
                    if st.get("round") is None and st.get("round_id") is None:
                        continue
                    self.push(room, {"seq": -1, "kind": "round.tick",
                                     "serverTime": int(time.time() * 1000),
                                     "round_id": st.get("round_id"),
                                     "status": st.get("status"),
                                     "betting_end_at": st.get("betting_end_at")})
                except Exception:
                    pass


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
                _send_frame(conn, json.dumps({
                    "kind": "snapshot",
                    "data": hub.svc.state(room, player)}))
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
