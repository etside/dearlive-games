import json
import time
import uuid
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlparse

GAME_SLUGS = {"teen-patti-pro", "greedy-monkey", "baby-king"}
CURRENCY_CODES = {"USD", "BDT", "INR"}
TRANSACTION_TYPES = {
    "COIN_PURCHASE", "BET_DEBIT", "WIN_CREDIT", "BONUS", "ADMIN_CREDIT",
    "WITHDRAWAL_HOLD", "WITHDRAWAL_COMPLETED", "REFUND",
}


def _json(value):
    return json.dumps(value, separators=(",", ":"), default=str)


def _decode(value):
    try:
        return json.loads(value) if value else None
    except (TypeError, ValueError):
        return None


def _list(r, key):
    return [_decode(item) for item in (r.command("LRANGE", key, "0", "4999") or [])
            if _decode(item) is not None]


def _write_list(r, key, rows):
    r.command("DEL", key)
    for row in rows:
        r.command("LPUSH", key, _json(row))
    r.command("LTRIM", key, "0", "4999")


def _append(r, key, row):
    r.command("LPUSH", key, _json(row))
    r.command("LTRIM", key, "0", "4999")
    return row


def _find(rows, **fields):
    return next((row for row in rows if all(row.get(key) == value
                                            for key, value in fields.items())), None)


def _scoped(r, operator_id, name):
    return f"phase3:operator:{operator_id}:{name}"


def _global(name):
    return f"phase3:global:{name}"


def _audit(r, scope, operator_id, actor, action, target_type, target_id,
           before, after, ip):
    row = {
        "id": str(uuid.uuid4()), "scope": scope, "operator_id": operator_id,
        "actor": actor, "action": action, "target_type": target_type,
        "target_id": str(target_id), "before": before, "after": after,
        "ip": ip, "created_at": int(time.time()),
    }
    if scope == "operator":
        return _append(r, _scoped(r, operator_id, "audit"), row)
    return _append(r, _global("superadmin_audit"), row)


def _response(status, data=None, message="OK", code="OK"):
    from common import envelope as E
    if status >= 400:
        return status, E.err(message, code)
    return status, E.ok(data or {}, message)


def _error(status, message, code):
    return _response(status, message=message, code=code)


def _body(body):
    from staging.wsgi import _staging_json
    try:
        return _staging_json(body), None
    except ValueError as exc:
        return None, str(exc)


def _decimal(value, field):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{field} must be a number")
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def _currency_code(value):
    code = str(value or "").upper()
    if code not in CURRENCY_CODES:
        raise ValueError("currency is not supported")
    return code


def _operator_rows(r, operator_id, name):
    return _list(r, _scoped(r, operator_id, name))


def dispatch(method, path, query, headers, body, environ, r):
    parsed = urlparse(path)
    clean = parsed.path.rstrip("/") or "/"
    params = parse_qs(query or "")
    ip = str(environ.get("REMOTE_ADDR", ""))
    operator_id = str(environ.get("operator_id", "global"))
    scope = environ.get("operator", {}).get("scope", "")
    try:
        if scope == "superadmin" and clean.startswith("/api/v1/superadmin/"):
            return _superadmin(r, method, clean, params, headers, body, ip)
        if scope == "operator" and clean.startswith("/api/v1/operator/admin/"):
            return _operator(r, method, clean, params, body, operator_id, ip)
    except ValueError as exc:
        return _error(422, str(exc), "VALIDATION_ERROR")
    return None


def _superadmin(r, method, path, params, headers, body, ip):
    segments = path.strip("/").split("/")
    if path == "/api/v1/superadmin/dashboard" and method == "GET":
        operators = _list(r, _global("operators"))
        return _response(200, {"operators": len(operators),
                               "active_operators": sum(row.get("status") == "active" for row in operators),
                               "online_devices": len([row for row in _list(r, _global("devices")) if row.get("status") == "active"]),
                               "pending_operations": 0, "kpis": {}})
    if path == "/api/v1/superadmin/operators" and method == "GET":
        rows = _list(r, _global("operators"))
        return _response(200, {"operators": rows, "count": len(rows)})
    if path.startswith("/api/v1/superadmin/operators/") and len(segments) == 6:
        operator_id, action = segments[4], segments[5]
        row = _find(_list(r, _global("operators")), id=operator_id)
        if row is None:
            return _error(404, "operator not found", "NOT_FOUND")
        if action in ("suspend", "activate") and method == "POST":
            before = dict(row)
            row["status"] = "suspended" if action == "suspend" else "active"
            _write_list(r, _global("operators"), [item for item in _list(r, _global("operators")) if item.get("id") != operator_id] + [row])
            _audit(r, "superadmin", operator_id, "superadmin", f"operator.{action}", "operator", operator_id, before, row, ip)
            return _response(200, row)
    if path.startswith("/api/v1/superadmin/operators/") and len(segments) == 6 and segments[5] == "api-keys" and method == "GET":
        operator_id = segments[4]
        if _find(_list(r, _global("operators")), id=operator_id) is None:
            return _error(404, "operator not found", "NOT_FOUND")
        rows = [row for row in _list(r, _global("api_keys")) if row.get("operator_id") == operator_id]
        return _response(200, {"api_keys": rows, "count": len(rows)})
    if path.startswith("/api/v1/superadmin/operators/") and len(segments) == 7 and segments[5] == "api-keys" and method == "POST":
        operator_id, action = segments[4], segments[6]
        if _find(_list(r, _global("operators")), id=operator_id) is None:
            return _error(404, "operator not found", "NOT_FOUND")
        data, error = _body(body)
        if error:
            return _error(422, error, "VALIDATION_ERROR")
        if action == "rotate":
            return _error(404, "api key not found", "NOT_FOUND")
        if action == "revoke":
            rows = _list(r, _global("api_keys"))
            target = str(data.get("key_id", ""))
            row = _find(rows, operator_id=operator_id, id=target)
            if row is None:
                return _error(404, "api key not found", "NOT_FOUND")
            old = dict(row)
            row["revoked"] = True
            _write_list(r, _global("api_keys"), rows)
            _audit(r, "superadmin", operator_id, "superadmin", "api_key.revoke", "api_key", target, old, row, ip)
            return _response(200, row)
        return _error(404, "api key route not found", "NOT_FOUND")
    if path.startswith("/api/v1/superadmin/operators/") and len(segments) == 8 and segments[5] == "api-keys" and method == "POST":
        operator_id, target, action = segments[4], segments[6], segments[7]
        if _find(_list(r, _global("operators")), id=operator_id) is None:
            return _error(404, "operator not found", "NOT_FOUND")
        rows = _list(r, _global("api_keys"))
        row = _find(rows, operator_id=operator_id, id=target)
        if row is None:
            return _error(404, "api key not found", "NOT_FOUND")
        if action == "revoke":
            old = dict(row)
            row["revoked"] = True
            _write_list(r, _global("api_keys"), rows)
            _audit(r, "superadmin", operator_id, "superadmin", "api_key.revoke", "api_key", target, old, row, ip)
            return _response(200, row)
        return _error(404, "api key route not found", "NOT_FOUND")
    if path == "/api/v1/superadmin/locked-fields" and method == "GET":
        rows = _list(r, _global("locked_fields"))
        return _response(200, {"locked_fields": rows, "count": len(rows)})
    if path == "/api/v1/superadmin/locked-fields" and method == "POST":
        data, error = _body(body)
        if error:
            return _error(422, error, "VALIDATION_ERROR")
        game = str(data.get("game_slug", ""))
        field = str(data.get("field_name", ""))
        if game not in GAME_SLUGS or not field:
            return _error(422, "game_slug and field_name are required", "VALIDATION_ERROR")
        before = _list(r, _global("locked_fields"))
        row = {"id": str(uuid.uuid4()), "game_slug": game, "field_name": field,
               "locked_by": "superadmin", "locked_at": int(time.time())}
        rows = [item for item in before if not (item.get("game_slug") == game and item.get("field_name") == field)]
        _write_list(r, _global("locked_fields"), rows + [row])
        _audit(r, "superadmin", "global", "superadmin", "locked_field.create", "locked_field", row["id"], before, row, ip)
        return _response(201, row)
    if path.startswith("/api/v1/superadmin/locked-fields/") and method == "DELETE":
        target = path.rsplit("/", 1)[-1]
        before = _list(r, _global("locked_fields"))
        row = _find(before, id=target)
        if row is None:
            return _error(404, "locked field not found", "NOT_FOUND")
        _write_list(r, _global("locked_fields"), [item for item in before if item.get("id") != target])
        _audit(r, "superadmin", "global", "superadmin", "locked_field.delete", "locked_field", target, row, None, ip)
        return _response(200, {"deleted": True})
    if path == "/api/v1/superadmin/global-config" and method == "GET":
        return _response(200, {"config": _list(r, _global("platform_config"))})
    if path == "/api/v1/superadmin/global-config" and method == "PUT":
        data, error = _body(body)
        if error:
            return _error(422, error, "VALIDATION_ERROR")
        before = _list(r, _global("platform_config"))
        rows = [row for row in before if row.get("key") != data.get("key")]
        row = {"id": str(uuid.uuid4()), "key": str(data.get("key", "")),
               "value_json": data.get("value", {}), "updated_at": int(time.time())}
        if not row["key"]:
            return _error(422, "key is required", "VALIDATION_ERROR")
        _write_list(r, _global("platform_config"), rows + [row])
        _audit(r, "superadmin", "global", "superadmin", "platform_config.put", "platform_config", row["key"], before, row, ip)
        return _response(200, row)
    if path == "/api/v1/superadmin/devices" and method == "GET":
        rows = _list(r, _global("devices"))
        return _response(200, {"devices": rows, "count": len(rows)})
    if path == "/api/v1/superadmin/devices/live" and method == "GET":
        return _response(200, {"devices": _list(r, _global("devices")), "feed": "polling"})
    if path == "/api/v1/superadmin/devices/stats" and method == "GET":
        rows = _list(r, _global("devices"))
        return _response(200, {"active": sum(row.get("status") == "active" for row in rows),
                               "total": len(rows)})
    if path.startswith("/api/v1/superadmin/devices/") and path.endswith("/kick") and method == "POST":
        target = path.split("/")[-2]
        before = _list(r, _global("devices"))
        row = _find(before, id=target)
        if row is None:
            return _error(404, "device not found", "NOT_FOUND")
        before_copy = dict(row)
        row.update({"status": "kicked", "ended_at": int(time.time())})
        _write_list(r, _global("devices"), [item for item in before if item.get("id") != target] + [row])
        _audit(r, "superadmin", row.get("operator_id", "global"), "superadmin", "device.kick", "device_session", target, before_copy, row, ip)
        return _response(200, row)
    if path == "/api/v1/superadmin/audit" and method == "GET":
        rows = _list(r, _global("superadmin_audit"))
        return _response(200, {"audit": rows, "count": len(rows)})
    if path == "/api/v1/superadmin/health" and method == "GET":
        return _response(200, {"status": "ok", "redis": True, "devices": len(_list(r, _global("devices")))})
    if path == "/api/v1/superadmin/currencies" and method == "GET":
        rows = _list(r, _global("currencies"))
        return _response(200, {"currencies": rows, "count": len(rows)})
    if path == "/api/v1/superadmin/currencies" and method == "POST":
        data, error = _body(body)
        if error:
            return _error(422, error, "VALIDATION_ERROR")
        code = str(data.get("code", "")).upper()
        if code not in CURRENCY_CODES or not data.get("name") or not data.get("symbol"):
            return _error(422, "code, name, and symbol are required", "VALIDATION_ERROR")
        before = _list(r, _global("currencies"))
        if _find(before, code=code):
            return _error(409, "currency already exists", "CONFLICT")
        row = {"id": str(uuid.uuid4()), "code": code, "name": data["name"],
               "symbol": data["symbol"], "decimal_places": data.get("decimal_places", 2),
               "enabled": data.get("enabled", True), "display_order": data.get("display_order", 0)}
        _write_list(r, _global("currencies"), before + [row])
        _audit(r, "superadmin", "global", "superadmin", "currency.create", "currency", row["id"], before, row, ip)
        return _response(201, row)
    if path.startswith("/api/v1/superadmin/currencies/") and method == "PUT":
        target = path.rsplit("/", 1)[-1]
        data, error = _body(body)
        if error:
            return _error(422, error, "VALIDATION_ERROR")
        before = _list(r, _global("currencies"))
        row = _find(before, id=target)
        if row is None:
            return _error(404, "currency not found", "NOT_FOUND")
        old = dict(row)
        row.update({key: data[key] for key in ("name", "symbol", "decimal_places", "enabled", "display_order") if key in data})
        _write_list(r, _global("currencies"), [item for item in before if item.get("id") != target] + [row])
        _audit(r, "superadmin", "global", "superadmin", "currency.update", "currency", target, old, row, ip)
        return _response(200, row)
    return _error(404, "superadmin route not found", "NOT_FOUND")


def _operator(r, method, path, params, body, operator_id, ip):
    segments = path.strip("/").split("/")
    if path == "/api/v1/operator/admin/dashboard" and method == "GET":
        locked = [{"game_slug": row.get("game_slug"), "field_name": row.get("field_name"),
                   "locked": True, "editable": False}
                  for row in _list(r, _global("locked_fields"))]
        return _response(200, {"active_games": 0, "live_rounds": 0,
                               "total_bets_today": 0, "net_revenue_today": 0,
                               "online_players": 0, "pending_withdrawals": 0,
                               "balance": 0, "locked_fields": locked})
    if path == "/api/v1/operator/admin/audit" and method == "GET":
        rows = _operator_rows(r, operator_id, "audit")
        return _response(200, {"audit": rows, "count": len(rows)})
    if path == "/api/v1/operator/admin/currency" and method == "GET":
        row = _find(_operator_rows(r, operator_id, "currency"), operator_id=operator_id)
        return _response(200, row or {"operator_id": operator_id, "code": None})
    if path == "/api/v1/operator/admin/currency" and method == "PUT":
        data, error = _body(body)
        if error:
            return _error(422, error, "VALIDATION_ERROR")
        code = _currency_code(data.get("code"))
        before = _operator_rows(r, operator_id, "currency")
        row = {"id": str(uuid.uuid4()), "operator_id": operator_id, "code": code,
               "updated_at": int(time.time())}
        _write_list(r, _scoped(r, operator_id, "currency"), [item for item in before if item.get("operator_id") != operator_id] + [row])
        _audit(r, "operator", operator_id, operator_id, "currency.put", "currency", code, before, row, ip)
        return _response(200, row)
    if path == "/api/v1/operator/admin/coin-config" and method == "GET":
        rows = [row for row in _operator_rows(r, operator_id, "coin_config") if row.get("operator_id") == operator_id]
        return _response(200, rows[0] if rows else None)
    if path == "/api/v1/operator/admin/coin-config" and method == "PUT":
        data, error = _body(body)
        if error:
            return _error(422, error, "VALIDATION_ERROR")
        required = ("name", "symbol", "coin_to_currency_rate", "min_purchase", "max_purchase")
        if any(key not in data for key in required):
            return _error(422, "coin config fields are required", "VALIDATION_ERROR")
        rate = _decimal(data["coin_to_currency_rate"], "coin_to_currency_rate")
        if rate <= 0 or _decimal(data["min_purchase"], "min_purchase") < 0 or _decimal(data["max_purchase"], "max_purchase") < 0:
            return _error(422, "coin config values are invalid", "VALIDATION_ERROR")
        before = _operator_rows(r, operator_id, "coin_config")
        old = next((item for item in before if item.get("operator_id") == operator_id), None)
        row = {"id": old["id"] if old else str(uuid.uuid4()), "operator_id": operator_id,
               "name": data["name"], "symbol": data["symbol"],
               "coin_to_currency_rate": str(rate), "min_purchase": str(data["min_purchase"]),
               "max_purchase": str(data["max_purchase"]), "enabled": bool(data.get("enabled", True)),
               "updated_at": int(time.time())}
        _write_list(r, _scoped(r, operator_id, "coin_config"), [item for item in before if item.get("id") != row["id"]] + [row])
        _audit(r, "operator", operator_id, operator_id, "coin_config.put", "coin_config", row["id"], old, row, ip)
        return _response(200, row)
    if path == "/api/v1/operator/admin/wallets" and method == "GET":
        rows = [row for row in _operator_rows(r, operator_id, "wallets") if row.get("operator_id") == operator_id]
        return _response(200, {"wallets": rows, "count": len(rows)})
    if path.startswith("/api/v1/operator/admin/wallets/") and method == "GET":
        target = path.rsplit("/", 1)[-1]
        row = _find(_operator_rows(r, operator_id, "wallets"), operator_id=operator_id, player_id=target)
        return _response(200, row) if row else _error(404, "wallet not found", "NOT_FOUND")
    if path.startswith("/api/v1/operator/admin/wallets/") and path.endswith("/adjust") and method == "POST":
        player_id = path.split("/")[-2]
        data, error = _body(body)
        if error:
            return _error(422, error, "VALIDATION_ERROR")
        if not data.get("reason") or not data.get("idempotency_key") or data.get("type") not in ("credit", "debit"):
            return _error(422, "reason, type, and idempotency_key are required", "VALIDATION_ERROR")
        wallets = _operator_rows(r, operator_id, "wallets")
        wallet = _find(wallets, operator_id=operator_id, player_id=player_id)
        if wallet is None:
            return _error(404, "wallet not found", "NOT_FOUND")
        transactions = _operator_rows(r, operator_id, "transactions")
        existing = _find(transactions, operator_id=operator_id, idempotency_key=data["idempotency_key"])
        if existing:
            return _response(200, existing)
        amount = _decimal(data.get("amount"), "amount")
        if amount <= 0:
            return _error(422, "amount must be positive", "VALIDATION_ERROR")
        before = float(wallet.get("balance", 0))
        after_decimal = Decimal(str(before)) + (amount if data["type"] == "credit" else -amount)
        if after_decimal < 0:
            return _error(422, "insufficient balance", "INSUFFICIENT_BALANCE")
        before_copy = dict(wallet)
        wallet["balance"] = str(after_decimal)
        wallet["updated_at"] = int(time.time())
        transaction = {"id": str(uuid.uuid4()), "operator_id": operator_id,
                       "player_id": player_id, "wallet_id": wallet.get("id"),
                       "type": "ADMIN_CREDIT" if data["type"] == "credit" else "BET_DEBIT",
                       "amount": str(amount), "balance_before": str(before),
                       "balance_after": str(after_decimal), "reason": data["reason"],
                       "idempotency_key": data["idempotency_key"], "actor_id": operator_id,
                       "created_at": int(time.time())}
        _write_list(r, _scoped(r, operator_id, "wallets"), [item for item in wallets if item.get("id") != wallet.get("id")] + [wallet])
        _append(r, _scoped(r, operator_id, "transactions"), transaction)
        _audit(r, "operator", operator_id, operator_id, "wallet.adjust", "wallet", wallet.get("id"), before_copy, wallet, ip)
        return _response(200, transaction)
    if path.startswith("/api/v1/operator/admin/wallets/") and path.endswith(("/freeze", "/unfreeze")) and method == "POST":
        player_id = path.split("/")[-2]
        wallets = _operator_rows(r, operator_id, "wallets")
        wallet = _find(wallets, operator_id=operator_id, player_id=player_id)
        if wallet is None:
            return _error(404, "wallet not found", "NOT_FOUND")
        old = dict(wallet)
        wallet["frozen"] = path.endswith("/freeze")
        wallet["updated_at"] = int(time.time())
        _write_list(r, _scoped(r, operator_id, "wallets"), [item for item in wallets if item.get("id") != wallet.get("id")] + [wallet])
        _audit(r, "operator", operator_id, operator_id, "wallet.freeze" if wallet["frozen"] else "wallet.unfreeze", "wallet", wallet.get("id"), old, wallet, ip)
        return _response(200, wallet)
    if path == "/api/v1/operator/admin/players" and method == "GET":
        rows = [row for row in _operator_rows(r, operator_id, "players") if row.get("operator_id") == operator_id]
        for key in ("status", "vip_tier", "country"):
            if params.get(key):
                rows = [row for row in rows if str(row.get(key)) == params[key][0]]
        search = (params.get("search") or [""])[0].lower()
        if search:
            rows = [row for row in rows if search in " ".join(str(row.get(key, "")) for key in ("username", "email", "external_id")).lower()]
        return _response(200, {"players": rows, "count": len(rows)})
    if path.startswith("/api/v1/operator/admin/players/") and method == "GET" and len(segments) == 6:
        target = segments[-1]
        row = _find(_operator_rows(r, operator_id, "players"), operator_id=operator_id, id=target)
        return _response(200, row) if row else _error(404, "player not found", "NOT_FOUND")
    if path.startswith("/api/v1/operator/admin/players/") and method == "PUT" and len(segments) == 6:
        target = segments[-1]
        data, error = _body(body)
        if error:
            return _error(422, error, "VALIDATION_ERROR")
        rows = _operator_rows(r, operator_id, "players")
        row = _find(rows, operator_id=operator_id, id=target)
        if row is None:
            return _error(404, "player not found", "NOT_FOUND")
        old = dict(row)
        row.update({key: data[key] for key in ("username", "email", "phone", "country", "currency", "vip_tier_id") if key in data})
        _write_list(r, _scoped(r, operator_id, "players"), rows)
        _audit(r, "operator", operator_id, operator_id, "player.update", "player", target, old, row, ip)
        return _response(200, row)
    if path.startswith("/api/v1/operator/admin/players/") and method == "POST" and len(segments) == 7:
        target, action = segments[-2], segments[-1]
        rows = _operator_rows(r, operator_id, "players")
        row = _find(rows, operator_id=operator_id, id=target)
        if row is None:
            return _error(404, "player not found", "NOT_FOUND")
        data, error = _body(body)
        if error:
            return _error(422, error, "VALIDATION_ERROR")
        old = dict(row)
        if action in ("suspend", "ban"):
            row["status"] = "suspended" if action == "suspend" else "banned"
        elif action == "vip":
            row["vip_tier_id"] = data.get("vip_tier_id")
        elif action == "notes":
            row.setdefault("notes", []).append({"note": data.get("note", ""), "created_at": int(time.time())})
        else:
            return _error(404, "player route not found", "NOT_FOUND")
        _write_list(r, _scoped(r, operator_id, "players"), rows)
        _audit(r, "operator", operator_id, operator_id, f"player.{action}", "player", target, old, row, ip)
        return _response(200, row)
    if path == "/api/v1/operator/admin/transactions" and method == "GET":
        rows = [row for row in _operator_rows(r, operator_id, "transactions") if row.get("operator_id") == operator_id]
        for key in ("type", "player_id"):
            if params.get(key):
                rows = [row for row in rows if str(row.get(key)) == params[key][0]]
        return _response(200, {"transactions": rows, "count": len(rows)})
    return _error(404, "operator route not found", "NOT_FOUND")
