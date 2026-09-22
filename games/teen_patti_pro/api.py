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
from .config import TeenPattiConfig, DEFAULT_CONFIG
from .service import TeenPattiService, ServiceError


def _load_admin_keys():
    """ADMIN_KEYS from env GAME_ADMIN_KEYS=key:role,... (+ legacy GAME_ADMIN_KEY).
    Sandbox fallback keeps the previous dev keys; production requires env keys."""
    import os
    keys = {}
    raw = os.environ.get("GAME_ADMIN_KEYS", "")
    for part in raw.split(","):
        part = part.strip()
        if part and ":" in part:
            k, r = part.split(":", 1)
            keys[k.strip()] = r.strip()
    single = os.environ.get("GAME_ADMIN_KEY", "")
    if single:
        keys.setdefault(single, "admin")
    if not keys and os.environ.get("APP_ENV", "sandbox").lower() != "production":
        keys = {"dev-admin-key": "admin", "dev-super-key": "superadmin"}
    return keys


ADMIN_KEYS = _load_admin_keys()


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
    wheels: dict = {}  # canonical wheel game_id -> WheelService
    game_enabled: dict = {}  # game_id/alias -> bool (admin enable/disable)

    TEEN_IDS = {"teen-patti-pro", "teen_patti"}
    WHEEL_ALIAS = {"greedy-monkey": "greedy-monkey", "greedy": "greedy-monkey",
                   "greedy_monkey": "greedy-monkey", "baby-king": "baby-king",
                   "baby_king": "baby-king", "animal-food-wheel": "baby-king",
                   "food-wheel": "baby-king", "food_wheel": "baby-king"}

    # -- helpers --
    def game_kind(self, game_id: str):
        if game_id in self.TEEN_IDS:
            return "teen"
        if game_id in self.WHEEL_ALIAS:
            return self.WHEEL_ALIAS[game_id]
        return None

    def game_service(self, game_id: str):
        kind = self.game_kind(game_id)
        if kind == "teen":
            return self.svc
        if kind in ("greedy-monkey", "baby-king"):
            return self.wheels.get(kind)
        return None

    def game_check(self, game_id: str):
        """Resolve service or send error. Returns service or None (sent)."""
        svc = self.game_service(game_id)
        if svc is None:
            self.send(404, E.err(f"Unknown game {game_id}", E.E_NOT_FOUND))
            return None
        if self.game_enabled.get(game_id, self.game_enabled.get(
                getattr(svc, "game_id", game_id), True)) is False:
            self.send(403, E.err(f"Game {game_id} disabled", E.E_FORBIDDEN))
            return None
        return svc

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

    def session_player_any(self):
        """Player lookup across teen + wheel session stores (cross-game auth)."""
        auth = self.headers.get("Authorization", "")
        m = re.fullmatch(r"Bearer (\S+)", auth)
        if not m:
            return None
        sid = m.group(1)
        sess = self.svc.sessions.get(sid)
        if sess:
            return sess.player_id
        for wsf in (self.wheels or {}).values():
            sess = wsf.sessions.get(sid)
            if sess:
                return sess.player_id
        return None

    def admin_role(self):
        return ADMIN_KEYS.get(self.headers.get("X-Admin-Key", ""))

    # -- admin game-configuration views (all games) --
    def _all_game_ids(self):
        ids = list(self.TEEN_IDS)
        ids += sorted((self.wheels or {}).keys())
        return ids

    def game_config_view(self, game_id: str, svc) -> dict:
        from common.plugins import catalog as _catalog, import_builtin_games
        import_builtin_games()
        meta = {g["game_id"]: g for g in _catalog()}.get(game_id, {})
        cfg = svc.config
        tbc = list(getattr(cfg, "tbc", ()))
        view = {"game_id": getattr(svc, "game_id", game_id),
                "name": meta.get("name", game_id),
                "status": meta.get("status", "live"),
                "enabled": self.game_enabled.get(game_id, True),
                "config_version": getattr(cfg, "version", ""),
                "confirmed": getattr(cfg, "confirmed", False),
                "tbc": tbc,
                "denoms": list(getattr(cfg, "denoms", ())),
                "min_bet": getattr(cfg, "min_bet", 0),
                "max_bet": getattr(cfg, "max_bet", 0),
                "packages": getattr(self, "game_packages", {}).get(game_id, []),
                "localization": getattr(self, "game_labels", {}).get(game_id, {})}
        if hasattr(cfg, "guess_ms"):  # teen-patti-pro
            view["seats"] = list(cfg.seats)
            view["guess_ms"] = cfg.guess_ms
            view["betting_duration_ms"] = cfg.guess_ms
            view["rake_bps"] = cfg.rake_bps
        else:  # wheel games
            view["round_duration_ms"] = cfg.round_duration_ms
            view["betting_duration_ms"] = cfg.betting_duration_ms
            view["auto_allowed"] = cfg.auto_allowed
            view["payout_rule"] = cfg.payout_rule
            view["options"] = [{"option_id": o.option_id, "name": o.name,
                                "weight": o.weight, "multiplier": o.multiplier,
                                "icon": o.icon, "color_hex": o.color_hex,
                                "hot": o.hot, "is_active": o.is_active}
                               for o in cfg.options]
        return view

    def game_inventory(self) -> list:
        out = []
        for gid in self._all_game_ids():
            svc = self.game_service(gid)
            if svc is None:
                continue
            v = self.game_config_view(gid, svc)
            out.append({k: v[k] for k in ("game_id", "name", "status", "enabled",
                                          "config_version", "confirmed") if k in v})
        seen, uniq = set(), []
        for g in out:
            if g["game_id"] not in seen:
                seen.add(g["game_id"])
                uniq.append(g)
        return uniq

    def apply_game_config(self, game_id: str, svc, patch: dict,
                          actor: str) -> dict:
        """Validated, audited admin config update. Teen config is frozen
        (only enabled/packages/localization may change); wheel config fields
        are mutable. Unknown fields -> VALIDATION_ERROR."""
        allowed_common = {"enabled", "packages", "localization"}
        kind = self.game_kind(game_id)
        if kind == "teen":
            extra = set(patch) - allowed_common
            if extra:
                raise ServiceError(E.E_VALIDATION,
                                   f"Teen Patti config frozen; mutable: {sorted(allowed_common)}")
        else:
            from games.wheel_common.service import WheelOption
            allowed = allowed_common | {"denoms", "min_bet", "max_bet",
                                        "round_duration_ms", "betting_duration_ms",
                                        "auto_allowed", "options"}
            extra = set(patch) - allowed
            if extra:
                raise ServiceError(E.E_VALIDATION, f"Unknown config fields: {sorted(extra)}")
            cfg = svc.config
            if "denoms" in patch:
                d = [int(x) for x in patch["denoms"]]
                if not d or any(x <= 0 for x in d):
                    raise ServiceError(E.E_VALIDATION, "denoms must be positive ints")
                cfg.denoms = tuple(d)
            if "min_bet" in patch:
                cfg.min_bet = int(patch["min_bet"])
            if "max_bet" in patch:
                cfg.max_bet = int(patch["max_bet"])
            if cfg.min_bet > cfg.max_bet:
                raise ServiceError(E.E_VALIDATION, "min_bet > max_bet")
            if "round_duration_ms" in patch:
                cfg.round_duration_ms = int(patch["round_duration_ms"])
            if "betting_duration_ms" in patch:
                cfg.betting_duration_ms = int(patch["betting_duration_ms"])
            if "auto_allowed" in patch:
                cfg.auto_allowed = bool(patch["auto_allowed"])
            if "options" in patch:
                opts, ids = [], set()
                for o in patch["options"]:
                    oid = str(o["option_id"])
                    if oid in ids:
                        raise ServiceError(E.E_VALIDATION, "duplicate option_id")
                    ids.add(oid)
                    if float(o.get("weight", 0)) <= 0 or float(o.get("multiplier", 0)) <= 0:
                        raise ServiceError(E.E_VALIDATION, "weight/multiplier must be > 0")
                    opts.append(WheelOption(oid, str(o.get("name", oid)),
                                            weight=float(o["weight"]),
                                            multiplier=float(o["multiplier"]),
                                            icon=str(o.get("icon", "")),
                                            color_hex=str(o.get("color_hex", "#ffffff")),
                                            hot=bool(o.get("hot", False)),
                                            is_active=bool(o.get("is_active", True))))
                if not opts:
                    raise ServiceError(E.E_VALIDATION, "options must not be empty")
                cfg.options = tuple(opts)
        if "enabled" in patch:
            self.game_enabled[game_id] = bool(patch["enabled"])
            self.game_enabled[getattr(svc, "game_id", game_id)] = bool(patch["enabled"])
        if "packages" in patch:
            self.game_packages = getattr(self, "game_packages", {})
            self.game_packages[getattr(svc, "game_id", game_id)] = patch["packages"]
        if "localization" in patch:
            self.game_labels = getattr(self, "game_labels", {})
            self.game_labels[getattr(svc, "game_id", game_id)] = patch["localization"]
        svc.audit.record(actor, "config.update", "game",
                         getattr(svc, "game_id", game_id), after=patch)
        return self.game_config_view(game_id, svc)

    # -- routing --
    CLIENT_DIR = Path(__file__).parent / "client"
    WHEEL_DIR = Path(__file__).parent.parent / "wheel_common"

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

    def serve_wheel(self):
        try:
            body = (self.WHEEL_DIR / "client.html").read_bytes()
        except OSError:
            return self.send(404, E.err("Not found", E.E_NOT_FOUND))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path, qs = url.path, urllib.parse.parse_qs(url.query)
        try:
            if path in ("/greedy-monkey", "/greedy-monkey/"):
                return self.serve_wheel()
            if path in ("/baby-king", "/baby-king/"):
                return self.serve_wheel()
            if path in ("/teen-patti-pro", "/teen-patti-pro/"):
                return self.serve_client("index.html", "text/html; charset=utf-8")
            if path == "/teen-patti-pro/game.js":
                return self.serve_client("game.js", "application/javascript; charset=utf-8")
            if path == "/teen-patti-pro/demo.html":
                return self.serve_client("demo.html", "text/html; charset=utf-8")
            if path == "/teen-patti-pro/demo_round.json":
                return self.serve_client("demo_round.json", "application/json; charset=utf-8")
            if path == "/teen-patti-pro/theme.json":
                return self.serve_client("theme.json", "application/json; charset=utf-8")
            if path == "/teen-patti-pro/assets.json":
                return self.serve_client("assets.json", "application/json; charset=utf-8")
            m = re.fullmatch(r"/teen-patti-pro/assets/([A-Za-z0-9][A-Za-z0-9._-]*)", path)
            if m:
                name = m.group(1)
                if not name.endswith(".svg"):
                    return self.send(404, E.err("Not found", E.E_NOT_FOUND))
                return self.serve_client(f"assets/{name}", "image/svg+xml")
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
            # --- cross-game contract: GET /api/v1/games/{gameId}/... ---
            m = re.fullmatch(r"/api/v1/games/(\S+)/rounds/current", path)
            if m and m.group(1) not in ("teen-patti-pro",):
                svc = self.game_check(m.group(1))
                if svc is None:
                    return
                room = qs.get("room", ["default"])[0]
                pid = self.session_player_any()
                if not pid:
                    return self.send(401, E.err("Bearer session required", E.E_AUTH))
                return self.ok(svc.state(room, pid))
            m = re.fullmatch(r"/api/v1/games/(\S+)/rounds/(\S+)/result", path)
            if m and not (m.group(1) == "teen-patti-pro"):
                svc = self.game_check(m.group(1))
                if svc is None:
                    return
                pid = self.session_player_any()
                if not pid:
                    return self.send(401, E.err("Bearer session required", E.E_AUTH))
                room = qs.get("room", ["default"])[0]
                st = svc.state(room, pid)
                if st.get("status") not in ("RESULT", "SETTLED", "CLOSED"):
                    return self.send(404, E.err("Result not published", E.E_NOT_FOUND))
                if st.get("game_id", "").startswith("teen") or "winners" in st:
                    keep = ("round_id", "winners", "hands", "pot_total",
                            "config_version")
                else:
                    keep = ("round_id", "winner", "recent", "config_version")
                return self.ok({k: st[k] for k in keep if k in st})
            m = re.fullmatch(r"/api/v1/games/(\S+)/history", path)
            if m and m.group(1) not in ("teen-patti-pro",):
                svc = self.game_check(m.group(1))
                if svc is None:
                    return
                pid = self.session_player_any()
                if not pid:
                    return self.send(401, E.err("Bearer session required", E.E_AUTH))
                room = qs.get("room", ["default"])[0]
                limit = int(qs.get("limit", ["50"])[0])
                if hasattr(svc, "history"):
                    return self.ok(svc.history(room, pid, limit))
                # teen-patti-pro service: current-round own bets
                r = svc._room(room).round
                bets = [{"bet_id": b.bet_id, "round_id": b.round_id,
                         "position": b.position, "amount": b.amount,
                         "status": b.status}
                        for b in (r.bets if r else []) if b.player_id == pid]
                return self.ok({"bets": bets[-limit:]})
            m = re.fullmatch(r"/api/v1/games/(\S+)/results/recent", path)
            if m:
                svc = self.game_check(m.group(1))
                if svc is None:
                    return
                pid = self.session_player_any()
                if not pid:
                    return self.send(401, E.err("Bearer session required", E.E_AUTH))
                room = qs.get("room", ["default"])[0]
                limit = int(qs.get("limit", ["20"])[0])
                if not hasattr(svc, "recent_results"):
                    return self.send(404, E.err("Recent results not supported here",
                                                E.E_NOT_FOUND))
                return self.ok(svc.recent_results(room, limit))
            # G3 tables state view (GET variant; tableId == room)
            m = re.fullmatch(r"/api/v1/games/(\S+)/tables/(\S+)/state", path)
            if m:
                game_id, table_id = m.group(1), m.group(2)
                if self.game_kind(game_id) != "teen":
                    return self.send(404, E.err("Tables API is Teen Patti only",
                                                E.E_NOT_FOUND))
                svc = self.game_check(game_id)
                if svc is None:
                    return
                pid = self.session_player_any()
                if not pid:
                    return self.send(401, E.err("Bearer session required", E.E_AUTH))
                st = svc.state(table_id, pid)
                st["players"] = sorted(svc._room(table_id).members.keys())
                return self.ok(st)
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
            if path == "/api/v1/admin/games":
                if not self.admin_role():
                    return self.send(403, E.err("Admin key required", E.E_FORBIDDEN))
                return self.ok(self.game_inventory())
            m = re.fullmatch(r"/api/v1/admin/games/(\S+)/config", path)
            if m:
                if not self.admin_role():
                    return self.send(403, E.err("Admin key required", E.E_FORBIDDEN))
                svc = self.game_service(m.group(1))
                if svc is None:
                    return self.send(404, E.err("Unknown game", E.E_NOT_FOUND))
                return self.ok(self.game_config_view(m.group(1), svc))
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
            # --- cross-game contract: POST /api/v1/games/{gameId}/... ---
            m = re.fullmatch(r"/api/v1/games/(\S+)/sessions", path)
            if m:
                svc = self.game_check(m.group(1))
                if svc is None:
                    return
                body, err = parse_body(self)
                if err:
                    return self.send(422, err)
                try:
                    return self.ok(svc.open_session(body.get("launch_token", "")),
                                   "Session opened")
                except ServiceError as exc:
                    return self.fail(exc)
            m = re.fullmatch(r"/api/v1/games/(\S+)/rounds/(\S+)/bets", path)
            if m:
                game_id, round_id = m.group(1), m.group(2)
                svc = self.game_check(game_id)
                if svc is None:
                    return
                pid = self.session_player_any()
                if not pid:
                    return self.send(401, E.err("Bearer session required", E.E_AUTH))
                body, err = parse_body(self)
                if err:
                    return self.send(422, err)
                key = self.headers.get("Idempotency-Key", "") or body.get(
                    "idempotencyKey", "")
                room = urllib.parse.parse_qs(
                    urllib.parse.urlparse(self.path).query).get("room", ["default"])[0]
                try:
                    if self.game_kind(game_id) == "teen":
                        cur = svc._room(room).round
                        if body.get("roundId", "") and cur is not None and \
                                body["roundId"] != cur.round_id:
                            return self.send(409, E.err("Stale roundId", E.E_CONFLICT))
                        if round_id not in ("current",) and cur is not None and \
                                round_id != cur.round_id:
                            return self.send(409, E.err("Stale roundId", E.E_CONFLICT))
                        return self.ok(svc.place_bet(
                            room, pid, body.get("position", ""),
                            int(body.get("amount", 0)), key), "Bet accepted")
                    opt = body.get("option_id", body.get("option",
                                  body.get("position", "")))
                    cur = svc._room(room).round
                    if round_id not in ("current",) and cur is not None and \
                            round_id != cur.round_id:
                        return self.send(409, E.err("Stale roundId", E.E_CONFLICT))
                    return self.ok(svc.place_bet(
                        room, pid, opt, int(body.get("amount", 0)), key),
                        "Bet accepted")
                except (ValueError, TypeError):
                    return self.send(422, E.err("amount must be integer", E.E_VALIDATION))
                except ServiceError as exc:
                    return self.fail(exc)
            # G3 tables API (tableId == room)
            m = re.fullmatch(r"/api/v1/games/(\S+)/tables/(\S+)/bets", path)
            if m:
                game_id, table_id = m.group(1), m.group(2)
                if self.game_kind(game_id) != "teen":
                    return self.send(404, E.err("Tables API is Teen Patti only",
                                                E.E_NOT_FOUND))
                svc = self.game_check(game_id)
                if svc is None:
                    return
                pid = self.session_player_any()
                if not pid:
                    return self.send(401, E.err("Bearer session required", E.E_AUTH))
                body, err = parse_body(self)
                if err:
                    return self.send(422, err)
                key = self.headers.get("Idempotency-Key", "") or body.get(
                    "idempotencyKey", "")
                try:
                    cur = svc._room(table_id).round
                    if body.get("roundId", "") and cur is not None and \
                            body["roundId"] != cur.round_id:
                        return self.send(409, E.err("Stale roundId", E.E_CONFLICT))
                    return self.ok(svc.place_bet(
                        table_id, pid, body.get("position", ""),
                        int(body.get("amount", 0)), key), "Bet accepted")
                except (ValueError, TypeError):
                    return self.send(422, E.err("amount must be integer", E.E_VALIDATION))
                except ServiceError as exc:
                    return self.fail(exc)
            m = re.fullmatch(r"/api/v1/games/(\S+)/tables/(\S+)/state", path)
            if m:
                game_id, table_id = m.group(1), m.group(2)
                if self.game_kind(game_id) != "teen":
                    return self.send(404, E.err("Tables API is Teen Patti only",
                                                E.E_NOT_FOUND))
                svc = self.game_check(game_id)
                if svc is None:
                    return
                # state is a GET-style view exposed here for table clients
                body, _ = parse_body(self)
                pid = self.session_player_any()
                if not pid:
                    return self.send(401, E.err("Bearer session required", E.E_AUTH))
                st = svc.state(table_id, pid)
                st["players"] = sorted(svc._room(table_id).members.keys())
                return self.ok(st)
            m = re.fullmatch(r"/api/v1/games/(\S+)/auto(bet|play)", path)
            if m:
                svc = self.game_check(m.group(1))
                if svc is None:
                    return
                if not hasattr(svc, "set_autobet"):
                    return self.send(404, E.err("Auto Bet not supported here",
                                                E.E_NOT_FOUND))
                pid = self.session_player_any()
                if not pid:
                    return self.send(401, E.err("Bearer session required", E.E_AUTH))
                body, err = parse_body(self)
                if err:
                    return self.send(422, err)
                room = urllib.parse.parse_qs(
                    urllib.parse.urlparse(self.path).query).get("room", ["default"])[0]
                try:
                    return self.ok(svc.set_autobet(
                        room, pid, body.get("option_id", body.get("option", "")),
                        int(body.get("amount", 0)), int(body.get("rounds", 1))),
                        "Auto Bet configured")
                except (ValueError, TypeError):
                    return self.send(422, E.err("Bad autobet params", E.E_VALIDATION))
                except ServiceError as exc:
                    return self.fail(exc)
            m = re.fullmatch(r"/api/v1/games/(\S+)/rooms/(\S+)/rounds/(start|close|result|settle)", path)
            if m and m.group(1) not in ("teen-patti-pro",):
                svc = self.game_check(m.group(1))
                if svc is None:
                    return
                if not self.admin_role():
                    return self.send(403, E.err("Admin key required", E.E_FORBIDDEN))
                op = m.group(3)
                try:
                    if op == "start":
                        return self.ok(svc.start_round(m.group(2), self.admin_role()),
                                       "Round started")
                    if op == "close":
                        return self.ok(svc.close_betting(m.group(2)), "Betting closed")
                    if op == "result":
                        return self.ok(svc.publish_result(m.group(2)), "Result published")
                    return self.ok(svc.settle(m.group(2)), "Settled")
                except ServiceError as exc:
                    return self.fail(exc)
            return self.send(404, E.err("Not found", E.E_NOT_FOUND))
        except ServiceError as exc:
            return self.fail(exc)

    def do_PUT(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path
        try:
            m = re.fullmatch(r"/api/v1/admin/games/(\S+)/config", path)
            if m:
                role = self.admin_role()
                if role != "superadmin":
                    return self.send(403, E.err("superadmin key required",
                                                E.E_FORBIDDEN))
                svc = self.game_service(m.group(1))
                if svc is None:
                    return self.send(404, E.err("Unknown game", E.E_NOT_FOUND))
                body, err = parse_body(self)
                if err:
                    return self.send(422, err)
                try:
                    return self.ok(self.apply_game_config(m.group(1), svc, body,
                                                          role),
                                   "Config updated")
                except ServiceError as exc:
                    return self.fail(exc)
            return self.send(404, E.err("Not found", E.E_NOT_FOUND))
        except ServiceError as exc:
            return self.fail(exc)

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(__import__("os").environ.get("GAME_API_PORT", "5002")))
    ap.add_argument("--host", default=__import__("os").environ.get("GAME_API_HOST", "127.0.0.1"))
    ap.add_argument("--confirmed", action="store_true",
                    help="Run with TBC rules confirmed (dev/demo only, NOT real money)")
    args = ap.parse_args()
    from common.config import Settings
    from integrations import build_stores
    settings = Settings.from_env()
    if args.port:
        pass
    else:
        args.port = settings.api_port
    cfg = TeenPattiConfig(confirmed=args.confirmed) if args.confirmed else DEFAULT_CONFIG
    if settings.is_production():
        errs = settings.validate_for_production()
        if errs:
            raise SystemExit(f"Refusing production boot, missing: {sorted(errs)}")
        if not cfg.confirmed and args.confirmed is False:
            raise SystemExit("Refusing production boot with unconfirmed (TBC) rules")
    wallet, tokens, sessions, idem, note = build_stores()
    Handler.svc = TeenPattiService(
        config=cfg, wallet=wallet, tokens=tokens, sessions=sessions,
        idempotency=idem,
        webhook_destinations=([settings.settlement_webhook_url]
                              if settings.settlement_webhook_url else []),
        webhook_secret=settings.webhook_secret or settings.settlement_signing_secret or "dev-secret")
    Handler.svc.admin_keys_note = note
    # Wheel games (G1 Greedy Monkey, G2 Baby King): same adapter selection,
    # independent service state per game.
    from games.wheel_common.configs import baby_king_config, greedy_config
    from games.wheel_common.service import WheelService
    Handler.wheels = {}
    for _mkcfg in (greedy_config, baby_king_config):
        _wc = _mkcfg()
        if args.confirmed:
            _wc.confirmed = True
        _w, _t, _s, _i, _n = build_stores()
        Handler.wheels[_wc.game_id] = WheelService(
            config=_wc, wallet=_w, tokens=_t, sessions=_s, idempotency=_i,
            webhook_destinations=([settings.settlement_webhook_url]
                                  if settings.settlement_webhook_url else []),
            webhook_secret=settings.webhook_secret or settings.settlement_signing_secret or "dev-secret")
    Handler.game_enabled = {}
    Handler.game_packages = {}
    Handler.game_labels = {}
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"TeenPattiPro API: http://{args.host}:{args.port} "
          f"(config {cfg.version}, confirmed={cfg.confirmed})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
