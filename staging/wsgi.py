"""Staging WSGI transport (Vercel serverless).

Reuses games/teen_patti_pro/api.py Handler routing, validation, RBAC, and
envelopes 1:1 by invoking do_GET/do_POST/do_PUT on a facade instance backed
by per-request Redis state (staging/state.py). No game logic is duplicated.
Static files are served by Vercel itself; only /api/* reaches this app.
"""
import io
import json
import os
import re

from staging import state as ST


def _is_production():
    return os.environ.get("APP_ENV", "sandbox").lower() == "production"


def _extract_scope(path, method):
    """Return (game, rooms:set, wildcard:bool) for locking/loading."""
    rooms, game, wildcard = set(), None, False
    m = re.search(r"/games/([^/]+)/", path + "/")
    if m:
        game = m.group(1)
    m = re.search(r"/rooms/([^/?]+)", path)
    if m:
        rooms.add(m.group(1))
    m = re.search(r"/tables/([^/?]+)", path)
    if m:
        rooms.add(m.group(1))  # tables API: tableId == room
    if re.search(r"/rounds/[^/?]+/(result)$", path):
        wildcard = True  # roundId -> room needs a game-wide lock
    if path.startswith("/api/v1/staging/"):
        wildcard = True
    if path.startswith("/api/v1/admin/"):
        wildcard = True
    return game, rooms, wildcard


def _game_of(path, default="teen-patti-pro"):
    m = re.search(r"/games/([^/]+)/", path + "/")
    return m.group(1) if m else default


def _canonical(game):
    from games.teen_patti_pro.api import Handler
    if game in ("teen-patti-pro", "teen_patti"):
        return "teen-patti-pro"
    return Handler.WHEEL_ALIAS.get(game, game)


def _load_all(r, teen, wheels):
    from staging.state import _load_cfg, load_handler_state, load_room
    hstate = load_handler_state(r)
    teen.config = _load_cfg(r, "teen-patti-pro", teen.config)
    for gid, svc in wheels.items():
        svc.config = _load_cfg(r, gid, svc.config)
    return hstate


def _save_all(r, teen, wheels, hstate, games_touched):
    from staging.state import _save_cfg, save_handler_state, save_room, spill_audit
    save_handler_state(r, hstate)
    _save_cfg(r, "teen-patti-pro", teen.config)
    for gid, svc in wheels.items():
        _save_cfg(r, gid, svc.config)
    for game in games_touched:
        svc = teen if game == "teen-patti-pro" else wheels.get(game)
        if svc is None:
            continue
        for room_id in list(getattr(svc, "rooms", {}).keys()):
            save_room(r, svc, game, room_id)
        spill_audit(r, svc, game)


class _Headers(dict):
    pass


def _run_handler(method, path, query, headers, body):
    from games.teen_patti_pro.api import Handler
    from common import envelope as E  # noqa: F401 (contract parity)

    r, teen, wheels = ST.build_services()
    hstate = _load_all(r, teen, wheels)

    game = _canonical(_game_of(path))
    _, rooms, wildcard = _extract_scope(path, method)

    # Load the touched rooms into their services (rooms persist per game).
    targets = []
    if game == "teen-patti-pro":
        targets = [("teen-patti-pro", teen)]
    else:
        for gid, svc in wheels.items():
            targets.append((gid, svc))
    for gid, svc in targets:
        for room_id in rooms:
            try:
                ST.load_room(r, svc, gid, room_id)
            except Exception:
                pass

    # Lazy sweep of due rooms (idempotent close->result->settle).
    try:
        ST.lazy_sweep(r, teen, wheels)
        # Reload touched rooms post-sweep (sweep may have mutated them).
        for gid, svc in targets:
            for room_id in rooms:
                try:
                    ST.load_room(r, svc, gid, room_id)
                except Exception:
                    pass
    except Exception:
        pass

    # Facade Handler with Redis-backed admin state.
    h = Handler.__new__(Handler)
    h.svc = teen
    h.wheels = wheels
    h.game_enabled = dict(hstate.get("game_enabled", {}))
    h.game_packages = dict(hstate.get("game_packages", {}))
    h.game_labels = dict(hstate.get("game_labels", {}))
    h.provider_ctx = None
    h.provider_tokens = None
    try:
        from provider.context import staging_context
        _pctx = staging_context(r, teen)
        if not _pctx.base_url:
            _host = (headers or {}).get("Host") or ""
            if _host:
                _pctx.base_url = f"https://{_host}"
        h.provider_ctx = _pctx
        h.provider_tokens = _pctx.tokens
    except Exception:
        pass
    locks = []
    locked = True
    # Staging issuance + session-open take no room state; never lock them.
    from urllib.parse import urlparse as _up
    _p = _up(path).path
    _no_lock = _p in ("/api/v1/staging/test-login",
                      "/api/v1/staging/test-wallet/grant",
                      "/api/v1/sessions") or _p.endswith("/sessions")
    if method in ("POST", "PUT") and not _no_lock:
        scope_rooms = rooms if rooms and not wildcard else {"*"}
        try:
            for room_id in scope_rooms:
                tok = ST.acquire(r, game, room_id)
                locks.append((game, room_id, tok))
        except Exception:
            locked = False
    try:
        if not locked:
            return 503, {"Content-Type": "application/json"}, json.dumps(
                E.err("room busy, retry", E.E_RATE_LIMIT)).encode()
        h.command = method
        h.path = path + (("?" + query) if query else "")
        h.headers = _Headers(headers or {})
        h.rfile = io.BytesIO(body or b"")
        out = io.BytesIO()
        h.wfile = out
        captured = {}

        def send_response(code, message=None):
            captured["status"] = code

        def send_header(k, v):
            captured.setdefault("headers", {})[k] = v

        def end_headers():
            pass

        h.send_response = send_response
        h.send_header = send_header
        h.end_headers = end_headers
        # Route to the canonical service for aliased game ids.
        if game != "teen-patti-pro" and game not in wheels:
            canon = {"monkey-wheel": "greedy-monkey",
                     "monkey_wheel": "greedy-monkey"}.get(game)
            if canon and canon in wheels:
                # Rewrite path so Handler resolves the canonical service.
                h.path = h.path.replace(f"/games/{game}/", f"/games/{canon}/", 1)
                game = canon
        try:
            if method == "GET":
                h.do_GET()
            elif method == "POST":
                # Staging-only issuance routes (refused in production).
                from urllib.parse import urlparse
                if urlparse(path).path == "/api/v1/staging/test-login":
                    return _staging_login(r, teen, wheels, body)
                if urlparse(path).path == "/api/v1/staging/test-wallet/grant":
                    return _staging_grant(r, wheels, teen, body)
                h.do_POST()
            elif method == "PUT":
                h.do_PUT()
            elif method == "DELETE":
                h.do_DELETE()
            else:
                return 405, {"Content-Type": "application/json"}, json.dumps(
                    E.err("method not allowed", E.E_VALIDATION)).encode()
        except BrokenPipeError:
            pass
        status = captured.get("status", 200)
        resp_headers = captured.get("headers", {"Content-Type": "application/json"})
        payload = out.getvalue()
    finally:
        for game_l, room_l, tok in locks:
            try:
                ST.release(r, game_l, room_l, tok)
            except Exception:
                pass
    # Persist rooms touched + handler/admin state + audit spill.
    try:
        hstate = {"game_enabled": dict(getattr(h, "game_enabled", {})),
                  "game_packages": dict(getattr(h, "game_packages", {})),
                  "game_labels": dict(getattr(h, "game_labels", {}))}
        games_touched = {"teen-patti-pro", *wheels.keys()}
        _save_all(r, teen, wheels, hstate, games_touched)
    except Exception:
        pass
    return status, resp_headers, payload


def _staging_login(r, teen, wheels, body):
    from common import envelope as E
    if _is_production():
        return 403, {"Content-Type": "application/json"}, json.dumps(
            E.err("staging-only", E.E_FORBIDDEN)).encode()
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        data = {}
    player = str(data.get("player", "qa-player"))[:64] or "qa-player"
    game = str(data.get("game", "teen-patti-pro"))[:64]
    room = str(data.get("room", "staging-room"))[:64] or "staging-room"
    canon = {"monkey-wheel": "greedy-monkey", "monkey_wheel": "greedy-monkey"}.get(game, game)
    svc = teen if canon == "teen-patti-pro" else wheels.get(canon)
    if svc is None:
        return 404, {"Content-Type": "application/json"}, json.dumps(
            E.err(f"Unknown game {game}", E.E_NOT_FOUND)).encode()
    tok = svc.tokens.mint(player, room, canon).token
    # Fund the player wallet on first login (TEST COINS, idempotent per player).
    try:
        funded = r.command("SISMEMBER", "stg:funded", player)
        if not funded:
            svc.wallet.faucet(player, 20000, "staging-welcome", f"welcome-{player}") \
                if hasattr(svc.wallet, "faucet") else svc.wallet.credit(
                    player, 20000, "faucet:staging-welcome",
                    f"faucet:welcome-{player}")
            r.command("SADD", "stg:funded", player)
    except Exception:
        pass
    return 200, {"Content-Type": "application/json"}, json.dumps(
        E.ok({"launch_token": tok, "player_id": player, "game_id": canon,
              "room_id": room, "test_coins": True})).encode()


def _staging_grant(r, wheels, teen, body):
    from common import envelope as E
    if _is_production():
        return 403, {"Content-Type": "application/json"}, json.dumps(
            E.err("staging-only", E.E_FORBIDDEN)).encode()
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return 422, {"Content-Type": "application/json"}, json.dumps(
            E.err("invalid JSON", E.E_VALIDATION)).encode()
    player = str(data.get("player", ""))[:64]
    amount = data.get("amount", 0)
    key = str(data.get("idempotency_key", data.get("key", "")))[:128]
    game = str(data.get("game", "teen-patti-pro"))[:64]
    if not player or not key:
        return 422, {"Content-Type": "application/json"}, json.dumps(
            E.err("player + idempotency_key required", E.E_VALIDATION)).encode()
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return 422, {"Content-Type": "application/json"}, json.dumps(
            E.err("amount must be integer", E.E_VALIDATION)).encode()
    canon = {"monkey-wheel": "greedy-monkey", "monkey_wheel": "greedy-monkey"}.get(game, game)
    svc = teen if canon == "teen-patti-pro" else wheels.get(canon)
    if svc is None:
        return 404, {"Content-Type": "application/json"}, json.dumps(
            E.err(f"Unknown game {game}", E.E_NOT_FOUND)).encode()
    try:
        if hasattr(svc.wallet, "faucet"):
            ref = svc.wallet.faucet(player, amount, "staging-grant", key)
        else:
            ref = svc.wallet.credit(player, amount, f"faucet:{key}", f"faucet:{key}")
    except Exception as exc:
        from common.wallet import InsufficientBalance, WalletError
        code = E.E_INSUFFICIENT if isinstance(exc, InsufficientBalance) else E.E_VALIDATION
        if isinstance(exc, WalletError) and "staging-only" in str(exc):
            code = E.E_FORBIDDEN
        return 422 if code != E.E_FORBIDDEN else 403, {"Content-Type": "application/json"}, \
            json.dumps(E.err(str(exc), code)).encode()
    bal = svc.wallet.get_balance(player)
    return 200, {"Content-Type": "application/json"}, json.dumps(
        E.ok({"txn_id": ref.txn_id, "available": bal.available,
              "currency": "TEST", "test_coins": True})).encode()


DOC_PATHS = ("/docs", "/docs/", "/openapi.json")


def _serve_static_spec(path):
    """Docs and the OpenAPI document never need Redis or game state."""
    from urllib.parse import urlparse
    clean = urlparse(path).path
    if clean == "/openapi.json":
        from provider.spec import openapi_json
        return 200, "application/json", openapi_json().encode()
    if clean in ("/docs", "/docs/"):
        from provider.docs_page import render
        from provider.spec import SPEC
        return 200, "text/html; charset=utf-8", render(SPEC).encode()
    return None


def _degraded_health(reason):
    import json as _j
    import time as _t
    from common import envelope as E
    from provider.router import GAME_CODE
    return 200, {"Content-Type": "application/json"}, _j.dumps(E.ok({
        "status": "degraded",
        "game_code": GAME_CODE,
        "engine": "TeenPattiPro/1.0",
        "provider_api": "v1",
        "provider_auth_configured": bool(os.environ.get("PROVIDER_API_KEYS")),
        "wallet_backend": "unavailable",
        "currency": os.environ.get("COIN_CURRENCY", "COIN"),
        "tables": 0,
        "live_tables": 0,
        "redis": False,
        "reason": str(reason)[:200],
        "serverTime": int(_t.time() * 1000),
    })).encode()


def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/") or "/"
    query = environ.get("QUERY_STRING", "")
    static = _serve_static_spec(path) if method == "GET" else None
    if static is not None:
        status, content_type, payload = static
        start_response(f"{status} 200", [("Content-Type", content_type)])
        return [payload]
    headers = {}
    for k, v in environ.items():
        if k.startswith("HTTP_"):
            headers[k[5:].replace("_", "-").title()] = v
    if environ.get("CONTENT_TYPE"):
        headers["Content-Type"] = environ["CONTENT_TYPE"]
    try:
        length = int(environ.get("CONTENT_LENGTH", "0") or 0)
    except ValueError:
        length = 0
    headers["Content-Length"] = str(length)
    body = environ["wsgi.input"].read(length) if length > 0 else b""
    try:
        status, resp_headers, payload = _run_handler(method, path, query, headers, body)
    except Exception as exc:  # noqa: BLE001 - classify, then envelope
        import json as _j
        from common import envelope as E
        from integrations.redis_store import RedisConnectionError
        if isinstance(exc, (RedisConnectionError, TimeoutError, RuntimeError)):
            # Staging without Redis (or prod boot attempt): fail loudly, never fake.
            if path == "/api/v1/provider/health":
                # Liveness must still answer so a probe can see the degradation.
                status, resp_headers, payload = _degraded_health(exc)
            else:
                status, resp_headers = 503, {"Content-Type": "application/json"}
                payload = _j.dumps(E.err(f"staging backend unavailable: {exc}", "UNAVAILABLE")).encode()
        else:
            status, resp_headers = 500, {"Content-Type": "application/json"}
            payload = _j.dumps(E.err("internal error", E.E_INTERNAL)).encode()
    phrases = {200: "OK", 201: "Created", 401: "Unauthorized", 403: "Forbidden",
               404: "Not Found", 405: "Method Not Allowed", 409: "Conflict",
               422: "Unprocessable Entity", 429: "Too Many Requests",
               500: "Internal Server Error", 503: "Service Unavailable"}
    start_response(f"{status} {phrases.get(status, 'OK')}",
                   [(k, v) for k, v in resp_headers.items()])
    return [payload]
