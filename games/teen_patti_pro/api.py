#!/usr/bin/env python3
"""Teen Patti Pro REST API (stdlib only). Envelope on all responses.

Auth (player): Bearer <session_id|gst_session_token>.
Admin: X-Admin-Key header + role check (RBAC: roles in GAME_ADMIN_KEYS).
Provider (B2B): /api/v1/provider/*, /api/v1/teen-patti/*, /api/v1/wallet/*,
/api/v1/players/* — HMAC-SHA256 signed (X-API-Key, X-Timestamp, X-Nonce,
X-Signature), handled by provider/router.py.
"""
import argparse
import json
import logging
import os
import re
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from common import envelope as E
from common.admin_store import AdminStoreUnavailable
from common.session import TokenError
from .config import TeenPattiConfig, DEFAULT_CONFIG
from .service import TeenPattiService, ServiceError


def _load_admin_keys():
    """ADMIN_KEYS from env GAME_ADMIN_KEYS=key:role,... (+ legacy GAME_ADMIN_KEY).
    Sandbox fallback keeps the previous dev keys; production requires env keys."""
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
        keys = {"dev-admin-key": "admin",
                "dev-operator-key": "operator", "dev-auditor-key": "auditor"}
    return keys


def _load_admin_scopes(raw: str = ""):
    """Optional per-game grants: GAME_ADMIN_SCOPES=key:game1|game2,key2:game3.

    A key absent from this map, or an entry with an empty game list, is not
    restricted and may act on every game. Keys listed here may only act on the
    named games, whatever their role level.
    """
    scopes = {}
    for part in (raw or os.environ.get("GAME_ADMIN_SCOPES", "")).split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, games = part.split(":", 1)
        wanted = {g.strip().lower() for g in games.replace(";", "|").split("|") if g.strip()}
        if key.strip() and wanted:
            scopes[key.strip()] = wanted
    return scopes


ADMIN_KEYS = _load_admin_keys()
ADMIN_SCOPES = _load_admin_scopes()


def _new_id(prefix):
    """Collision-resistant id for a row the caller did not name."""
    import uuid
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def body_of(handler):
    """Parsed JSON body for a request, or {} when absent/unparseable.

    The deep-control router validates field by field and reports precise
    errors, so a body-level 422 here would pre-empt them.
    """
    try:
        data, err = parse_body(handler)
    except Exception:
        return {}
    if err or not isinstance(data, dict):
        return {}
    return data


def _int_arg(qs, name, default, low=None, high=None):
    """Read a bounded integer from a query dict, falling back to the default.

    A malformed or out-of-range value is clamped rather than rejected, so a
    typo in a dashboard URL shows a page instead of a 500.
    """
    raw = (qs.get(name, [None])[0] or "").strip()
    try:
        value = int(raw) if raw else int(default)
    except (TypeError, ValueError):
        value = int(default)
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


def _text_arg(qs, name, default="", max_len=64, pattern=None):
    """Read a bounded, optionally pattern-checked string from the query."""
    raw = str(qs.get(name, [default])[0] or "").strip()[:max_len]
    if pattern and raw and not re.fullmatch(pattern, raw):
        return default
    return raw


def parse_body(handler, max_bytes=1 << 20):
    """Read and JSON-decode the request body, at most once per request.

    The result is cached on the handler. Several routes probe the body before
    deciding which route they are (the deep-control router runs first), and
    rfile is a one-shot stream: a second read returns b"" and the real handler
    then blocked or validated against nothing.
    """
    cached = getattr(handler, "_parsed_body", None)
    if cached is not None:
        return cached
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError:
        return None, E.err("Bad Content-Length", E.E_VALIDATION)
    if length > max_bytes:
        return None, E.err("Body too large", E.E_VALIDATION)
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        result = (json.loads(raw or b"{}"), None)
    except (ValueError, UnicodeError):
        result = (None, E.err("Invalid JSON", E.E_VALIDATION))
    handler._parsed_body = result
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "TeenPattiPro/1.0"
    svc: TeenPattiService = None
    game_enabled: dict = {}  # game_id/alias -> bool (admin enable/disable)

    TEEN_IDS = {"teen-patti-pro", "teen_patti"}
    provider_ctx: object = None
    provider_tokens: object = None

    # Postgres-backed admin store, assigned in main(). Defaults to None so a
    # handler constructed directly (tests, tools) fails loudly through
    # admin_db_or_503() rather than raising AttributeError mid-route.
    admin_store = None

    # -- helpers --

    def admin_db_or_503(self):
        """Return the admin store, or send 503 and return None.

        Admin reads must come from the database. When none is configured the
        route answers 503 with the reason instead of returning zeros, because
        a dashboard of fabricated zeros is indistinguishable from a real
        trading day and would be acted on.
        """
        store = getattr(self, "admin_store", None)
        if store is None:
            from integrations.admin_db import build_admin_store
            store = build_admin_store()
            type(self).admin_store = store
        return store

    # -- deep-control plumbing -------------------------------------------
    #
    # Every admin read funnels through _admin_read and every write through
    # _admin_write so that "no database" produces one consistent 503 and one
    # consistent message, rather than each route inventing its own failure.

    def _admin_read(self, fn):
        """Run a store read and envelope it, or answer 503/502 honestly."""
        try:
            store = self.admin_db_or_503()
            return self.ok(fn(store))
        except AdminStoreUnavailable as exc:
            return self.send(503, E.err(str(exc), E.E_INTERNAL))
        except ServiceError as exc:
            return self.fail(exc)
        except Exception as exc:
            # The driver's message can name tables and columns, so report the
            # type only.
            return self.send(502, E.err(
                f"admin query failed: {type(exc).__name__}", E.E_INTERNAL))

    def _admin_post(self, path, body, query=""):
        """POST routes for the deep-control surface. None means "not mine".

        Kept as one method so the route table for the admin surface can be
        read in one place instead of being scattered through do_POST.
        """
        from common.profit_sim import simulate

        def need(role):
            return self.require_role(role)

        # --- profit & risk (save is PUT; POST here is only the simulator) ---
        if path == "/api/v1/admin/profit-risk/simulate":
            denied = need("auditor")
            if denied:
                return self.send(*denied)
            active = None
            try:
                active = self.admin_db_or_503().get_active_profit_risk()
            except AdminStoreUnavailable as exc:
                return self.send(503, E.err(str(exc), E.E_INTERNAL))
            except Exception:
                active = None
            cfg = dict(active or {})
            cfg.update({k: v for k, v in (body or {}).items() if v is not None})
            rounds = int(body.get("rounds", 10000) or 10000)
            return self.ok(simulate(cfg, rounds=rounds))
        if path == "/api/v1/admin/scheduled-changes/apply":
            # Manual trigger. The background sweeper normally does this on a
            # timer; an operator who just scheduled a change for "now" should
            # not have to wait a minute to see it land.
            denied = need("admin")
            if denied:
                return self.send(*denied)
            from common.config_scheduler import apply_due_changes
            return self._admin_read(
                lambda st: {"report": apply_due_changes(st)})
        if path == "/api/v1/admin/scheduled-changes":
            denied = need("admin")
            if denied:
                return self.send(*denied)
            return self._admin_write(
                lambda st: st.create_scheduled_change(
                    change_id=body.get("change_id") or _new_id("chg"),
                    target_type=str(body.get("target_type", "")),
                    target_id=str(body.get("target_id", "")),
                    payload=body.get("payload") or {},
                    effective_at=str(body.get("effective_at", "")),
                    created_by=self.admin_role() or "admin",
                    reason=str(body.get("reason", ""))),
                audit_action="config.schedule", audit_entity="scheduled_change",
                before=body)
        # --- player overrides ---
        if path == "/api/v1/admin/player-overrides":
            denied = need("admin")
            if denied:
                return self.send(*denied)
            return self._admin_write(
                lambda st: st.create_player_override(
                    override_id=body.get("override_id") or _new_id("ovr"),
                    player_id=str(body.get("player_id", "")),
                    house_edge_pct=body.get("house_edge_pct"),
                    token_delta=int(body.get("token_delta", 0) or 0),
                    custom_loss_limit=body.get("custom_loss_limit"),
                    expires_at=body.get("expires_at"),
                    reason=str(body.get("reason", "")),
                    actor=self.admin_role() or "admin"),
                audit_action="player.override", audit_entity="player",
                audit_entity_id=str(body.get("player_id", "")), before=body)
        # --- token packages ---
        if path == "/api/v1/admin/packages":
            denied = need("admin")
            if denied:
                return self.send(*denied)
            return self._admin_write(
                lambda st: st.create_package(
                    package_id=str(body.get("package_id") or _new_id("pkg")),
                    name=str(body.get("name", "")),
                    coins=int(body.get("coins", 0) or 0),
                    price_minor=int(body.get("price_minor", 0) or 0),
                    currency=str(body.get("currency", "USD")),
                    bonus_percent=int(body.get("bonus_percent", 0) or 0),
                    bonus_coins=int(body.get("bonus_coins", 0) or 0),
                    is_active=bool(body.get("is_active", True)),
                    sort_order=int(body.get("sort_order", 0) or 0),
                    tags=body.get("tags") or []),
                audit_action="package.create", audit_entity="package", before=body)
        m = re.fullmatch(r"/api/v1/admin/games/([^/]+)/(enable|disable)", path)
        if m:
            denied = need("admin")
            if denied:
                return self.send(*denied)
            enabled = m.group(2) == "enable"
            return self._admin_write(
                lambda st: st.set_game_enabled(
                    m.group(1), enabled, status=body.get("status"),
                    message=str(body.get("message", ""))),
                audit_action=("game.enable" if enabled else "game.disable"),
                audit_entity="game", audit_entity_id=m.group(1),
                before={"enabled": enabled})
        return None

    def _admin_put(self, path, body):
        """PUT routes for the deep-control surface. None means "not mine"."""
        if path == "/api/v1/admin/profit-risk":
            denied = self.require_role("admin")
            if denied:
                return self.send(*denied)
            if not isinstance(body, dict) or not body:
                return self.send(422, E.err("profit-risk body required",
                                            E.E_VALIDATION))
            return self._admin_write(
                lambda st: st.save_profit_risk(body, self.admin_role() or "admin"),
                audit_action="profit_risk.update", audit_entity="profit_risk",
                audit_entity_id="default", before=body)
        m = re.fullmatch(r"/api/v1/admin/players/([^/]+)/appearance", path)
        if m:
            denied = self.require_role("admin")
            if denied:
                return self.send(*denied)
            return self._admin_write(
                lambda st: {"appearance": st.set_player_appearance(
                    m.group(1), body.get("appearance") or body,
                    actor=self.admin_role() or "admin")},
                audit_action="player.appearance.set", audit_entity="player",
                audit_entity_id=m.group(1), before=body)
        if path == "/api/v1/admin/profit-risk":
            denied = self.require_role("admin")
            if denied:
                return self.send(*denied)
            if not isinstance(body, dict) or not body:
                return self.send(422, E.err("profit-risk body required",
                                            E.E_VALIDATION))
            return self._admin_write(
                lambda st: st.save_profit_risk(body, self.admin_role() or "admin"),
                audit_action="profit_risk.update", audit_entity="profit_risk",
                audit_entity_id="default", before=body)
        m = re.fullmatch(r"/api/v1/admin/players/([^/]+)/appearance", path)
        if m:
            denied = self.require_role("admin")
            if denied:
                return self.send(*denied)
            return self._admin_write(
                lambda st: {"appearance": st.set_player_appearance(
                    m.group(1), body.get("appearance") or body,
                    actor=self.admin_role() or "admin")},
                audit_action="player.appearance.set", audit_entity="player",
                audit_entity_id=m.group(1), before=body)
        if path == "/api/v1/admin/settings":
            denied = self.require_role("admin")
            if denied:
                return self.send(*denied)
            if not isinstance(body, dict) or not body:
                return self.send(422, E.err("settings body required",
                                            E.E_VALIDATION))
            return self._admin_write(
                lambda st: {"settings": st.put_settings(
                    body, actor=self.admin_role() or "admin")},
                audit_action="settings.update", audit_entity="settings",
                before=body)
        m = re.fullmatch(r"/api/v1/admin/packages/([^/]+)", path)
        if m:
            denied = self.require_role("admin")
            if denied:
                return self.send(*denied)
            return self._admin_write(
                lambda st: st.update_package(m.group(1), **body),
                audit_action="package.update", audit_entity="package",
                audit_entity_id=m.group(1), before=body)
        return None

    def _admin_delete(self, path):
        """DELETE routes for the deep-control surface. None means "not mine".

        Deletes are soft everywhere: a package is archived and an override is
        revoked, because both can already be referenced by wallet
        transactions. Hard-deleting either would orphan money records.
        """
        m = re.fullmatch(r"/api/v1/admin/packages/([^/]+)", path)
        if m:
            denied = self.require_role("admin")
            if denied:
                return self.send(*denied)
            return self._admin_write(
                lambda st: {"archived": st.archive_package(m.group(1))},
                audit_action="package.archive", audit_entity="package",
                audit_entity_id=m.group(1))
        m = re.fullmatch(r"/api/v1/admin/scheduled-changes/([^/]+)", path)
        if m:
            denied = self.require_role("admin")
            if denied:
                return self.send(*denied)
            return self._admin_write(
                lambda st: {"cancelled": st.cancel_scheduled_change(m.group(1))},
                audit_action="config.schedule.cancel",
                audit_entity="scheduled_change", audit_entity_id=m.group(1))
        m = re.fullmatch(r"/api/v1/admin/player-overrides/([^/]+)", path)
        if m:
            denied = self.require_role("admin")
            if denied:
                return self.send(*denied)
            return self._admin_write(
                lambda st: {"revoked": st.revoke_player_override(
                    m.group(1), self.admin_role() or "admin")},
                audit_action="player.override.revoke", audit_entity="player_override",
                audit_entity_id=m.group(1))
        return None

    def _admin_write(self, fn, audit_action=None, audit_entity=None,
                     audit_entity_id=None, before=None):
        """Run a store write, audit it, and envelope the result.

        The audit row is written by the caller's caller -- the store's own
        tables are the durable record; this keeps the in-process audit log
        consistent with the other admin routes.
        """
        try:
            store = self.admin_db_or_503()
            result = fn(store)
            if audit_action:
                try:
                    self.svc.audit.record(
                        self.admin_role() or "admin", audit_action,
                        audit_entity or "admin", str(audit_entity_id or ""),
                        before={"args": repr(before)[:400]} if before is not None
                        else None,
                        after={"ok": True})
                except Exception:
                    # Never let an audit failure mask a successful write; the
                    # store's own tables remain the durable record.
                    pass
            return self.ok(result)
        except AdminStoreUnavailable as exc:
            return self.send(503, E.err(str(exc), E.E_INTERNAL))
        except ServiceError as exc:
            return self.fail(exc)
        except KeyError as exc:
            return self.send(404, E.err(f"Not found: {exc}", E.E_NOT_FOUND))
        except Exception as exc:
            return self.send(502, E.err(
                f"admin write failed: {type(exc).__name__}", E.E_INTERNAL))

    def game_kind(self, game_id: str):
        return "teen" if game_id in self.TEEN_IDS else None

    def game_service(self, game_id: str):
        return self.svc if self.game_kind(game_id) == "teen" else None

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
    def send(self, status, payload, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def ok(self, data=None, message="OK"):
        self.send(200, E.ok(data, message))

    def fail(self, exc: ServiceError):
        table = {E.E_AUTH: 401, E.E_FORBIDDEN: 403, E.E_NOT_FOUND: 404,
                 E.E_VALIDATION: 422, E.E_WINDOW_CLOSED: 409, E.E_INSUFFICIENT: 402,
                 E.E_DUPLICATE: 409, E.E_CONFLICT: 409, E.E_RATE_LIMIT: 429,
                 "INVALID_TOKEN": 401, E.E_TBC_BLOCKED: 403}
        headers = {}
        if getattr(exc, "retry_after", 0):
            headers["Retry-After"] = str(exc.retry_after)
        self.send(table.get(exc.code, 500),
                  E.err(str(exc), exc.code if exc.code in table else E.E_INTERNAL),
                  headers=headers)

    def raw_body(self, max_bytes=1 << 20) -> bytes:
        """Read the request body verbatim. The provider HMAC is computed over
        these exact bytes, so the body must never be re-serialized."""
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            return b""
        if length <= 0:
            return b""
        if length > max_bytes:
            raise ServiceError(E.E_VALIDATION, "Body too large")
        return self.rfile.read(length)

    def serve_provider(self, method: str, path: str, query: str) -> bool:
        """Hand the B2B provider contract to provider/router.py. Returns True
        when the request was a provider route (handled or rejected there).

        When no provider context is configured the legacy routes keep serving
        /api/v1/games and /api/v1/sessions unchanged.
        """
        from provider.router import dispatch, is_provider_path
        if self.provider_ctx is None:
            return False
        # Only hand over paths the provider contract actually owns. Without this
        # guard every request was forwarded to the provider router whenever a
        # provider context existed, and any path outside its route table came
        # back 404 "no such provider route" -- which silently killed every local
        # admin/game route (whoami, round start, per-room wallet) on staging.
        if not is_provider_path(path):
            return False
        body = b""
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            try:
                body = self.raw_body()
            except ServiceError as exc:
                return self.send(413, E.err(str(exc), E.E_VALIDATION))
        status, headers, payload = dispatch(self.provider_ctx, method, path,
                                            query, self.headers, body)
        if "Location" in headers:
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True
        if isinstance(payload, str):
            raw = payload.encode()
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Type",
                             headers.get("Content-Type", "text/plain"))
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return True
        self.send(status, payload)
        return True

    def do_DELETE(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path
        try:
            if self.serve_provider("DELETE", url.path, url.query):
                return
            # ---- deep-control deletes (soft: archive/revoke, never hard) ----
            if path.startswith("/api/v1/admin/"):
                handled = self._admin_delete(path)
                if handled is not None:
                    return handled
            return self.send(404, E.err("Not found", E.E_NOT_FOUND))
        except ServiceError as exc:
            return self.fail(exc)

    def _provider_player(self, token: str):
        """Resolve a gst_ session token to its player (player-facing bearer)."""
        record = self._provider_token_record(token)
        return record.get("player_id") if record else None

    def session_player(self):
        auth = self.headers.get("Authorization", "")
        m = re.fullmatch(r"Bearer (\S+)", auth)
        if not m:
            return None
        sess = self.svc.sessions.get(m.group(1))
        if sess:
            return sess.player_id
        return self._provider_player(m.group(1))

    def session_identity_any(self):
        """Bearer identity plus the canonical game that issued the credential."""
        auth = self.headers.get("Authorization", "")
        m = re.fullmatch(r"Bearer (\S+)", auth)
        if not m:
            return None
        sid = m.group(1)
        sess = self.svc.sessions.get(sid)
        if sess:
            return {"player_id": sess.player_id, "game_id": sess.game_id,
                    "source": "session"}
        record = self._provider_token_record(sid)
        if record:
            return {"player_id": record.get("player_id"),
                    "game_id": record.get("game_code"),
                    "source": "session_token"}
        return None

    def session_player_any(self):
        """Player lookup for cross-game auth within the single game."""
        identity = self.session_identity_any()
        return identity["player_id"] if identity else None

    def _provider_token_record(self, token: str):
        """Resolve a gst_ session token without trusting client claims."""
        if self.provider_tokens is None or not token.startswith("gst_"):
            return None
        from provider.sessions import resolve as _resolve
        return _resolve(self.provider_tokens, token)

    def admin_role(self):
        return ADMIN_KEYS.get(self.headers.get("X-Admin-Key", ""))

    # RBAC hierarchy: admin > operator > auditor. Unknown or missing roles are
    # denied. Reads need auditor+, round operations need operator+, config
    # writes need admin.
    #
    # There is deliberately no superadmin tier. A role that overrides everything
    # is the one credential worth stealing, and with a single game there was
    # nothing left for it to be uniquely able to do. `admin` is the top role;
    # scope a key down with GAME_ADMIN_SCOPES instead of escalating to a
    # god-mode key.
    ROLE_LEVEL = {"auditor": 1, "operator": 2, "admin": 3}

    def admin_scopes(self):
        """Games this key may act on, or None when unrestricted."""
        key = self.headers.get("X-Admin-Key", "")
        return ADMIN_SCOPES.get(key)

    def require_role(self, minimum: str, game_id: str = ""):
        """Return (status, payload) denial, or None when authorized.

        When game_id is supplied and the key carries a per-game grant, the key
        must include that game (or one of its aliases) or it is denied.
        """
        role = self.admin_role()
        if role is None or self.ROLE_LEVEL.get(role, 0) < self.ROLE_LEVEL[minimum]:
            return (403, E.err(f"{minimum} role or higher required", E.E_FORBIDDEN))
        scopes = self.admin_scopes()
        if scopes and game_id:
            if str(game_id).lower() not in {s.lower() for s in scopes}:
                return (403, E.err(
                    f"key is not scoped to game {game_id}", E.E_FORBIDDEN))
        return None

    # -- admin game-configuration views (all games) --
    def _all_game_ids(self):
        return list(self.TEEN_IDS)

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
        view["seats"] = list(cfg.seats)
        view["guess_ms"] = cfg.guess_ms
        view["betting_duration_ms"] = cfg.guess_ms
        view["rake_bps"] = cfg.rake_bps
        return view

    def game_inventory(self, allowed=None) -> list:
        from provider.games import canonical_code
        wanted = None
        if allowed:
            wanted = set()
            for entry in allowed:
                code = canonical_code(entry)
                if code is not None:
                    wanted.add(code)
        out = []
        for gid in self._all_game_ids():
            svc = self.game_service(gid)
            if svc is None:
                continue
            v = self.game_config_view(gid, svc)
            if wanted is not None:
                from provider.games import canonical_code as _canonical
                if _canonical(v.get("game_id", gid)) not in wanted:
                    continue
            out.append({k: v[k] for k in ("game_id", "name", "status", "enabled",
                                          "config_version", "confirmed") if k in v})
        seen, uniq = set(), []
        for g in out:
            canonical = canonical_code(g["game_id"])
            dedupe = canonical if canonical is not None else g["game_id"]
            if dedupe not in seen:
                seen.add(dedupe)
                uniq.append(g)
        return uniq

    def apply_game_config(self, game_id: str, svc, patch: dict,
                           actor: str) -> dict:
        """Validated, audited admin config update.

        Teen Patti's ruleset is frozen: only ``enabled``, ``packages`` and
        ``localization`` may change. Free-form rule editing (min/max bet,
        denoms, durations, options) belonged to the wheel games and went with
        them. Changing a live game's payout maths now needs a code change and a
        config version bump, which is the safer default for a system that moves
        money.

        Unknown fields are a VALIDATION_ERROR. An optional ``reason`` key is a
        change-reference only -- never applied as configuration -- and is
        recorded in the audit trail beside the before/after values.
        """
        import time
        reason = patch.pop("reason", "")
        if reason is not None and not isinstance(reason, str):
            raise ServiceError(E.E_VALIDATION, "reason must be a string")
        before = self.game_config_view(game_id, svc)
        allowed_common = {"enabled", "packages", "localization"}
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
                          getattr(svc, "game_id", game_id), before=before,
                          after={"patch": patch, "reason": reason or "",
                                 "updated_by": actor,
                                 "applied_at_ms": int(time.time() * 1000)})
        return self.game_config_view(game_id, svc)

    # -- routing --
    CLIENT_DIR = Path(__file__).parent / "client"
    MASTER_DIR = Path(__file__).parent.parent.parent / "assets" / "dearlive-master"
    # Shared artwork (avatars, frames) is authored once under assets/games/ and
    # referenced by URL from the client and from admin_store defaults, so the
    # default avatar an operator never configures still resolves to a real file.
    ASSETS_DIR = Path(__file__).parent.parent.parent / "assets"
    ASSET_SUFFIXES = (".svg", ".png", ".webp", ".jpg", ".jpeg", ".json")
    ASSET_MIME = {".svg": "image/svg+xml", ".png": "image/png",
                  ".webp": "image/webp", ".jpg": "image/jpeg",
                  ".jpeg": "image/jpeg", ".json": "application/json"}
    MASTER_KINDS = {"lottie": ("application/json; charset=utf-8", ".json"),
                    "gif": ("image/gif", ".gif"),
                    "wav": ("audio/wav", ".wav")}

    def serve_repo_asset(self, rel: str):
        """Serve a file from the repo assets/ tree, read-only and contained.

        rel is untrusted: it comes straight off the URL. Rejecting "..", the
        absolute form and any symlink that escapes the root is what keeps this
        from becoming a filesystem read primitive. The extension is
        whitelisted so this cannot be used to fetch .env or .py from inside
        the tree.
        """
        if not rel or rel.endswith("/"):
            return self.send(404, E.err("Not found", E.E_NOT_FOUND))
        parts = rel.split("/")
        if any(p in ("..", ".", "") for p in parts) or rel.startswith("/"):
            return self.send(404, E.err("Not found", E.E_NOT_FOUND))
        target = (self.ASSETS_DIR / rel).resolve()
        try:
            target.relative_to(self.ASSETS_DIR.resolve())
        except ValueError:
            return self.send(404, E.err("Not found", E.E_NOT_FOUND))
        if target.suffix.lower() not in self.ASSET_SUFFIXES or not target.is_file():
            return self.send(404, E.err("Not found", E.E_NOT_FOUND))
        ctype = self.ASSET_MIME.get(target.suffix.lower(), "application/octet-stream")
        try:
            body = target.read_bytes()
        except OSError:
            return self.send(404, E.err("Not found", E.E_NOT_FOUND))
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Artwork changes only per release, so let the client hold it briefly.
        # Hashed filenames would allow immutable caching; without them a short
        # max-age is the safe compromise.
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        return self.wfile.write(body)

    # Client files are addressed by name from the URL, so the set is closed by
    # extension rather than by an ever-growing hardcoded list (lobby.html and
    # how-to-play.html were unreachable precisely because nobody added them to
    # such a list).
    CLIENT_SUFFIXES = {".html": "text/html; charset=utf-8",
                       ".js": "application/javascript; charset=utf-8",
                       ".json": "application/json; charset=utf-8",
                       ".css": "text/css; charset=utf-8",
                       ".svg": "image/svg+xml",
                       ".png": "image/png", ".webp": "image/webp",
                       ".ico": "image/x-icon"}

    def serve_client(self, name: str, ctype: str = ""):
        # name reaches here from the URL. Without the containment check below,
        # "..%2f..%2f.env" would read outside the client directory.
        target = (self.CLIENT_DIR / name).resolve()
        try:
            target.relative_to(self.CLIENT_DIR.resolve())
        except ValueError:
            return self.send(404, E.err("Not found", E.E_NOT_FOUND))
        if target.suffix.lower() not in self.CLIENT_SUFFIXES or not target.is_file():
            return self.send(404, E.err("Not found", E.E_NOT_FOUND))
        ctype = ctype or self.CLIENT_SUFFIXES[target.suffix.lower()]
        try:
            body = target.read_bytes()
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
            # ---- Zero-dependency demo bootstrap (never in production) ----
            # Gives a newcomer a playable table with no Redis, no provider keys
            # and no funded wallet. Everything runs against the in-process mock
            # stores. It mints a *real* launch token and opens a *real* session
            # on purpose: a fabricated session id would 401 on every downstream
            # route, and a hardcoded balance would still fail the engine's own
            # debit check. Nothing here is reachable when APP_ENV=production.
            if path == "/demo/session":
                if os.environ.get("APP_ENV", "sandbox").strip().lower() == "production":
                    return self.send(404, E.err("Not found", E.E_NOT_FOUND))
                room = qs.get("room", ["c-room"])[0]
                player = qs.get("player", ["demo-player"])[0]
                try:
                    fund = getattr(self.svc.wallet, "fund", None)
                    if fund is not None:
                        fund(player, 20000)
                    token = self.svc.tokens.mint(player, room, "teen-patti-pro")
                    sess = self.svc.open_session(token.token)
                    self.svc.ensure_round(room)
                    balance = self.svc.wallet.get_balance(player)
                    return self.ok({
                        "mode": "demo",
                        "session_id": sess["session_id"],
                        "player_id": player,
                        "room_id": room,
                        "balance": balance.available,
                        "currency": balance.currency,
                        "state": self.svc.state(room, player),
                        "note": "in-memory only: no Redis, no provider keys, "
                                "no settlement ledger rows",
                    })
                except ServiceError as exc:
                    return self.fail(exc)
            if self.serve_provider("GET", path, url.query):
                return
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
            if path == "/teen-patti-pro/asset-manifest.json":
                try:
                    body = (self.MASTER_DIR / "asset-manifest.json").read_bytes()
                except OSError:
                    return self.send(404, E.err("Not found", E.E_NOT_FOUND))
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return self.wfile.write(body)
            m = re.fullmatch(r"/teen-patti-pro/([A-Za-z0-9][A-Za-z0-9._-]*)", path)
            if m:
                return self.serve_client(m.group(1))
            if path.startswith("/assets/"):
                return self.serve_repo_asset(path[len("/assets/"):])
            m = re.fullmatch(r"/teen-patti-pro/assets/([A-Za-z0-9][A-Za-z0-9._-]*)", path)
            if m:
                name = m.group(1)
                if not name.endswith(".svg"):
                    return self.send(404, E.err("Not found", E.E_NOT_FOUND))
                return self.serve_client(f"assets/{name}", "image/svg+xml")
            m = re.fullmatch(r"/teen-patti-pro/master/([a-z]+)/([A-Za-z0-9][A-Za-z0-9._-]*)", path)
            if m:
                kind, name = m.group(1), m.group(2)
                spec = self.MASTER_KINDS.get(kind)
                if spec is None or not name.endswith(spec[1]):
                    return self.send(404, E.err("Not found", E.E_NOT_FOUND))
                ctype = spec[0]
                try:
                    body = (self.MASTER_DIR / "teen-patti-pro" / kind / name).read_bytes()
                except OSError:
                    return self.send(404, E.err("Not found", E.E_NOT_FOUND))
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                return self.wfile.write(body)
            if path == "/health":
                return self.ok({"game": "teen-patti-pro", "config": self.svc.config.version,
                                 "confirmed": self.svc.config.confirmed})
            if path == "/api/v1/games":
                from common.plugins import catalog, import_builtin_games
                import_builtin_games()
                games = catalog()
                for g in games:
                    g["slug"] = g["game_id"]
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
            if path == "/api/v1/wallet/balance":
                identity = self.session_identity_any()
                if identity is None:
                    return self.send(401, E.err("Bearer session required", E.E_AUTH))
                svc = self.game_service(identity.get("game_id", "teen-patti-pro"))
                if svc is None:
                    return self.send(404, E.err("Unknown game", E.E_NOT_FOUND))
                balance = svc.wallet.get_balance(identity["player_id"])
                return self.ok({"player_id": balance.player_id,
                                "available": balance.available,
                                "currency": balance.currency})
            m = re.fullmatch(r"/api/v1/games/teen-patti-pro/rounds/current", path)
            if m:
                room = qs.get("room", ["default"])[0]
                pid = self.session_player()
                if not pid:
                    return self.send(401, E.err("Bearer session required", E.E_AUTH))
                # Deliberately NOT calling _ensure_round() here. Bootstrapping a
                # round from a read made the table's own rounds/start fail
                # afterwards, because start_round() rejects a room that already
                # has a live round -- which broke the operator/platform ticker
                # that owns round creation. Round starts stay with the operator;
                # the bet path below is the only player-reachable safety net.
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
                from provider.games import TEEN_CODE, canonical_code
                identity = self.session_identity_any()
                if not identity:
                    return self.send(401, E.err("Bearer session required", E.E_AUTH))
                if canonical_code(identity.get("game_id")) != TEEN_CODE:
                    return self.send(403, E.err(
                        "session belongs to another game", E.E_FORBIDDEN))
                pid = identity["player_id"]
                bal = self.svc.wallet.get_balance(pid)
                return self.ok({"player_id": pid, "available": bal.available,
                                 "currency": bal.currency})
            m = re.fullmatch(r"/api/v1/games/(\S+)/rooms/(\S+)/wallet", path)
            if m and m.group(1) not in ("teen-patti-pro",):
                svc = self.game_check(m.group(1))
                if svc is None:
                    return
                from provider.games import canonical_code as _canonical_code
                requested = _canonical_code(m.group(1))
                admin_key = self.admin_role() is not None
                identity = self.session_identity_any()
                if identity is not None:
                    pid = identity["player_id"]
                    if _canonical_code(identity.get("game_id")) != requested:
                        return self.send(403, E.err(
                            "session belongs to another game", E.E_FORBIDDEN))
                elif admin_key:
                    denied = self.require_role("auditor", m.group(1))
                    if denied:
                        return self.send(*denied)
                    pid = urllib.parse.parse_qs(
                        urllib.parse.urlparse(self.path).query).get("player", [""])[0]
                    if not pid:
                        return self.send(422, E.err("player query is required",
                                                    E.E_VALIDATION))
                else:
                    return self.send(401, E.err("Bearer session required", E.E_AUTH))
                bal = svc.wallet.get_balance(pid)
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
            m = re.fullmatch(r"/api/v1/games/(\S+)/assets", path)
            if m:
                game_id = m.group(1)
                kind = self.game_kind(game_id)
                if kind is None:
                    return self.send(404, E.err(f"Unknown game {game_id}", E.E_NOT_FOUND))
                if kind == "teen":
                    try:
                        manifest = json.loads((self.CLIENT_DIR / "assets.json").read_bytes())
                        theme = json.loads((self.CLIENT_DIR / "theme.json").read_bytes())
                    except OSError:
                        return self.send(404, E.err("Asset manifest not bundled",
                                                    E.E_NOT_FOUND))
                    try:
                        master = json.loads((self.MASTER_DIR / "asset-manifest.json").read_bytes())
                    except OSError:
                        master = {"version": "missing"}
                    return self.ok({"game_id": "teen-patti-pro", "manifest": manifest,
                                    "theme": theme, "master": master})
                svc = self.game_check(game_id)
                if svc is None:
                    return
                cfg = svc.config
                return self.ok({"game_id": getattr(svc, "game_id", game_id),
                                "options": [{"option_id": o.option_id, "name": o.name,
                                             "icon": o.icon, "color_hex": o.color_hex,
                                             "hot": o.hot, "is_active": o.is_active,
                                             "multiplier": o.multiplier}
                                            for o in cfg.options],
                                "theme": {"denoms": list(cfg.denoms),
                                          "min_bet": cfg.min_bet, "max_bet": cfg.max_bet}})
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
            # --- admin reads (auditor role or higher) ---
            if path == "/api/v1/admin/whoami":
                denied = self.require_role("auditor")
                if denied:
                    return self.send(*denied)
                from provider.games import BINDINGS
                scopes = self.admin_scopes()
                return self.ok({"role": self.admin_role(),
                                 "games": sorted(scopes) if scopes else sorted(BINDINGS)})
            if path == "/api/v1/admin/config":
                denied = self.require_role("auditor")
                if denied:
                    return self.send(*denied)
                c = self.svc.config
                return self.ok({"version": c.version, "confirmed": c.confirmed,
                                 "tbc": list(c.tbc), "seats": list(c.seats),
                                 "denoms": list(c.denoms), "guess_ms": c.guess_ms,
                                 "rake_bps": c.rake_bps})
            if path == "/api/v1/admin/settlement-health":
                # Deliberately independent of the admin store. A round stuck in
                # SETTLED_PENDING exists only in engine memory, and this is the
                # one signal that must stay readable when Postgres is down --
                # folding it into the dashboard would hide stranded pots behind
                # a 503 at exactly the moment they matter.
                denied = self.require_role("auditor")
                if denied:
                    return self.send(*denied)
                try:
                    return self.ok(self.svc.settlement_health_report())
                except Exception as exc:
                    return self.send(500, E.err(
                        f"settlement health unavailable: {type(exc).__name__}",
                        E.E_INTERNAL))
            if path == "/api/v1/admin/dashboard":
                denied = self.require_role("auditor")
                if denied:
                    return self.send(*denied)
                store = self.admin_db_or_503()
                if store is None:
                    return self.send(503, E.err("admin store unavailable",
                                                E.E_INTERNAL))
                try:
                    kpis = store.dashboard_kpis()
                except AdminStoreUnavailable as exc:
                    return self.send(503, E.err(str(exc), E.E_INTERNAL))
                except Exception as exc:
                    return self.send(502, E.err(
                        f"admin dashboard query failed: {type(exc).__name__}",
                        E.E_INTERNAL))
                # Settlement health is live engine state, not a database fact:
                # a round stuck in SETTLED_PENDING exists only in memory until it
                # settles. It used to ride along on the old operator kpis route,
                # so it is preserved here rather than dropped with that route --
                # stranded pots must never be invisible.
                try:
                    kpis["settlement_health"] = self.svc.settlement_health_report()
                except Exception:
                    kpis["settlement_health"] = {"available": False}
                return self.ok(kpis)
            # ---- deep-control reads -------------------------------------
            # Every one of these is database-backed. With no DATABASE_URL the
            # store raises AdminStoreUnavailable and the route answers 503
            # with the reason, rather than returning an empty-but-successful
            # payload that would read as "there is nothing configured".
            if path == "/api/v1/admin/profit-risk":
                denied = self.require_role("auditor")
                if denied:
                    return self.send(*denied)
                return self._admin_read(
                    lambda st: {"active": st.get_active_profit_risk(),
                                "versions": st.list_profit_risk_versions(10)})
            if path == "/api/v1/admin/packages":
                denied = self.require_role("auditor")
                if denied:
                    return self.send(*denied)
                only_active = qs.get("active", ["false"])[0].lower() in ("1", "true", "yes")
                return self._admin_read(
                    lambda st: {"packages": st.list_packages(only_active)})
            if path == "/api/v1/admin/settings":
                denied = self.require_role("auditor")
                if denied:
                    return self.send(*denied)
                return self._admin_read(lambda st: {"settings": st.get_settings()})
            if path == "/api/v1/admin/scheduled-changes":
                denied = self.require_role("auditor")
                if denied:
                    return self.send(*denied)
                target_type = qs.get("target_type", [None])[0]
                target_id = qs.get("target_id", [None])[0]
                status = qs.get("status", [None])[0]
                limit = _int_arg(qs, "limit", 50, 1, 500)
                return self._admin_read(lambda st: {"changes": st.list_scheduled_changes(
                    target_type, target_id, status, limit)})
            if path == "/api/v1/admin/player-overrides":
                denied = self.require_role("auditor")
                if denied:
                    return self.send(*denied)
                player_id = qs.get("player_id", [None])[0]
                limit = _int_arg(qs, "limit", 50, 1, 500)
                return self._admin_read(
                    lambda st: {"overrides": st.list_player_overrides(player_id, limit)})
            m = re.fullmatch(r"/api/v1/admin/players/([^/]+)/appearance", path)
            if m:
                denied = self.require_role("auditor")
                if denied:
                    return self.send(*denied)
                return self._admin_read(
                    lambda st: {"appearance": st.get_player_appearance(m.group(1))})
            m = re.fullmatch(r"/api/v1/players/([^/]+)/appearance", path)
            if m:
                # Client-facing: an operator sets a player's look in the admin
                # panel, and the game reads it by player id. Unlike every
                # admin route, a missing database here is NOT a 503: this sits
                # in the table-rendering path, and an admin outage must not
                # blank out every player's avatar. Fall back to the default.
                try:
                    store = self.admin_db_or_503()
                    return self.ok({"appearance":
                                    store.get_player_appearance(m.group(1))})
                except Exception as exc:
                    # Logged, not swallowed: a blank avatar on every table is a
                    # real outage, and the operator has to be able to find it.
                    logging.getLogger("dearlive.api").warning(
                        "appearance lookup failed for %s, serving default: %s",
                        m.group(1), type(exc).__name__)
                    from common.admin_store import PostgresAdminStore
                    return self.ok({"appearance": dict(
                        PostgresAdminStore.DEFAULT_APPEARANCE),
                        "degraded": True})
            if path == "/api/v1/admin/audit":
                denied = self.require_role("auditor")
                if denied:
                    return self.send(*denied)
                return self.ok({"entries": self.svc.audit.list(
                    qs.get("entity", [""])[0], int(qs.get("limit", ["100"])[0]))})
            if path == "/api/v1/admin/webhooks":
                denied = self.require_role("auditor")
                if denied:
                    return self.send(*denied)
                return self.ok({"deliveries": self.svc.webhooks.deliveries[-100:]})
            if path == "/api/v1/admin/games":
                denied = self.require_role("auditor")
                if denied:
                    return self.send(*denied)
                return self.ok(self.game_inventory(self.admin_scopes()))
            m = re.fullmatch(r"/api/v1/admin/games/(\S+)/config", path)
            if m:
                denied = self.require_role("auditor")
                if denied:
                    return self.send(*denied)
                svc = self.game_service(m.group(1))
                if svc is None:
                    return self.send(404, E.err("Unknown game", E.E_NOT_FOUND))
                return self.ok(self.game_config_view(m.group(1), svc))

            # The operator dashboard routes that used to live here
            # (/api/v1/operator/admin/dashboard/{kpis,charts,summary}) were
            # removed: they answered 200 with hardcoded zeros for net profit,
            # revenue and pending withdrawals, which is indistinguishable from
            # a real trading day. Use GET /api/v1/admin/dashboard, which reads
            # the database and returns 503 when no database is configured.
            return self.send(404, E.err("Not found", E.E_NOT_FOUND))
        except ServiceError as exc:
            return self.fail(exc)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path
        try:
            if self.serve_provider("POST", path, url.query):
                return
            # ---- deep-control writes -------------------------------------
            if path.startswith("/api/v1/admin/"):
                handled = self._admin_post(path, body_of(self), url.query)
                if handled is not None:
                    return handled
            # --- Operator Auth (Phase 1) ---
            if path == "/api/v1/operator/auth":
                body, err = parse_body(self)
                if err:
                    return self.send(422, err)
                pin = body.get("pin", "")
                if not pin:
                    return self.send(422, E.err("PIN required", E.E_VALIDATION))
                # Check configuration BEFORE importing bcrypt. Importing first
                # meant a host with no PIN configured but a broken or missing
                # bcrypt raised ModuleNotFoundError, which dropped the
                # connection with no response at all.
                pin_hash = os.environ.get("OPERATOR_PIN_HASH", "")
                if not pin_hash:
                    # 501, not 500: this is an unconfigured optional feature, not
                    # a fault. PIN login is a convenience for humans; the
                    # supported integration path is an API key in GAME_ADMIN_KEYS.
                    return self.send(501, E.err(
                        "PIN login is not configured. Set OPERATOR_PIN_HASH to "
                        "enable it, or authenticate with an X-Admin-Key from "
                        "GAME_ADMIN_KEYS (see docs/INTEGRATION.md).",
                        E.E_VALIDATION))
                try:
                    import bcrypt
                except ImportError:
                    return self.send(501, E.err(
                        "PIN login needs the bcrypt package "
                        "(pip install -r requirements.txt). An X-Admin-Key from "
                        "GAME_ADMIN_KEYS needs no extra dependency.",
                        E.E_VALIDATION))
                try:
                    if not bcrypt.checkpw(pin.encode(), pin_hash.encode()):
                        return self.send(401, E.err("Invalid PIN", E.E_AUTH))
                except Exception:
                    return self.send(500, E.err("PIN verification failed", E.E_INTERNAL))
                # Generate the operator bearer token. HS256 via common.jwtx, so
                # the server keeps its standard-library-only dependency set
                # (PyJWT was never declared and the route 500'd on import).
                import time
                from common import jwtx as jwt
                token_secret = os.environ.get("OPERATOR_TOKEN_SECRET", "")
                if not token_secret:
                    return self.send(501, E.err(
                        "OPERATOR_TOKEN_SECRET is not set, so a session token "
                        "cannot be signed. Set it, or authenticate with an "
                        "X-Admin-Key instead (see docs/INTEGRATION.md).",
                        E.E_VALIDATION))
                now = int(time.time())
                expires_at = now + 24 * 3600          # 24h TTL
                payload = {
                    "scope": "operator",
                    "iat": now,
                    "exp": expires_at,
                    "iss": "dearlive-games",
                    "sub": "operator",
                }
                operator_token = jwt.encode(payload, token_secret,
                                            algorithm="HS256")
                return self.ok({
                    "operator_token": operator_token,
                    "expires_at": expires_at,
                    "scope": "operator"
                }, "Operator authenticated")
            
            # --- Operator Session Endpoints ---
            if path == "/api/v1/operator/sessions":
                # Verify operator token
                auth = self.headers.get("Authorization", "")
                if not auth.startswith("Bearer "):
                    return self.send(401, E.err("Operator token required", E.E_AUTH))
                token = auth.split(" ")[1]
                from common import jwtx as jwt
                token_secret = os.environ.get("OPERATOR_TOKEN_SECRET", "")
                if not token_secret:
                    return self.send(500, E.err("Operator token secret not configured", E.E_INTERNAL))
                try:
                    payload = jwt.decode(token, token_secret, algorithms=["HS256"])
                    if payload.get("scope") != "operator":
                        return self.send(403, E.err("Invalid token scope", E.E_FORBIDDEN))
                except jwt.ExpiredSignatureError:
                    return self.send(401, E.err("Token expired", E.E_AUTH))
                except jwt.InvalidTokenError:
                    return self.send(401, E.err("Invalid token", E.E_AUTH))
                
                body, err = parse_body(self)
                if err:
                    return self.send(422, err)
                
                # Create operator session
                game_slug = body.get("game_slug", "")
                currency = body.get("currency", "USD")
                lang = body.get("lang", "EN")
                return_url = body.get("return_url", "/")
                demo_balance = body.get("demo_balance")
                
                # Use the service's demo session store
                demo_store = getattr(self.svc, 'demo_store', None)
                if demo_store is None:
                    from common.session import DemoSessionStore
                    demo_store = DemoSessionStore()
                    self.svc.demo_store = demo_store
                
                # Get client IP
                client_ip = self.headers.get("X-Forwarded-For", self.client_address[0])
                
                session = demo_store.create(
                    game_slug=game_slug,
                    ip=self.headers.get("X-Forwarded-For", self.client_address[0]),
                    currency=currency,
                    lang=lang,
                    demo_balance=demo_balance
                )
                demo_token = f"demo-{session.session_id}"
                return self.ok({
                    "session_id": session.session_id,
                    "demo_token": demo_token,
                    "starting_balance": session.starting_balance,
                    "current_balance": session.current_balance,
                    "currency": session.currency,
                    "lang": session.lang,
                    "expires_at_ms": session.expires_at_ms,
                    "return_url": return_url
                }, "Demo session created")
            
            # The operator dashboard routes that used to live here
            # (/api/v1/operator/admin/dashboard/{kpis,charts,summary}) were
            # removed: they answered 200 with hardcoded zeros for net profit,
            # revenue and pending withdrawals, which reads exactly like a real
            # trading day. Use GET /api/v1/admin/dashboard instead -- it reads
            # the database and returns 503 when none is configured.
            #
            # Removing the summary branch also removed a duplicated copy of the
            # demo-session handler that had been sitting unreachable inside it,
            # after that branch's own return.
            
            m = re.fullmatch(r"/api/v1/demo/sessions/(\S+)", path)
            if m:
                session_id = m.group(1)
                demo_store = getattr(self.svc, 'demo_store', None)
                if demo_store is None:
                    return self.send(404, E.err("Demo session not found", E.E_NOT_FOUND))
                session = demo_store.get(m.group(1))
                if not session:
                    return self.send(404, E.err("Demo session not found", E.E_NOT_FOUND))
                return self.ok({
                    "session_id": session.session_id,
                    "game_slug": session.game_slug,
                    "starting_balance": session.starting_balance,
                    "current_balance": session.current_balance,
                    "currency": session.currency,
                    "lang": session.lang,
                    "status": session.status,
                    "rounds_played": session.rounds_played,
                    "expires_at_ms": session.expires_at_ms,
                    "created_at_ms": session.created_at_ms
                })
            
            m = re.fullmatch(r"/api/v1/demo/sessions/(\S+)/close", path)
            if m:
                session_id = m.group(1)
                demo_store = getattr(self.svc, 'demo_store', None)
                if demo_store is None:
                    return self.send(404, E.err("Demo session not found", E.E_NOT_FOUND))
                demo_store.close(session_id)
                return self.ok({"session_id": session_id, "status": "CLOSED"}, "Demo session closed")
            
            m = re.fullmatch(r"/api/v1/games/teen-patti-pro/rooms/(\S+)/rounds/start", path)
            if m:
                denied = self.require_role("operator", "teen-patti-pro")
                if denied:
                    return self.send(*denied)
                return self.ok(self.svc.start_round(m.group(1), self.admin_role()), "Round started")
            m = re.fullmatch(r"/api/v1/games/teen-patti-pro/rooms/(\S+)/rounds/close", path)
            if m:
                denied = self.require_role("operator", "teen-patti-pro")
                if denied:
                    return self.send(*denied)
                return self.ok(self.svc.close_betting(m.group(1)), "Betting closed")
            m = re.fullmatch(r"/api/v1/games/teen-patti-pro/rooms/(\S+)/rounds/result", path)
            if m:
                denied = self.require_role("operator", "teen-patti-pro")
                if denied:
                    return self.send(*denied)
                return self.ok(self.svc.publish_result(m.group(1)), "Result published")
            m = re.fullmatch(r"/api/v1/games/teen-patti-pro/rooms/(\S+)/rounds/settle", path)
            if m:
                denied = self.require_role("operator", "teen-patti-pro")
                if denied:
                    return self.send(*denied)
                return self.ok(self.svc.settle(m.group(1)), "Settled")
            # [^/]+ not \S+: \S+ is greedy and the trailing group is optional, so
            # "/rooms/qaZ/rounds/qaZ-r1/bets" matched with room_id =
            # "qaZ/rounds/qaZ-r1" (greedy wins before backtracking to the short
            # parse). That silently addressed a phantom room, which had no open
            # round, so every bet came back 409 BETTING_CLOSED.
            m = re.fullmatch(r"/api/v1/games/teen-patti-pro/rooms/([^/]+)/(?:rounds/([^/]+)/)?bets", path)
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
            if path == "/api/v1/sessions":
                body, err = parse_body(self)
                if err:
                    return self.send(422, err)
                token = body.get("launch_token", "")
                for svc in [self.svc]:
                    if svc is None:
                        continue
                    try:
                        return self.ok(svc.open_session(token),
                                       "Session opened")
                    except ServiceError:
                        continue
                return self.send(404, E.err("Unknown launch token",
                                            E.E_NOT_FOUND))
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
                denied = self.require_role("operator", m.group(1))
                if denied:
                    return self.send(*denied)
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
            # ---- deep-control writes (settings, packages) ----
            if path.startswith("/api/v1/admin/"):
                handled = self._admin_put(path, body_of(self))
                if handled is not None:
                    return handled
            m = re.fullmatch(r"/api/v1/admin/games/(\S+)/config", path)
            if m:
                denied = self.require_role("admin", m.group(1))
                if denied:
                    return self.send(*denied)
                # Captured for the audit record: apply_game_config attributes
                # the change to a role, not to a key, so a key that is rotated
                # later still leaves a readable actor behind.
                role = self.admin_role()
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


def _start_sweeper(interval_s: float = 1.0):
    """Close -> result -> settle expired betting windows in the background.

    The serverless deployment sweeps lazily per request; a long-lived server
    needs this so timed rounds settle without an external scheduler.
    """
    import threading
    import time as _time

    def loop():
        while True:
            try:
                Handler.svc.sweep()
            except Exception:
                pass
            _time.sleep(interval_s)

    thread = threading.Thread(target=loop, name="teen-patti-sweeper", daemon=True)
    thread.start()
    return thread


def _start_config_sweeper(store, interval_s: int = 60):
    """Apply due scheduled config changes on a timer.

    Started only when the admin store can actually reach a database: with no
    DATABASE_URL the sweep would log a warning every minute forever, which
    buries real errors and looks like a fault when nothing is wrong.

    Separate from _start_sweeper, which settles betting windows on a 1s tick.
    Config changes are rare and can wait a minute; round settlement cannot.
    """
    from common.admin_store import UnavailableAdminStore
    from common.config_scheduler import ScheduledChangeSweeper
    if isinstance(store, UnavailableAdminStore):
        return None
    return ScheduledChangeSweeper(store, interval_seconds=interval_s).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(__import__("os").environ.get("GAME_API_PORT", "5002")))
    ap.add_argument("--host", default=__import__("os").environ.get("GAME_API_HOST", "127.0.0.1"))
    ap.add_argument("--confirmed", action="store_true",
                    help="Run with TBC rules confirmed (dev/demo only, NOT real money)")
    ap.add_argument("--no-sweeper", action="store_true",
                    help="Disable the background round sweeper")
    ap.add_argument("--ws-port", type=int, default=int(os.environ.get("GAME_WS_PORT", "5003")),
                    help="WebSocket port served in-process (shares game state)")
    ap.add_argument("--no-ws", action="store_true",
                    help="Do not start the in-process WebSocket listener")
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
    Handler.game_enabled = {}
    Handler.game_packages = {}
    Handler.game_labels = {}
    # Admin reads come from Postgres. Without DATABASE_URL this is an
    # UnavailableAdminStore whose every method raises, so /api/v1/admin/*
    # answers 503 with a reason instead of serving invented zeros.
    from integrations.admin_db import build_admin_store
    Handler.admin_store = build_admin_store()
    from provider.context import build_context
    _ctx = build_context(Handler.svc, wallet,
                         base_url=os.environ.get("PROVIDER_PUBLIC_BASE_URL",
                                                 f"http://{args.host}:{args.port}"),
                         client_path=os.environ.get("PROVIDER_CLIENT_PATH",
                                                    "/teen-patti-pro/?session="))
    Handler.provider_ctx = _ctx
    Handler.provider_tokens = _ctx.tokens
    if not args.no_sweeper:
        _start_sweeper()
    _config_sweeper = None if args.no_sweeper else _start_config_sweeper(
        Handler.admin_store)
    if not args.no_ws:
        from .ws import start_background as _start_ws
        _start_ws(Handler.svc, Handler.provider_tokens, args.host, args.ws_port)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"TeenPattiPro API: http://{args.host}:{args.port} "
          f"(config {cfg.version}, confirmed={cfg.confirmed}, "
          f"provider={'on' if _ctx.keys else 'no-keys'}, "
          f"config-sweeper={'on' if _config_sweeper else 'off'})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if _config_sweeper is not None:
            _config_sweeper.stop()


if __name__ == "__main__":
    main()
