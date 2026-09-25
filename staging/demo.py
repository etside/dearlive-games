import hashlib
import json
import os
import secrets
import time
import uuid
from urllib.parse import urlparse

GAMES = {"teen-patti-pro", "greedy-monkey", "baby-king"}
CURRENCIES = {"USD", "BDT", "INR"}


def _ttl(value, default):
    raw = str(value or default).lower()
    if raw.isdigit():
        return int(raw)
    if raw.endswith("s"):
        return int(raw[:-1])
    if raw.endswith("h"):
        return int(raw[:-1]) * 3600
    if raw.endswith("m"):
        return int(raw[:-1]) * 60
    return int(default)


def _balance():
    try:
        return str(os.environ.get("DEMO_STARTING_BALANCE", "10000"))
    except Exception:
        return "0"


def _ip_hash(ip):
    return hashlib.sha256((ip or "unknown").encode()).hexdigest()


def _data(r, session_id):
    raw = r.command("GET", f"phase4:demo:{session_id}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _response(status, data, message="OK"):
    from common import envelope as E
    return status, E.ok(data, message)


def dispatch(method, path, headers, body, environ, r):
    parsed = urlparse(path)
    clean = parsed.path.rstrip("/") or "/"
    if clean == "/api/v1/demo/sessions" and method == "POST":
        return _create(headers, body, environ, r)
    parts = clean.strip("/").split("/")
    if len(parts) in (5, 6) and parts[:4] == ["api", "v1", "demo", "sessions"]:
        return _session(parts[4], method, clean, headers, r)
    return None


def _create(headers, body, environ, r):
    from common import envelope as E
    try:
        data = json.loads(body or b"{}")
    except (TypeError, ValueError):
        return _response(422, {}, "invalid JSON")
    if not isinstance(data, dict):
        return _response(422, {}, "body must be an object")
    game = str(data.get("game_slug", ""))
    currency = str(data.get("currency", "USD")).upper()
    lang = str(data.get("lang", "EN"))[:8]
    if game not in GAMES or currency not in CURRENCIES:
        return _response(422, {}, "game_slug or currency is invalid")
    ip = str(headers.get("X-Forwarded-For", "")).split(",")[0].strip() or str(environ.get("REMOTE_ADDR", "unknown"))
    rate_key = f"phase4:demo:rate:{_ip_hash(ip)}"
    try:
        count = int(r.command("INCR", rate_key) or 0)
        if count == 1:
            r.command("EXPIRE", rate_key, "3600")
        if count > 5:
            return _response(429, {}, "demo rate limit exceeded")
    except Exception:
        return _response(503, {}, "demo backend unavailable")
    now = int(time.time())
    expires = now + _ttl(os.environ.get("DEMO_SESSION_TTL", "30m"), 1800)
    session_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    balance = _balance()
    record = {"id": session_id, "game_slug": game, "starting_balance": balance,
              "current_balance": balance, "currency": currency, "lang": lang,
              "return_url": str(data.get("return_url", ""))[:512],
              "created_at": now, "expires_at": expires, "ip_hash": _ip_hash(ip),
              "status": "active", "demo_token_hash": hashlib.sha256(token.encode()).hexdigest()}
    r.command("SET", f"phase4:demo:{session_id}", json.dumps(record), "EX", str(max(1, expires - now)))
    return _response(201, {"session_id": session_id, "demo_token": token,
                           "starting_balance": balance, "expires_at": expires,
                           "game_slug": game, "currency": currency, "lang": lang})


def _session(session_id, method, path, headers, r):
    record = _data(r, session_id)
    if record is None:
        return _response(404, {}, "demo session not found")
    token = str(headers.get("X-Demo-Token", ""))
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    if not token or not secrets.compare_digest(token_hash, record.get("demo_token_hash", "")):
        return _response(401, {}, "demo token required")
    if record.get("status") != "active" or int(record.get("expires_at", 0)) <= int(time.time()):
        record["status"] = "expired" if int(record.get("expires_at", 0)) <= int(time.time()) else record.get("status")
        r.command("SET", f"phase4:demo:{session_id}", json.dumps(record), "EX", "60")
        return _response(410, {"session_id": session_id, "status": record["status"]}, "demo session expired")
    if method == "GET":
        return _response(200, {key: value for key, value in record.items() if key != "demo_token_hash"})
    if method == "POST" and path.endswith("/close"):
        record["status"] = "closed"
        r.command("SET", f"phase4:demo:{session_id}", json.dumps(record), "EX", "60")
        return _response(200, {"session_id": session_id, "status": "closed"})
    return _response(405, {}, "method not allowed")
