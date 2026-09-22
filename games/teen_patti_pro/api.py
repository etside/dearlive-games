#!/usr/bin/env python3
"""Teen Patti Pro REST API (stdlib only). Envelope on all responses.

Auth: Bearer <session_id> (issued by POST /sessions via launch-token redeem).
Admin: X-Admin-Key header + role check (RBAC stub: roles in ADMIN_KEYS).
"""
import argparse
import json
import re
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from common import envelope as E
from common.session import TokenError
from common.wallet import MemoryWallet
from .config import TeenPattiConfig, DEFAULT_CONFIG
from .service import TeenPattiService, ServiceError

ADMIN_KEYS = {"dev-admin-key": "admin", "dev-super-key": "superadmin"}


def parse_body(handler, max_bytes=1 << 20):
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError:
        return None, E.err("Bad Content-Length", E.E_VALIDATION)
    if length > max_bytes:
        return None, E.err("Body too large", E.E_VALIDATION)
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        return json.loads(raw or b"{}"), None
    except (ValueError, UnicodeError):
        return None, E.err("Invalid JSON", E.E_VALIDATION)


class Handler(BaseHTTPRequestHandler):
    server_version = "TeenPattiPro/1.0"
    svc: TeenPattiService = None

    # -- helpers --
    def send(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def ok(self, data=None, message="OK"):
        self.send(200, E.ok(data, message))

    def fail(self, exc: ServiceError):
        table = {E.E_AUTH: 401, E.E_FORBIDDEN: 403, E.E_NOT_FOUND: 404,
                 E.E_VALIDATION: 422, E.E_WINDOW_CLOSED: 409, E.E_INSUFFICIENT: 402,
                 E.E_DUPLICATE: 409, E.E_CONFLICT: 409, E.E_RATE_LIMIT: 429,
                 "INVALID_TOKEN": 401, E.E_TBC_BLOCKED: 403}
        self.send(table.get(exc.code, 500),
                  E.err(str(exc), exc.code if exc.code in table else E.E_INTERNAL))

    def session_player(self):
        auth = self.headers.get("Authorization", "")
        m = re.fullmatch(r"Bearer (\S+)", auth)
        if not m:
            return None
        sess = self.svc.sessions.get(m.group(1))
        return sess.player_id if sess else None

    def admin_role(self):
        return ADMIN_KEYS.get(self.headers.get("X-Admin-Key", ""))

    # -- routing --
    CLIENT_DIR = Path(__file__).parent / "client"

    def serve_client(self, name: str, ctype: str):
        try:
            body = (self.CLIENT_DIR / name).read_bytes()
        except OSError:
            return self.send(404, E.err("Not found", E.E_NOT_FOUND))
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Games are static + server-driven; no caching of the entry page.
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path, qs = url.path, urllib.parse.parse_qs(url.query)
        try:
            if path in ("/teen-patti-pro", "/teen-patti-pro/"):
                return self.serve_client("index.html", "text/html; charset=utf-8")
            if path == "/teen-patti-pro/game.js":
                return self.serve_client("game.js", "application/javascript; charset=utf-8")
            if path == "/teen-patti-pro/demo.html":
                return self.serve_client("demo.html", "text/html; charset=utf-8")
            if path == "/teen-patti-pro/demo_round.json":
                return self.serve_client("demo_round.json", "application/json; charset=utf-8")
            if path == "/health":
                return self.ok({"game": "teen-patti-pro", "config": self.svc.config.version,
                                 "confirmed": self.svc.config.confirmed})
            if path == "/api/v1/games":
                from common.plugins import catalog, import_builtin_games
                import_builtin_games()
                games = catalog()
                for g in games:
                    if g["game_id"] == "teen-patti-pro":
                        g["config_version"] = self.svc.config.version
                return self.ok(games)
            if path == "/api/v1/skills":
                from common.skills import HOOKS
                bus = getattr(self.svc, "skills", None)
                return self.ok({"hooks": list(HOOKS),
                                 "registered": bus.catalog() if bus else []})
            if path == "/api/v1/games/teen-patti-pro":
                c = self.svc.config
                return self.ok({"id": "teen-patti-pro", "seats": list(c.seats),
                                 "denoms": list(c.denoms), "min_bet": c.min_bet,
                                 "max_bet": c.max_bet, "guess_ms": c.guess_ms,
                                 "tbc": list(c.tbc), "confirmed": c.confirmed,
                                 "config_version": c.version})
            m = re.fullmatch(r"/api/v1/sessions/(\S+)", path)
            if m:
                s = self.svc.sessions.get(m.group(1))
                if not s:
                    return self.send(404, E.err("Unknown session", E.E_NOT_FOUND))
                return self.ok({"session_id": s.session_id, "player_id": s.player_id,
                                 "room_id": s.room_id, "game_id": s.game_id})
            m = re.fullmatch(r"/api/v1/games/teen-patti-pro/rounds/current", path)
            if m:
                room = qs.get("room", ["default"])[0]
                pid = self.session_player()
                if not pid:
                    return self.send(401, E.err("Bearer session required", E.E_AUTH))
                return self.ok(self.svc.state(room, pid))
            m = re.fullmatch(r"/api/v1/games/teen-patti-pro/rounds/(\S+)/result", path)
            if m:
                pid = self.session_player()
                if not pid:
                    return self.send(401, E.err("Bearer session required", E.E_AUTH))
                room = qs.get("room", ["default"])[0]
                st = self.svc.state(room, pid)
                if st.get("status") not in ("RESULT", "SETTLED", "CLOSED"):
                    return self.send(404, E.err("Result not published", E.E_NOT_FOUND))
                return self.ok({k: st[k] for k in ("round_id", "winners", "hands",
                                                    "pot_total", "config_version") if k in st})
            if path == "/api/v1/games/teen-patti-pro/history":
                pid = self.session_player()
                if not pid:
                    return self.send(401, E.err("Bearer session required", E.E_AUTH))
                room = qs.get("room", ["default"])[0]
                r = self.svc._room(room).round
                bets = [{"bet_id": b.bet_id, "round_id": b.round_id, "position": b.position,
                         "amount": b.amount, "status": b.status}
                        for b in (r.bets if r else []) if b.player_id == pid]
                return self.ok({"bets": bets})
            m = re.fullmatch(r"/api/v1/games/teen-patti-pro/rooms/(\S+)/wallet", path)
            if m:
                pid = self.session_player()
                if not pid:
                    return self.send(401, E.err("Bearer session required", E.E_AUTH))
                bal = self.svc.wallet.get_balance(pid)
                return self.ok({"player_id": pid, "available": bal.available,
                                 "currency": bal.currency})
            # --- admin reads ---
            if path == "/api/v1/admin/config":
                if not self.admin_role():
                    return self.send(403, E.err("Admin key required", E.E_FORBIDDEN))
                c = self.svc.config
                return self.ok({"version": c.version, "confirmed": c.confirmed,
                                 "tbc": list(c.tbc), "seats": list(c.seats),
                                 "denoms": list(c.denoms), "guess_ms": c.guess_ms,
                                 "rake_bps": c.rake_bps})
            if path == "/api/v1/admin/audit":
                if not self.admin_role():
                    return self.send(403, E.err("Admin key required", E.E_FORBIDDEN))
                return self.ok({"entries": self.svc.audit.list(
                    qs.get("entity", [""])[0], int(qs.get("limit", ["100"])[0]))})
            if path == "/api/v1/admin/webhooks":
                if not self.admin_role():
                    return self.send(403, E.err("Admin key required", E.E_FORBIDDEN))
                return self.ok({"deliveries": self.svc.webhooks.deliveries[-100:]})
            return self.send(404, E.err("Not found", E.E_NOT_FOUND))
        except ServiceError as exc:
            return self.fail(exc)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path
        try:
            if path == "/api/v1/sessions":
                body, err = parse_body(self)
                if err:
                    return self.send(422, err)
                try:
                    return self.ok(self.svc.open_session(body.get("launch_token", "")),
                                   "Session opened")
                except ServiceError as exc:
                    return self.fail(exc)
            m = re.fullmatch(r"/api/v1/games/teen-patti-pro/rooms/(\S+)/rounds/start", path)
            if m:
                if not self.admin_role():
                    return self.send(403, E.err("Admin key required", E.E_FORBIDDEN))
                return self.ok(self.svc.start_round(m.group(1), self.admin_role()), "Round started")
            m = re.fullmatch(r"/api/v1/games/teen-patti-pro/rooms/(\S+)/rounds/close", path)
            if m:
                if not self.admin_role():
                    return self.send(403, E.err("Admin key required", E.E_FORBIDDEN))
                return self.ok(self.svc.close_betting(m.group(1)), "Betting closed")
            m = re.fullmatch(r"/api/v1/games/teen-patti-pro/rooms/(\S+)/rounds/result", path)
            if m:
                if not self.admin_role():
                    return self.send(403, E.err("Admin key required", E.E_FORBIDDEN))
                return self.ok(self.svc.publish_result(m.group(1)), "Result published")
            m = re.fullmatch(r"/api/v1/games/teen-patti-pro/rooms/(\S+)/rounds/settle", path)
            if m:
                if not self.admin_role():
                    return self.send(403, E.err("Admin key required", E.E_FORBIDDEN))
                return self.ok(self.svc.settle(m.group(1)), "Settled")
            m = re.fullmatch(r"/api/v1/games/teen-patti-pro/rooms/(\S+)/(?:rounds/(\S+)/)?bets", path)
            if m:
                room_id = m.group(1)
                pid = self.session_player()
                if not pid:
                    return self.send(401, E.err("Bearer session required", E.E_AUTH))
                body, err = parse_body(self)
                if err:
                    return self.send(422, err)
                key = self.headers.get("Idempotency-Key", "")
                try:
                    return self.ok(self.svc.place_bet(
                        room_id, pid, body.get("position", ""),
                        int(body.get("amount", 0)), key), "Bet accepted")
                except (ValueError, TypeError):
                    return self.send(422, E.err("amount must be integer", E.E_VALIDATION))
                except ServiceError as exc:
                    return self.fail(exc)
            m = re.fullmatch(r"/api/v1/games/teen-patti-pro/rooms/(\S+)/reconnect", path)
            if m:
                body, err = parse_body(self)
                if err:
                    return self.send(422, err)
                try:
                    return self.ok(self.svc.reconnect(
                        body.get("session_id", ""), int(body.get("last_seen_seq", 0))))
                except (ValueError, TypeError):
                    return self.send(422, E.err("Bad reconnect params", E.E_VALIDATION))
                except ServiceError as exc:
                    return self.fail(exc)
            return self.send(404, E.err("Not found", E.E_NOT_FOUND))
        except ServiceError as exc:
            return self.fail(exc)

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5002)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--confirmed", action="store_true",
                    help="Run with TBC rules confirmed (dev/demo only, NOT real money)")
    args = ap.parse_args()
    cfg = TeenPattiConfig(confirmed=args.confirmed) if args.confirmed else DEFAULT_CONFIG
    Handler.svc = TeenPattiService(config=cfg)
    # Dev funds for manual testing only (real backend uses DearLive wallet).
    if isinstance(Handler.svc.wallet, MemoryWallet):
        pass
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"TeenPattiPro API: http://{args.host}:{args.port} "
          f"(config {cfg.version}, confirmed={cfg.confirmed})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
