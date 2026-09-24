"""B2B provider API router (Teen Patti Pro, V1).

Transport-agnostic: it takes (method, path, query, headers, body) and returns
(status, headers, body bytes), so the same routes serve the standalone server,
the Vercel function and the tests. All game logic is delegated to the existing
TeenPattiService; this module only authenticates, validates, authorizes and
maps the provider contract onto it.
"""
import json
import os
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from common import envelope as E
from provider import auth as PA
from provider.games import (BINDINGS, LION_CODE, MONKEY_CODE, TEEN_CODE,
                             binding_for_code, binding_for_slug, canonical_code)
from provider.ledger import WalletError_
from provider.sessions import SessionTokenError, resolve as resolve_token
from provider.tables import (Table, TableCatalog, list_tables, room_status,
                             select_table, table_detail, seat_count)

PROVIDER_PREFIX = "/api/v1/provider"
GAME_CODE = TEEN_CODE  # default game when a request omits one
GAME_ID = "teen-patti-pro"
GAME_ALIASES = {"teen_patti_pro", "teen-patti-pro", "teenpatti", "teen_patti"}
PLAYER_ID_RE = re.compile(r"^[A-Za-z0-9._:@-]{1,128}$")
CURRENCY_RE = re.compile(r"^[A-Z]{2,12}$")
LANGUAGE_RE = re.compile(r"^[a-z]{2}(-[A-Za-z]{2,4})?$")
PLATFORM_RE = re.compile(r"^[a-z0-9_]{1,24}$")
MAX_BODY = 64 * 1024
DEFAULT_SESSION_TTL = 1800
DEFAULT_CLIENT_URL = "/teen-patti-pro/?session="


class ProviderError(Exception):
    def __init__(self, code: str, message: str, status: int = 422,
                 headers: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.headers = headers or {}


@dataclass
class ProviderContext:
    service: object
    wallet: object
    tokens: object
    catalog: TableCatalog
    nonces: object
    limiter: object
    keys: Dict[str, str] = field(default_factory=dict)
    base_url: str = ""
    currency: str = "COIN"
    session_ttl_s: int = DEFAULT_SESSION_TTL
    client_path: str = DEFAULT_CLIENT_URL
    redis: bool = False
    catalogs: Dict[str, TableCatalog] = field(default_factory=dict)
    wheels: Dict[str, object] = field(default_factory=dict)

    def attach_games(self, teen_service, wheels: Dict[str, object]):
        """Bind the wheel engines so one API can serve every game."""
        self.service = teen_service
        self.wheels = dict(wheels or {})
        return self

    def teen_service(self):
        return self.service

    def wheel_services(self):
        return self.wheels

    def session_stores(self):
        """Every session store this deployment serves.

        Each engine owns its own store, so a session id issued by a wheel must
        resolve there rather than in the Teen Patti store.
        """
        stores = []
        for service in [self.service] + list(self.wheels.values()):
            store = getattr(service, "sessions", None)
            if store is not None:
                stores.append(store)
        return stores

    def find_session(self, session_id: str):
        for store in self.session_stores():
            session = store.get(session_id)
            if session is not None:
                return session
        return None

    def catalog_for(self, code: str) -> TableCatalog:
        return self.catalogs.get(code) or self.catalog

    def client_path_for(self, code: str) -> str:
        try:
            from provider.context import client_path_for
            return client_path_for(code)
        except Exception:
            return self.client_path

    def audit(self, actor, action, entity, entity_id, after=None):
        log = getattr(self.service, "audit", None)
        if log is not None:
            log.record(actor, action, entity, entity_id, after=after or {})


@dataclass
class Request:
    method: str
    path: str
    query: Dict[str, str]
    headers: object
    body: bytes
    actor: str = ""

    def q(self, name: str, default: str = "") -> str:
        return self.query.get(name, default)

    def header(self, name: str, default: str = "") -> str:
        return PA.header(self.headers, name, default)

    def json_body(self, required: bool = True) -> dict:
        if not self.body:
            if required:
                raise ProviderError(E.E_VALIDATION, "JSON body is required")
            return {}
        try:
            data = json.loads(self.body)
        except (ValueError, UnicodeError):
            raise ProviderError(E.E_VALIDATION, "body is not valid JSON")
        if not isinstance(data, dict):
            raise ProviderError(E.E_VALIDATION, "body must be a JSON object")
        return data


def _iso(ms: int) -> str:
    import datetime
    stamp = datetime.datetime.fromtimestamp(int(ms) / 1000, datetime.timezone.utc)
    return stamp.isoformat().replace("+00:00", "Z")


def _text(value, field_name, max_len=128, required=True, pattern=None):
    text = str(value or "").strip()
    if required and not text:
        raise ProviderError(E.E_VALIDATION, f"{field_name} is required")
    if len(text) > max_len:
        raise ProviderError(E.E_VALIDATION, f"{field_name} is too long")
    if pattern is not None and text and not pattern.match(text):
        raise ProviderError(E.E_VALIDATION, f"{field_name} is malformed")
    return text


def _int(value, field_name, minimum=None, maximum=None):
    if isinstance(value, bool) or value is None:
        raise ProviderError(E.E_VALIDATION, f"{field_name} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ProviderError(E.E_VALIDATION, f"{field_name} must be an integer")
    if minimum is not None and number < minimum:
        raise ProviderError(E.E_VALIDATION, f"{field_name} must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise ProviderError(E.E_VALIDATION, f"{field_name} must be <= {maximum}")
    return number


def _status_for(code: str) -> int:
    return {E.E_AUTH: 401, E.E_FORBIDDEN: 403, E.E_NOT_FOUND: 404,
            E.E_VALIDATION: 422, E.E_WINDOW_CLOSED: 409, E.E_INSUFFICIENT: 402,
            E.E_DUPLICATE: 409, E.E_CONFLICT: 409, E.E_RATE_LIMIT: 429,
            "INVALID_TOKEN": 401, "INVALID_SESSION_TOKEN": 401,
            E.E_TBC_BLOCKED: 403, E.E_INTERNAL: 500}.get(code, 500)


def _service_error(exc):
    code = getattr(exc, "code", E.E_INTERNAL)
    return ProviderError(code, str(exc), _status_for(code))


# ---------------------------------------------------------------- handlers
def h_health(ctx: ProviderContext, req: Request) -> Tuple[int, dict]:
    rooms = getattr(ctx.service, "rooms", {}) or {}
    live = [r for r in rooms.values()
            if getattr(getattr(r, "round", None), "round_id", "")]
    payload = {
        "status": "ok",
        "game_code": GAME_CODE,
        "engine": "TeenPattiPro/1.0",
        "provider_api": "v1",
        "provider_auth_configured": bool(ctx.keys),
        "wallet_backend": type(ctx.wallet.adapter).__name__,
        "currency": ctx.currency,
        "tables": len(ctx.catalog.all()),
        "live_tables": len(live),
        "redis": ctx.redis,
        "serverTime": int(time.time() * 1000),
    }
    return 200, payload


def h_games(ctx: ProviderContext, req: Request) -> Tuple[int, dict]:
    games = []
    for code in BINDINGS:
        binding = BINDINGS[code]
        catalog = ctx.catalog_for(code)
        service = None
        try:
            service = binding.resolve(ctx)
        except Exception:
            service = None
        if service is None:
            continue  # game not enabled in this deployment
        tables = list_tables(catalog, service)
        if not tables:
            continue
        limits = [t["min_bet"] for t in tables]
        caps = [t["max_bet"] for t in tables]
        seats = [t["max_players"] for t in tables]
        games.append({
            "game_code": code,
            "name": binding.label,
            "status": "live",
            "kind": binding.kind,
            "engine_id": binding.engine_id,
            "currencies": sorted({t["currency"] for t in tables}) or [ctx.currency],
            "min_bet": min(limits), "max_bet": max(caps), "max_players": max(seats),
            "tables": [t["table_id"] for t in tables],
            "actions": ["bet"],
            "choice_field": binding.action_field,
            "choices_url": f"/api/v1/{binding.slug}/tables/{{tableId}}/choices",
            "launch_path": ctx.client_path_for(code).split("?")[0],
            "realtime": {"protocol": "websocket", "path": "/ws/game",
                         "auth": "session_token"},
        })
    return 200, {"games": games}


def _normalize_game_code(raw: str) -> str:
    code = canonical_code(raw or GAME_CODE)
    if code is None:
        raise ProviderError(E.E_VALIDATION,
                            "game_code must be one of: " + ", ".join(BINDINGS))
    return code


def h_create_session(ctx: ProviderContext, req: Request) -> Tuple[int, dict]:
    body = req.json_body()
    if not req.actor and body.get("launch_token"):
        return _legacy_redeem(ctx, body)
    if not req.actor:
        raise ProviderError(E.E_AUTH,
                            "signed operator request or launch_token is required", 401)
    player_id = _text(body.get("player_id"), "player_id", 128, pattern=PLAYER_ID_RE)
    game_code = _normalize_game_code(body.get("game_code", GAME_CODE))
    binding = BINDINGS[game_code]
    catalog = ctx.catalog_for(game_code)
    currency = _text(body.get("currency", ctx.currency), "currency", 12,
                     pattern=CURRENCY_RE).upper()
    language = _text(body.get("language", "en"), "language", 16,
                     pattern=LANGUAGE_RE).lower()
    platform = _text(body.get("platform", "web"), "platform", 24,
                     pattern=PLATFORM_RE).lower()
    return_url = _text(body.get("return_url"), "return_url", 512, required=False)
    if return_url and not return_url.startswith(("https://", "http://")):
        raise ProviderError(E.E_VALIDATION, "return_url must be an http(s) URL")
    if currency != ctx.currency:
        raise ProviderError(E.E_VALIDATION, f"currency must be {ctx.currency}")

    requested_table = str(body.get("table_id") or "").strip()
    amount = body.get("amount")
    bet_amount = _int(amount, "amount", 1) if amount is not None else None
    if requested_table:
        table = catalog.require(requested_table)
        if table is None:
            raise ProviderError(E.E_NOT_FOUND,
                                f"unknown table {requested_table}", 404)
        if table.currency != currency:
            raise ProviderError(E.E_VALIDATION,
                                f"table {table.table_id} is not {currency}")
    else:
        table = select_table(catalog, binding.resolve(ctx), bet_amount, currency)
    if bet_amount is not None and not (table.min_bet <= bet_amount <= table.max_bet):
        raise ProviderError(
            E.E_VALIDATION,
            f"amount must be between {table.min_bet} and {table.max_bet} for "
            f"{table.table_id}")

    service = binding.resolve(ctx)
    session = service.sessions.create(player_id, table.table_id, binding.engine_id)
    binding.join(ctx, table.table_id, player_id)
    record = ctx.tokens.mint(session.session_id, {
        "player_id": player_id, "game_code": game_code,
        "table_id": table.table_id, "currency": currency,
        "language": language, "platform": platform,
    }, ctx.session_ttl_s)
    launch_url = ctx.base_url.rstrip("/") + f"{PROVIDER_PREFIX}/launch/{record['token']}"
    ctx.audit(player_id, "provider.session.create", "session", session.session_id,
              after={"table_id": table.table_id, "game_code": game_code})
    try:
        service._fire("game.session.created", {
            "session_id": session.session_id, "player_id": player_id,
            "room_id": table.table_id, "game_code": game_code})
    except Exception:
        pass
    return 201, {
        "success": True,
        "session_id": session.session_id,
        "session_token": record["token"],
        "token_type": "Bearer",
        "game_code": game_code,
        "table_id": table.table_id,
        "currency": currency,
        "language": language,
        "platform": platform,
        "return_url": return_url,
        "launch_url": launch_url,
        "expires_at": _iso(record["expires_at_ms"]),
        "expires_at_ms": record["expires_at_ms"],
        "websocket_url": ctx.base_url.rstrip("/") + "/ws/game",
    }


def _legacy_redeem(ctx: ProviderContext, body: dict) -> Tuple[int, dict]:
    """Player-facing launch-token redemption kept for the existing client.

    The token is single use and short lived; it never carries operator rights.
    """
    try:
        return 200, ctx.service.open_session(str(body.get("launch_token") or ""))
    except Exception as exc:
        raise _service_error(exc)


def _load_session(ctx: ProviderContext, session_id: str):
    session = ctx.find_session(session_id)
    if session is None:
        record = resolve_token(ctx.tokens, session_id)
        if record is None:
            raise ProviderError("INVALID_SESSION_TOKEN", "unknown session", 401)
        session = ctx.find_session(record["session_id"])
        if session is None:
            raise ProviderError("INVALID_SESSION_TOKEN", "session expired", 401)
    return session


def h_get_session(ctx: ProviderContext, req: Request, session_id: str) -> Tuple[int, dict]:
    if not req.actor:
        _require_owner(ctx, req, session_id)
    session = _load_session(ctx, session_id)
    room = (getattr(ctx.service, "rooms", {}) or {}).get(session.room_id)
    return 200, {"session_id": session.session_id, "player_id": session.player_id,
                 "game_id": session.game_id, "table_id": session.room_id,
                 "created_at": _iso(session.created_at_ms),
                 "last_seen_at": _iso(session.last_seen_ms),
                 "active": True,
                 "table": room_status(room) if room is not None else None}


def h_delete_session(ctx: ProviderContext, req: Request, session_id: str) -> Tuple[int, dict]:
    session = _load_session(ctx, session_id)
    revoked = 0
    try:
        revoked = ctx.tokens.revoke_session(session.session_id)
    except Exception:
        revoked = 0
    for store in ctx.session_stores():
        try:
            if store.get(session.session_id) is not None:
                store.end(session.session_id)
                break
        except Exception:
            continue
    try:
        ctx.service.leave_table(session.room_id, session.player_id)
    except Exception:
        pass
    ctx.audit(session.player_id, "provider.session.delete", "session",
              session.session_id, after={"revoked_tokens": revoked})
    return 200, {"session_id": session.session_id, "ended": True,
                 "revoked_tokens": revoked}


def _require_owner(ctx: ProviderContext, req: Request, session_id: str) -> None:
    """A player may only read their own session with its own bearer token."""
    bearer = req.header("Authorization")
    token = bearer.split(None, 1)[1].strip() if bearer.split(None, 1)[:1] == ["Bearer"] else ""
    token = token or str(req.q("session_token", "")).strip()
    if not token:
        raise ProviderError(E.E_AUTH, "session bearer token is required", 401)
    record = resolve_token(ctx.tokens, token)
    owned = record["session_id"] if record else None
    if owned is None and ctx.service.sessions.get(token) is not None:
        owned = token
    if owned is None or owned != session_id:
        raise ProviderError(E.E_FORBIDDEN, "session belongs to another player", 403)


def h_list_tables(ctx: ProviderContext, req: Request, game: str) -> Tuple[int, dict]:
    binding = _binding_or_404(game)
    return 200, {"game_code": binding.game_code, "slug": binding.slug,
                 "tables": list_tables(ctx.catalog_for(binding.game_code),
                                       binding.resolve(ctx))}


def h_table_detail(ctx: ProviderContext, req: Request, game: str,
                   table_id: str) -> Tuple[int, dict]:
    binding = _binding_or_404(game)
    view = table_detail(ctx.catalog_for(binding.game_code), binding.resolve(ctx),
                        table_id)
    if view is None:
        raise ProviderError(E.E_NOT_FOUND, f"unknown table {table_id}", 404)
    view["game_code"] = binding.game_code
    view["choices"] = binding.choices(ctx, view["table_id"])
    return 200, view


def h_choices(ctx: ProviderContext, req: Request, game: str,
              table_id: str) -> Tuple[int, dict]:
    binding = _binding_or_404(game)
    catalog = ctx.catalog_for(binding.game_code)
    if catalog.require(table_id) is None:
        raise ProviderError(E.E_NOT_FOUND, f"unknown table {table_id}", 404)
    return 200, {"game_code": binding.game_code, "table_id": table_id,
                 "choice_field": binding.action_field,
                 "choices": binding.choices(ctx, table_id)}


def _player_from(ctx: ProviderContext, req: Request, body: dict,
                 query_player: str = "", optional: bool = False) -> Tuple[str, str]:
    """Resolve the acting player. A session token always wins over a
    caller-supplied player_id so a client can never act as another player."""
    bearer = req.header("Authorization")
    token = ""
    if bearer:
        parts = bearer.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
    supplied = str(body.get("session_id") or body.get("session_token") or "").strip() or token
    if supplied:
        record = resolve_token(ctx.tokens, supplied)
        if record is not None:
            return str(record["player_id"]), str(record["session_id"])
        session = ctx.find_session(supplied)
        if session is None:
            raise ProviderError("INVALID_SESSION_TOKEN",
                                "session is unknown or expired", 401)
        return session.player_id, session.session_id
    player_id = str(body.get("player_id") or query_player or "").strip()
    if not player_id and optional:
        return "", ""
    return _text(player_id, "player_id", 128, pattern=PLAYER_ID_RE), ""


def _binding_or_404(game: str):
    binding = binding_for_slug(game)
    if binding is None:
        raise ProviderError(E.E_NOT_FOUND, f"unknown game {game}", 404)
    return binding


def h_join(ctx: ProviderContext, req: Request, game: str, table_id: str) -> Tuple[int, dict]:
    body = req.json_body()
    binding = _binding_or_404(game)
    catalog = ctx.catalog_for(binding.game_code)
    table = catalog.require(table_id)
    if table is None:
        raise ProviderError(E.E_NOT_FOUND, f"unknown table {table_id}", 404)
    player_id, session_id = _player_from(ctx, req, body)
    currency = str(body.get("currency") or table.currency).upper()
    if currency != table.currency:
        raise ProviderError(E.E_VALIDATION,
                            f"table {table.table_id} is {table.currency}")
    if body.get("amount") is not None:
        amount = _int(body.get("amount"), "amount", 1)
        if not (table.min_bet <= amount <= table.max_bet):
            raise ProviderError(
                E.E_VALIDATION,
                f"amount must be between {table.min_bet} and {table.max_bet}")
    seated = seat_count(binding.resolve(ctx), table.table_id)
    result = binding.join(ctx, table.table_id, player_id)
    if not result["already_seated"] and seated >= table.max_players:
        binding.leave(ctx, table.table_id, player_id)
        raise ProviderError(E.E_CONFLICT, "table is full", 409)
    ctx.audit(player_id, "player.joined", "room", table.table_id,
              after={"session_id": session_id, "game_code": binding.game_code})
    try:
        binding.resolve(ctx)._fire("player.joined", {"room_id": table.table_id,
                                                     "player_id": player_id})
        binding.ensure_round(ctx, table.table_id)
    except Exception:
        pass
    _, room = binding.room(ctx, table.table_id)
    return 200, {"game_code": binding.game_code, "table_id": table.table_id,
                 "player_id": player_id, "seats": result["seats"],
                 "players": len(result["seats"]),
                 "max_players": table.max_players,
                 "status": room_status(room)["status"],
                 "joined": True, "already_seated": result["already_seated"]}


def h_leave(ctx: ProviderContext, req: Request, game: str, table_id: str) -> Tuple[int, dict]:
    body = req.json_body(required=False)
    binding = _binding_or_404(game)
    player_id, _ = _player_from(ctx, req, body)
    result = binding.leave(ctx, table_id, player_id)
    try:
        binding.resolve(ctx)._fire("player.left", {"room_id": table_id,
                                                   "player_id": player_id})
    except Exception:
        pass
    return 200, {"game_code": binding.game_code, "table_id": table_id,
                 "player_id": player_id, "seats": result["seats"],
                 "removed": result["removed"]}


def h_action(ctx: ProviderContext, req: Request, game: str, table_id: str) -> Tuple[int, dict]:
    body = req.json_body()
    binding = _binding_or_404(game)
    action = str(body.get("action") or "").strip().lower()
    player_id, _ = _player_from(ctx, req, body)
    if action in ("fold", "show"):
        raise ProviderError(
            E.E_VALIDATION,
            f"action '{action}' is not part of the {binding.label} engine")
    if action != "bet":
        raise ProviderError(
            E.E_VALIDATION,
            "action must be 'bet' in V1 (the engine has no turn/fold/show phase)")
    choice = _text(body.get(binding.action_field), binding.action_field, 32)
    amount = _int(body.get("amount"), "amount", 1)
    # Re-check the table profile here. The engine validates its own denoms and
    # limits, but a provider table may be stricter, and the contract promises
    # the table's range is enforced on every action.
    table = ctx.catalog_for(binding.game_code).require(table_id)
    if table is not None and not (table.min_bet <= amount <= table.max_bet):
        raise ProviderError(
            E.E_VALIDATION,
            f"amount must be between {table.min_bet} and {table.max_bet} for "
            f"{table.table_id}")
    key = str(req.header("Idempotency-Key") or body.get("idempotency_key") or "").strip()
    if not key:
        raise ProviderError(E.E_VALIDATION, "Idempotency-Key header is required")
    try:
        binding.ensure_round(ctx, table_id)
        result = binding.act(ctx, table_id, player_id, choice, amount, key)
    except Exception as exc:
        raise _service_error(exc)
    return 200, {"game_code": binding.game_code, "table_id": table_id,
                 "player_id": player_id, "action": "bet", "accepted": True,
                 binding.choice_field: result.get(binding.choice_field, choice),
                 **result}


def h_state(ctx: ProviderContext, req: Request, game: str, table_id: str) -> Tuple[int, dict]:
    binding = _binding_or_404(game)
    player_id, _ = _player_from(ctx, req, {}, req.q("player_id"))
    try:
        state = binding.state(ctx, table_id, player_id)
    except Exception as exc:
        raise _service_error(exc)
    return 200, {"game_code": binding.game_code, "table_id": table_id,
                 "player_id": player_id, "state": state}


def h_history(ctx: ProviderContext, req: Request, game: str, table_id: str) -> Tuple[int, dict]:
    binding = _binding_or_404(game)
    limit = _int(req.q("limit", "50"), "limit", 1, 200)
    player_id, _ = _player_from(ctx, req, {}, req.q("player_id"), optional=True)
    data = binding.history(ctx, table_id, player_id, limit)
    rounds = data.get("rounds", [])
    return 200, {"game_code": binding.game_code, "table_id": table_id,
                 "rounds": rounds, "count": len(rounds),
                 "bets": data.get("bets", []),
                 "earnings_today": data.get("earnings_today")}


def h_balance(ctx: ProviderContext, req: Request, player_id: str) -> Tuple[int, dict]:
    player_id = _text(urllib.parse.unquote(player_id), "player_id", 128,
                      pattern=PLAYER_ID_RE)
    try:
        balance = ctx.wallet.get_balance(player_id)
    except WalletError_ as exc:
        raise ProviderError(exc.code, str(exc), exc.status)
    return 200, {"player_id": balance.player_id, "available": balance.available,
                 "currency": balance.currency}


def _wallet_call(ctx: ProviderContext, req: Request, method_name: str) -> Tuple[int, dict]:
    body = req.json_body()
    key = req.header("Idempotency-Key")
    try:
        entry = getattr(ctx.wallet, method_name)(body, key, req.actor or "provider")
    except WalletError_ as exc:
        raise ProviderError(exc.code, str(exc), exc.status)
    except Exception as exc:
        raise ProviderError(E.E_INTERNAL, str(exc), 502)
    return 200, entry


def h_debit(ctx: ProviderContext, req: Request) -> Tuple[int, dict]:
    return _wallet_call(ctx, req, "debit")


def h_credit(ctx: ProviderContext, req: Request) -> Tuple[int, dict]:
    return _wallet_call(ctx, req, "credit")


def h_rollback(ctx: ProviderContext, req: Request) -> Tuple[int, dict]:
    return _wallet_call(ctx, req, "rollback")


def h_transactions(ctx: ProviderContext, req: Request, player_id: str) -> Tuple[int, dict]:
    player_id = _text(urllib.parse.unquote(player_id), "player_id", 128,
                      pattern=PLAYER_ID_RE)
    limit = _int(req.q("limit", "50"), "limit", 1, 200)
    rows = ctx.wallet.transactions(player_id, limit)
    return 200, {"player_id": player_id, "transactions": rows, "count": len(rows)}


def h_openapi(ctx: ProviderContext, req: Request) -> Tuple[int, dict]:
    from provider.spec import openapi_json
    return 200, {"__raw__": openapi_json(), "__type__": "application/json"}


def h_docs(ctx: ProviderContext, req: Request) -> Tuple[int, dict]:
    from provider.docs_page import render
    from provider.spec import SPEC
    return 200, {"__raw__": render(SPEC), "__type__": "text/html; charset=utf-8"}


def h_launch(ctx: ProviderContext, req: Request, token: str) -> Tuple[int, dict]:
    record = resolve_token(ctx.tokens, token)
    if record is None:
        raise ProviderError("INVALID_SESSION_TOKEN",
                            "launch token is unknown or expired", 401)
    code = canonical_code(record.get("game_code")) or GAME_CODE
    path = ctx.client_path_for(code)
    location = f"{path}{urllib.parse.quote(record['token'], safe='')}"
    if record.get("table_id"):
        location += f"&room={urllib.parse.quote(record['table_id'], safe='')}"
    return 302, {"__redirect__": location}


GAME_SLUG = r"(teen-patti|greedy-lion|monkey-wheel)"

# ------------------------------------------------------------------ routes
AUTH_HMAC = "hmac"
AUTH_PUBLIC = "public"
AUTH_HMAC_OR_TOKEN = "hmac_or_token"

ROUTES: List[Tuple[str, re.Pattern, object, str]] = [
    ("GET", re.compile(rf"^{PROVIDER_PREFIX}/health$"), h_health, AUTH_PUBLIC),
    ("GET", re.compile(r"^/api/v1/games$"), h_games, AUTH_HMAC),
    ("POST", re.compile(r"^/api/v1/sessions$"), h_create_session, AUTH_HMAC_OR_TOKEN),
    ("GET", re.compile(r"^/api/v1/sessions/([^/]+)$"), h_get_session, AUTH_HMAC_OR_TOKEN),
    ("DELETE", re.compile(r"^/api/v1/sessions/([^/]+)$"), h_delete_session, AUTH_HMAC),
    ("GET", re.compile(rf"^/api/v1/{GAME_SLUG}/tables$"), h_list_tables, AUTH_HMAC),
    ("GET", re.compile(rf"^/api/v1/{GAME_SLUG}/tables/([^/]+)$"), h_table_detail, AUTH_HMAC),
    ("GET", re.compile(rf"^/api/v1/{GAME_SLUG}/tables/([^/]+)/choices$"), h_choices, AUTH_HMAC),
    ("POST", re.compile(rf"^/api/v1/{GAME_SLUG}/tables/([^/]+)/join$"), h_join, AUTH_HMAC),
    ("POST", re.compile(rf"^/api/v1/{GAME_SLUG}/tables/([^/]+)/leave$"), h_leave, AUTH_HMAC),
    ("POST", re.compile(rf"^/api/v1/{GAME_SLUG}/tables/([^/]+)/action$"), h_action, AUTH_HMAC),
    ("GET", re.compile(rf"^/api/v1/{GAME_SLUG}/tables/([^/]+)/state$"), h_state, AUTH_HMAC),
    ("GET", re.compile(rf"^/api/v1/{GAME_SLUG}/tables/([^/]+)/history$"), h_history, AUTH_HMAC),
    ("GET", re.compile(r"^/api/v1/players/([^/]+)/balance$"), h_balance, AUTH_HMAC),
    ("POST", re.compile(r"^/api/v1/wallet/debit$"), h_debit, AUTH_HMAC),
    ("POST", re.compile(r"^/api/v1/wallet/credit$"), h_credit, AUTH_HMAC),
    ("POST", re.compile(r"^/api/v1/wallet/rollback$"), h_rollback, AUTH_HMAC),
    ("GET", re.compile(r"^/api/v1/wallet/transactions/([^/]+)$"), h_transactions, AUTH_HMAC),
    ("GET", re.compile(r"^/openapi\.json$"), h_openapi, AUTH_PUBLIC),
    ("GET", re.compile(r"^/docs/?$"), h_docs, AUTH_PUBLIC),
    ("GET", re.compile(rf"^{PROVIDER_PREFIX}/openapi\.json$"), h_openapi, AUTH_PUBLIC),
    ("GET", re.compile(rf"^{PROVIDER_PREFIX}/launch/([^/]+)$"), h_launch, AUTH_PUBLIC),
]


def resolve_route(method: str, path: str):
    for route_method, pattern, handler, auth in ROUTES:
        match = pattern.match(path)
        if match and route_method == method:
            return handler, list(match.groups()), auth
    return None, [], None


def is_provider_path(path: str) -> bool:
    if path in ("/docs", "/docs/", "/openapi.json"):
        return True
    if re.fullmatch(rf"/api/v1/{GAME_SLUG}/tables(/.*)?", path):
        return True
    if path.startswith("/api/v1/wallet/"):
        return True
    if path.startswith("/api/v1/players/"):
        return True
    if re.fullmatch(r"/api/v1/sessions(/[^/]+)?", path):
        return True
    if path == "/api/v1/games":
        return True
    if path.startswith(PROVIDER_PREFIX + "/"):
        return True
    return False


def dispatch(ctx: ProviderContext, method: str, path: str, query: str,
             headers, body: bytes) -> Tuple[int, dict, dict]:
    """Returns (status, response_headers, envelope_payload)."""
    if method in ("POST", "PUT", "PATCH", "DELETE") and len(body or b"") > MAX_BODY:
        return 413, {}, E.err("request body too large", E.E_VALIDATION)
    handler, groups, auth = resolve_route(method, path)
    if handler is None:
        allowed = [m for m, pattern, _, _ in ROUTES if pattern.match(path)]
        if allowed:
            return 405, {"Allow": ", ".join(allowed)}, E.err(
                "method not allowed", E.E_VALIDATION)
        return 404, {}, E.err("no such provider route", E.E_NOT_FOUND)

    actor = ""
    if auth == AUTH_HMAC:
        try:
            actor = PA.authenticate(headers, method, path, body or b"", ctx.keys,
                                    ctx.nonces, ctx.limiter)
        except PA.ProviderAuthError as exc:
            return exc.status, exc.headers, E.err(str(exc), exc.code)
    elif auth == AUTH_HMAC_OR_TOKEN:
        try:
            actor = PA.authenticate(headers, method, path, body or b"", ctx.keys,
                                    ctx.nonces, ctx.limiter)
        except PA.ProviderAuthError:
            actor = ""  # handler enforces the alternative credential

    query_map = {k: v[-1] for k, v in
                 urllib.parse.parse_qs(query or "").items()}
    req = Request(method, path, query_map, headers, body or b"", actor)
    try:
        status, data = handler(ctx, req, *groups)
    except ProviderError as exc:
        return exc.status, exc.headers, E.err(str(exc), exc.code)
    except WalletError_ as exc:
        return exc.status, exc.headers, E.err(str(exc), exc.code)
    except SessionTokenError as exc:
        return 401, {}, E.err(str(exc), exc.code)
    except Exception as exc:
        return 500, {}, E.err(f"internal error: {exc}", E.E_INTERNAL)
    if "__redirect__" in data:
        return status, {"Location": data["__redirect__"]}, {}
    if "__raw__" in data:
        return status, {"Content-Type": data.get("__type__", "text/plain")}, data["__raw__"]
    return status, {}, E.ok(data)
