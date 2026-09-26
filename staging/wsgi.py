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
import uuid

from staging import state as ST
from common.envelope import now_iso


_PROCESS_STARTED = __import__("time").time()


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
    if game in ("teen-patti-pro", "teen_patti"):
        return "teen-patti-pro"
    # Teen Patti Pro is the only shipped game, so anything else is left as-is
    # and rejected by the caller's unknown-game guard. This used to consult
    # Handler.WHEEL_ALIAS, which no longer exists; referencing it raised
    # AttributeError and surfaced as a 500 for any non-teen path.
    return game


def _load_all(r, teen, wheels):
    from staging.state import (_load_cfg, load_handler_state, load_room,
                               load_webhook_config)
    hstate = load_handler_state(r)
    teen.config = _load_cfg(r, "teen-patti-pro", teen.config)
    for gid, svc in wheels.items():
        svc.config = _load_cfg(r, gid, svc.config)
    webhooks = load_webhook_config(r)
    destinations = list(webhooks.get("destinations", [])) if webhooks.get("enabled") else []
    teen.webhook_destinations = destinations
    for svc in wheels.values():
        svc.webhook_destinations = list(destinations)
    return hstate


def _save_all(r, teen, wheels, hstate, games_touched):
    from staging.state import (_save_cfg, save_handler_state, save_room,
                               spill_audit, spill_webhook_deliveries)
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
        spill_webhook_deliveries(r, svc)


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
        _pctx = staging_context(r, teen, wheels)
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
                      "/api/v1/staging/api-keys/provision",
                      "/api/v1/staging/api-keys",
                      "/api/v1/staging/api-keys/revoke",
                      "/api/v1/staging/api-keys/rotate",
                      "/api/v1/admin/webhooks/config",
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
            canon = _canonical(game)
            if canon and canon in wheels:
                # Rewrite path so Handler resolves the canonical service.
                h.path = h.path.replace(f"/games/{game}/", f"/games/{canon}/", 1)
                game = canon
        try:
            if method == "GET":
                from urllib.parse import urlparse
                _staging_get_path = urlparse(path).path
                if _staging_get_path == "/api/v1/staging/api-keys":
                    return _staging_list_keys(r, headers)
                if _staging_get_path == "/api/v1/admin/audit":
                    return _staging_admin_audit(r, headers, query)
                if _staging_get_path == "/api/v1/admin/webhooks":
                    return _staging_webhook_log(r, headers, query)
                h.do_GET()
            elif method == "POST":
                # Staging-only issuance routes (refused in production).
                from urllib.parse import urlparse
                _staging_path = urlparse(path).path
                if _staging_path == "/api/v1/staging/test-login":
                    return _staging_login(r, teen, wheels, body)
                if _staging_path == "/api/v1/staging/test-wallet/grant":
                    return _staging_grant(r, wheels, teen, body)
                if _staging_path == "/api/v1/staging/api-keys/provision":
                    return _staging_issue_key(r, body)
                if _staging_path == "/api/v1/staging/api-keys/revoke":
                    return _staging_revoke_key(r, headers, body)
                if _staging_path == "/api/v1/staging/api-keys/rotate":
                    return _staging_rotate_key(r, headers, body)
                h.do_POST()
            elif method == "PUT":
                from urllib.parse import urlparse
                if urlparse(path).path == "/api/v1/admin/webhooks/config":
                    return _staging_webhook_config(r, teen, wheels, headers, body)
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


def _http_phrase(status):
    return {200: "OK", 201: "Created", 401: "Unauthorized", 403: "Forbidden",
            404: "Not Found", 405: "Method Not Allowed", 409: "Conflict",
            422: "Unprocessable Entity", 429: "Too Many Requests",
            500: "Internal Server Error", 503: "Service Unavailable"}.get(status, "OK")


def _git_short_sha():
    for key in ("VERCEL_GIT_COMMIT_SHA", "GITHUB_SHA", "GIT_COMMIT_SHA"):
        if os.environ.get(key):
            return str(os.environ[key])[:12]
    import subprocess
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True,
            stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def _service_result(status, reason=""):
    result = {"status": status}
    if reason:
        result["reason"] = reason[:200]
    return result


def _probe_redis():
    from integrations.redis_store import MinimalRedis, RedisConnectionError
    try:
        client = MinimalRedis(timeout=2.0)
        try:
            if client.command("PING") in (b"PONG", "PONG"):
                return _service_result("ok")
            return _service_result("error", "Redis PING returned an unexpected response")
        finally:
            client.close()
    except RedisConnectionError as exc:
        return _service_result("unavailable", type(exc).__name__)
    except Exception as exc:
        return _service_result("error", type(exc).__name__)


def _probe_database():
    if not os.environ.get("DATABASE_URL"):
        return _service_result("unavailable", "DATABASE_URL is not configured")
    try:
        import psycopg
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=2) as connection:
            connection.execute("SELECT 1")
        return _service_result("ok")
    except ModuleNotFoundError:
        return _service_result("unavailable", "psycopg driver is not installed")
    except Exception as exc:
        return _service_result("error", type(exc).__name__)


def _probe_websocket():
    import socket
    from urllib.parse import urlparse
    url = os.environ.get("WEBSOCKET_URL", "").strip()
    if url:
        parsed = urlparse(url if "://" in url else "ws://" + url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme in ("wss", "https") else 80)
    else:
        host = os.environ.get("GAME_API_HOST", "").strip()
        port = os.environ.get("GAME_WS_PORT", "").strip()
    if not host or not port:
        return _service_result("not_configured", "WEBSOCKET_URL is not configured; REST polling is active")
    try:
        with socket.create_connection((host, int(port)), timeout=2):
            return _service_result("ok")
    except (OSError, ValueError) as exc:
        return _service_result("error", type(exc).__name__)


def _health_payload():
    import datetime
    import time
    services = {"database": _probe_database(), "redis": _probe_redis(),
                "websocket": _probe_websocket()}
    statuses = {name: service["status"] for name, service in services.items()}
    critical = {"database": statuses["database"], "redis": statuses["redis"]}
    if all(value == "ok" for value in critical.values()):
        status = "ok"
    elif any(value == "error" for value in critical.values()):
        # "unavailable" rather than "down": a probe that ran and failed is not
        # the same claim as a service known to be down, and integrations
        # branch on this value.
        status = "unavailable"
    else:
        status = "degraded"
    # No test_count here. It used to be hardcoded at 198, which was wrong the
    # moment a test was added and told an operator nothing actionable. Health
    # reports the state of running services, not the size of the test suite.
    return {"status": status, "version": _git_short_sha(),
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "uptime_seconds": max(0, int(time.time() - _PROCESS_STARTED)),
            "services": services}


_PROCESS_STARTED = __import__("time").time()


def _phase1_response(status, payload):
    return status, {"Content-Type": "application/json"}, json.dumps(payload).encode()


def _phase1_auth(method, path, headers, body, environ):
    from common import envelope as E
    from provider.operator_auth import (AuthConfigurationError, AuthError,
                                        PinAuth, _client_ip, _country,
                                        require_operator, require_admin)
    clean_path = path.rstrip("/") or "/"
    # The PIN scopes are `operator` and `admin`. The /api/v1/superadmin/*
    # path prefix is a historical namespace for operator management; the role
    # behind it is the top `admin` role, since there is no superadmin tier.
    auth_paths = {
        "/api/v1/operator/auth": "operator",
        "/api/v1/superadmin/auth": "admin",
    }
    scope = auth_paths.get(clean_path)
    if scope:
        if method != "POST":
            return _phase1_response(405, E.err("method not allowed", E.E_VALIDATION))
        redis = None
        try:
            data = _staging_json(body)
            ip = _client_ip(headers, environ.get("REMOTE_ADDR", ""))
            user_agent = str(headers.get("User-Agent", ""))
            country = _country(headers)
            try:
                redis = ST._redis()
            except Exception:
                return _phase1_response(503, E.err("auth backend unavailable", "UNAVAILABLE"))
            auth = PinAuth(scope, redis_client=redis, remote_addr=environ.get("REMOTE_ADDR", ""))
            token, claims = auth.authenticate(data.get("pin"), ip, user_agent, country)
        except ValueError as exc:
            return _phase1_response(422, E.err(str(exc), E.E_VALIDATION))
        except AuthError as exc:
            return _phase1_response(exc.status, E.err(str(exc), exc.code))
        except AuthConfigurationError:
            return _phase1_response(503, E.err("auth is not configured", "UNAVAILABLE"))
        finally:
            if redis is not None:
                redis.close()
        field = "operator_token" if scope == "operator" else "admin_token"
        return _phase1_response(200, {
            field: token,
            "expires_at": claims["exp"],
            "scope": scope,
        })

    if clean_path.startswith("/api/v1/operator/"):
        claims = require_operator(headers)
        if claims is None:
            return _phase1_response(401, E.err("operator token required", "INVALID_TOKEN"))
        environ["operator"] = {
            "scope": claims["scope"],
            "issued_at": claims["iat"],
            "exp": claims["exp"],
        }
        environ["operator_id"] = claims.get("operator_id", "global")
        if method == "GET" and clean_path == "/api/v1/operator/sessions":
            return _phase1_response(200, {"sessions": [],
                                          "operator_id": environ["operator_id"]})
    elif clean_path.startswith("/api/v1/superadmin/"):
        claims = require_admin(headers)
        if claims is None:
            return _phase1_response(401, E.err("admin token required", "INVALID_TOKEN"))
        environ["operator"] = {
            "scope": claims["scope"],
            "issued_at": claims["iat"],
            "exp": claims["exp"],
        }
        environ["operator_id"] = claims.get("operator_id", "global")
    return None


def _staging_json(body, max_bytes=8192):
    from common import envelope as E
    if len(body or b"") > max_bytes:
        raise ValueError("request body too large")
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        raise ValueError("invalid JSON")
    if not isinstance(data, dict):
        raise ValueError("body must be a JSON object")
    return data


def _staging_json_response(status, payload):
    from common import envelope as E
    return status, {"Content-Type": "application/json"}, json.dumps(payload).encode()


def _staging_require_role(headers, minimum, game_id=""):
    from common import envelope as E
    from games.teen_patti_pro.api import Handler
    facade = Handler.__new__(Handler)
    facade.headers = _Headers(dict(headers or {}))
    denied = facade.require_role(minimum, game_id)
    if denied is None:
        return None
    status, payload = denied
    return (status, {"Content-Type": "application/json"},
            json.dumps(payload).encode())


def _staging_require_admin(headers):
    """Top-tier gate for the staging control plane. `admin` is the top role;
    there is no superadmin tier."""
    return _staging_require_role(headers, "admin")


def _staging_audit_event(r, actor, action, entity, entity_id, after):
    """Persist staging control-plane audit rows immediately.

    Direct staging routes return before the normal end-of-request spill, so
    issuance, revocation and configuration changes must not rely on in-memory
    service audit state.
    """
    import time as _time
    aid = f"stg-audit-{uuid.uuid4().hex}"
    entry = {"audit_id": aid, "actor": actor, "action": action,
             "entity": entity, "entity_id": entity_id, "before": None,
             "after": after, "timestamp": int(_time.time() * 1000)}
    key = "stg:audit:provider"
    r.command("LPUSH", key, json.dumps(entry))
    r.command("SADD", key + ":ids", aid)
    r.command("LTRIM", key, "0", "499")


def _staging_key_audit(r, actor, action, entity_id, after):
    _staging_audit_event(r, actor, action, "api-key", entity_id, after)


def _staging_webhook_config_payload(config):
    return {"enabled": bool(config.get("enabled", False)),
            "destinations": list(config.get("destinations", [])),
            "secret_configured": bool(os.environ.get("WEBHOOK_SECRET") or
                                      os.environ.get("SETTLEMENT_SIGNING_SECRET"))}


def _validate_webhook_config(data):
    from common import envelope as E
    from urllib.parse import urlparse
    if not isinstance(data, dict):
        raise ValueError("body must be a JSON object")
    if not isinstance(data.get("enabled"), bool):
        raise ValueError("enabled must be a boolean")
    destinations = data.get("destinations", [])
    if not isinstance(destinations, list):
        raise ValueError("destinations must be a list")
    if len(destinations) > 5:
        raise ValueError("at most five webhook destinations are allowed")
    cleaned = []
    for destination in destinations:
        text = str(destination or "").strip()
        if len(text) > 512:
            raise ValueError("webhook destination is too long")
        parsed = urlparse(text)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("webhook destinations must be https URLs")
        cleaned.append(text)
    reason = data.get("reason", "")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("reason must be a string")
    if len(reason or "") > 256:
        raise ValueError("reason is too long")
    return {"enabled": data["enabled"], "destinations": cleaned}


def _staging_webhook_log(r, headers, query):
    from common import envelope as E
    from urllib.parse import parse_qs
    if _is_production():
        return _staging_json_response(403, E.err("staging-only", E.E_FORBIDDEN))
    denied = _staging_require_role(headers, "auditor")
    if denied is not None:
        return denied
    params = parse_qs(query or "")
    try:
        limit = int((params.get("limit") or ["100"])[0])
    except (TypeError, ValueError):
        return _staging_json_response(422, E.err("limit must be an integer",
                                                 E.E_VALIDATION))
    deliveries = ST.read_webhook_deliveries(r, max(1, min(limit, 200)))
    return _staging_json_response(200, E.ok({
        "config": _staging_webhook_config_payload(ST.load_webhook_config(r)),
        "deliveries": deliveries, "count": len(deliveries)}))


def _staging_webhook_config(r, teen, wheels, headers, body):
    from common import envelope as E
    if _is_production():
        return _staging_json_response(403, E.err("staging-only", E.E_FORBIDDEN))
    denied = _staging_require_role(headers, "admin")
    if denied is not None:
        return denied
    try:
        data = _staging_json(body)
        config = _validate_webhook_config(data)
    except ValueError as exc:
        return _staging_json_response(422, E.err(str(exc), E.E_VALIDATION))
    import time as _time
    config.update({"updated_by": "staging",
                   "updated_at_ms": int(_time.time() * 1000),
                   "reason": str(data.get("reason") or "")})
    ST.save_webhook_config(r, {"enabled": config["enabled"],
                               "destinations": config["destinations"]})
    teen.webhook_destinations = list(config["destinations"]) if config["enabled"] else []
    for svc in wheels.values():
        svc.webhook_destinations = list(teen.webhook_destinations)
    _staging_audit_event(r, "staging", "staging.webhooks.configure",
                         "webhooks", "staging",
                         {"enabled": config["enabled"],
                          "destinations": config["destinations"],
                          "reason": config["reason"]})
    return _staging_json_response(200, E.ok({"config": _staging_webhook_config_payload(
        ST.load_webhook_config(r))}, "webhook configuration updated"))


def _staging_admin_audit(r, headers, query):
    from common import envelope as E
    from games.teen_patti_pro.api import ADMIN_SCOPES
    from provider.games import BINDINGS, ENGINE_IDS, canonical_code
    from urllib.parse import parse_qs
    if _is_production():
        return _staging_json_response(403, E.err("staging-only", E.E_FORBIDDEN))
    denied = _staging_require_role(headers, "auditor")
    if denied is not None:
        return denied
    params = parse_qs(query or "")
    try:
        limit = int((params.get("limit") or ["100"])[0])
    except (TypeError, ValueError):
        return _staging_json_response(422, E.err("limit must be an integer",
                                                 E.E_VALIDATION))
    limit = max(1, min(limit, 200))
    entity = str((params.get("entity") or [""])[0])[:64]
    requested = str((params.get("game") or [""])[0]).strip()
    requested_code = canonical_code(requested) if requested else None
    if requested and requested_code is None:
        return _staging_json_response(404, E.err(f"unknown game {requested}",
                                                 E.E_NOT_FOUND))
    key = (headers or {}).get("X-Admin-Key", "")
    raw_scopes = ADMIN_SCOPES.get(key)
    if raw_scopes:
        allowed = {code for value in raw_scopes
                   if (code := canonical_code(value)) is not None}
    else:
        allowed = set(BINDINGS)
    if requested_code is not None and requested_code not in allowed:
        return _staging_json_response(403, E.err(
            "key is not scoped to this game", E.E_FORBIDDEN))
    codes = [requested_code] if requested_code else sorted(allowed)
    rows = []
    for code in codes:
        engine = ENGINE_IDS.get(code)
        if not engine:
            continue
        raw = r.command("LRANGE", f"stg:audit:{engine}", "0", str(limit - 1)) or []
        for item in raw:
            try:
                row = json.loads(item)
            except ValueError:
                continue
            if isinstance(row, dict) and (not entity or row.get("entity") == entity):
                rows.append(row)
    rows.sort(key=lambda row: int(row.get("timestamp", 0) or 0), reverse=True)
    rows = rows[:limit]
    return _staging_json_response(200, E.ok({"entries": rows, "count": len(rows)}))


def _staging_issue_key(r, body):
    from common import envelope as E
    from provider import dynamic_keys as DK
    if _is_production():
        return _staging_json_response(403, E.err("staging-only", E.E_FORBIDDEN))
    try:
        data = _staging_json(body)
    except ValueError as exc:
        return _staging_json_response(422, E.err(str(exc), E.E_VALIDATION))
    try:
        issued = DK.issue_provider_key(
            r, pin=data.get("pin"), label=data.get("label"),
            role=data.get("role", "operator"), games=data.get("games"),
            ttl_seconds=data.get("ttl_seconds"), audit=None)
    except DK.DynamicKeyError as exc:
        return _staging_json_response(exc.status, E.err(str(exc), exc.code))
    _staging_key_audit(r, "staging-pin", "staging.api-key.issue",
                       issued["key_id"], {k: issued[k] for k in
                                          ("label", "role", "games",
                                           "environment", "test_only",
                                           "expires_at_ms")})
    return _staging_json_response(201, E.ok(issued, "staging API key issued"))


def _staging_list_keys(r, headers):
    from common import envelope as E
    from provider import dynamic_keys as DK
    if _is_production():
        return _staging_json_response(403, E.err("staging-only", E.E_FORBIDDEN))
    denied = _staging_require_admin(headers)
    if denied is not None:
        return denied
    try:
        records = DK.list_provider_keys(r)
    except DK.DynamicKeyError as exc:
        return _staging_json_response(exc.status, E.err(str(exc), exc.code))
    return _staging_json_response(200, E.ok({"keys": records,
                                             "count": len(records)}))


def _staging_revoke_key(r, headers, body):
    from common import envelope as E
    from provider import dynamic_keys as DK
    if _is_production():
        return _staging_json_response(403, E.err("staging-only", E.E_FORBIDDEN))
    denied = _staging_require_admin(headers)
    if denied is not None:
        return denied
    try:
        data = _staging_json(body)
    except ValueError as exc:
        return _staging_json_response(422, E.err(str(exc), E.E_VALIDATION))
    try:
        revoked = DK.revoke_provider_key(
            r, data.get("key_id"), actor="staging", audit=None)
    except DK.DynamicKeyError as exc:
        return _staging_json_response(exc.status, E.err(str(exc), exc.code))
    if revoked:
        _staging_key_audit(r, "staging", "staging.api-key.revoke",
                           str(data.get("key_id") or ""),
                           {"revoked": True})
        return _staging_json_response(200, E.ok({"revoked": True}))
    return _staging_json_response(404, E.err("unknown staging API key",
                                             E.E_NOT_FOUND))


def _staging_rotate_key(r, headers, body):
    from common import envelope as E
    from provider import dynamic_keys as DK
    if _is_production():
        return _staging_json_response(403, E.err("staging-only", E.E_FORBIDDEN))
    denied = _staging_require_admin(headers)
    if denied is not None:
        return denied
    try:
        data = _staging_json(body)
    except ValueError as exc:
        return _staging_json_response(422, E.err(str(exc), E.E_VALIDATION))
    try:
        rotated = DK.rotate_provider_key(
            r, data.get("key_id"), pin=data.get("pin"),
            ttl_seconds=data.get("ttl_seconds"), actor="staging",
            audit=None)
    except DK.DynamicKeyError as exc:
        return _staging_json_response(exc.status, E.err(str(exc), exc.code))
    _staging_key_audit(r, "staging", "staging.api-key.rotate",
                       rotated["key_id"],
                       {"label": rotated["label"], "role": rotated["role"],
                        "games": rotated["games"],
                        "expires_at_ms": rotated["expires_at_ms"]})
    return _staging_json_response(201, E.ok(rotated, "staging API key rotated"))


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
    canon = _canonical(game)
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
    canon = _canonical(game)
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
        "serverTime": now_iso(), "serverTimeMs": int(_t.time() * 1000),
    })).encode()


def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/") or "/"
    query = environ.get("QUERY_STRING", "")
    if method == "GET" and path in ("/health", "/api/v1/health"):
        payload = _health_payload()
        response = json.dumps(payload).encode()
        status = 200 if payload["status"] == "ok" else 503
        start_response(f"{status} {_http_phrase(status)}", [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(response)))])
        return [response]
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
    phase1 = _phase1_auth(method, path, headers, body, environ)
    if phase1 is not None:
        status, resp_headers, payload = phase1
        start_response(f"{status} {_http_phrase(status)}",
                       [(k, v) for k, v in resp_headers.items()])
        return [payload]
    if path.startswith("/api/v1/demo/"):
        from staging.demo import dispatch as demo_dispatch
        redis = None
        try:
            redis = ST._redis()
            demo = demo_dispatch(method, path, headers, body, environ, redis)
        finally:
            if redis is not None:
                redis.close()
        if demo is not None:
            status, payload = demo
            response = json.dumps(payload).encode()
            start_response(f"{status} {_http_phrase(status)}",
                           [("Content-Type", "application/json"),
                            ("Content-Length", str(len(response)))])
            return [response]
    if (path.startswith("/api/v1/superadmin/") or
            path.startswith("/api/v1/operator/admin/")):
        from staging.economy import dispatch as phase3_dispatch
        redis = None
        try:
            redis = ST._redis()
            phase3 = phase3_dispatch(method, path, query, headers, body, environ, redis)
        finally:
            if redis is not None:
                redis.close()
        if phase3 is not None:
            status, payload = phase3
            response = json.dumps(payload).encode()
            start_response(f"{status} {_http_phrase(status)}",
                           [("Content-Type", "application/json"),
                            ("Content-Length", str(len(response)))])
            return [response]
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


if __name__ == "__main__":
    # Local development runner only. This module is the staging/UAT adapter and
    # compiles out under APP_ENV=production (see _is_production). Production runs
    # the provider API instead: python -m games.teen_patti_pro.api --confirmed
    # See docs/DEPLOYMENT.md.
    import os as _os
    from socketserver import ThreadingMixIn
    from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server

    if _os.environ.get("APP_ENV", "sandbox").strip().lower() == "production":
        raise SystemExit(
            "refusing to serve: staging/wsgi.py is the staging adapter and is "
            "disabled under APP_ENV=production.\n"
            "For production run:  python -m games.teen_patti_pro.api --confirmed")

    _host = _os.environ.get("HOST", "0.0.0.0")
    _port = int(_os.environ.get("PORT")
                or _os.environ.get("GAME_API_PORT") or 8000)

    class _ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
        # The browser polls REST while a WebSocket stays open; the default
        # single-threaded wsgiref server would serialise those and stall.
        daemon_threads = True

    class _QuietHandler(WSGIRequestHandler):
        def log_message(self, *a):
            pass

    print(f"staging server   http://{_host}:{_port}")
    print(f"  APP_ENV        {_os.environ.get('APP_ENV', 'sandbox')}")
    print(f"  redis          {_os.environ.get('REDIS_HOST', '127.0.0.1')}"
          f":{_os.environ.get('REDIS_PORT', '6379')}   (required)")
    print("  dev server     wsgiref -- single process, not for production\n")
    make_server(_host, _port, app,
                server_class=_ThreadingWSGIServer,
                handler_class=_QuietHandler).serve_forever()
