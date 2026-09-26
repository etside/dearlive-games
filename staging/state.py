"""Serverless staging state (no game-logic changes).

Per request: build services on Redis stores, load the touched room snapshots
(+ handler config), run a lazy sweep of due rooms, execute, then save rooms,
audit spill, and config back. Distributed per-room locks serialize
load-mutate-save across function instances.
"""
import json
import time
import uuid

from staging.room_codec import dumps, loads

ROOM_KEY = "stg:room:{game}:{room}"
ROOMS_SET = "stg:rooms:{game}"
LOCK_KEY = "stg:lock:{game}:{room}"
AUDIT_KEY = "stg:audit:{game}"
CFG_KEY = "stg:cfg:{game}"
HANDLER_KEY = "stg:handler"
WEBHOOK_CFG_KEY = "stg:webhooks"
WEBHOOK_LOG_KEY = "stg:webhooks-log"
WEBHOOK_LOG_CAP = 500
LOCK_TTL_MS = 15000
AUDIT_CAP = 500


def _registry():
    from games.teen_patti_pro import config as tcfg
    from games.teen_patti_pro import engine as teng
    return {
        "games.teen_patti_pro.engine.Bet": teng.Bet,
        "games.teen_patti_pro.engine.Round": teng.Round,
        "games.teen_patti_pro.engine.Room": teng.Room,
        "games.teen_patti_pro.config.TeenPattiConfig": tcfg.TeenPattiConfig,
    }


def _redis():
    from integrations.redis_store import MinimalRedis
    return MinimalRedis()


def acquire(r, game, room, timeout_s=10.0):
    token = uuid.uuid4().hex
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if r.command("SET", LOCK_KEY.format(game=game, room=room), token,
                     "NX", "PX", str(LOCK_TTL_MS)) == "OK":
            return token
        time.sleep(0.05)
    raise TimeoutError(f"room lock busy: {game}/{room}")


def release(r, game, room, token):
    if r.command("GET", LOCK_KEY.format(game=game, room=room)) == token:
        r.command("DEL", LOCK_KEY.format(game=game, room=room))


def build_services():
    """Fresh services wired to Redis (tokens/sessions/idempotency/wallet)."""
    from common.config import Settings
    from games.teen_patti_pro.config import TeenPattiConfig
    from games.teen_patti_pro.service import TeenPattiService
    from integrations.redis_store import (RedisIdempotencyStore,
                                          RedisSessionStore, RedisTokenStore)
    from staging.redis_wallet import RedisWallet

    settings = Settings.from_env()
    if settings.is_production():
        raise RuntimeError("staging adapter refuses production boot")
    r = _redis()
    wallet = RedisWallet(r)
    tokens, sessions, idem = (RedisTokenStore(r), RedisSessionStore(r),
                              RedisIdempotencyStore(r))
    cfg = TeenPattiConfig(confirmed=True)  # staging test coins are valueless
    teen = TeenPattiService(config=cfg, wallet=wallet, tokens=tokens,
                            sessions=sessions, idempotency=idem,
                            webhook_secret="staging-secret")
    return r, teen, {}


def _load_cfg(r, game, default_cfg):
    raw = r.command("GET", CFG_KEY.format(game=game))
    if raw is None:
        return default_cfg
    return loads(raw, _registry())


def _save_cfg(r, game, cfg):
    r.command("SET", CFG_KEY.format(game=game), dumps(cfg))


def load_handler_state(r):
    raw = r.command("GET", HANDLER_KEY)
    if raw is None:
        return {"game_enabled": {}, "game_packages": {}, "game_labels": {}}
    return json.loads(raw)


def save_handler_state(r, state):
    r.command("SET", HANDLER_KEY, json.dumps(state))


def default_webhook_config() -> dict:
    return {"enabled": False, "destinations": []}


def load_webhook_config(r) -> dict:
    raw = r.command("GET", WEBHOOK_CFG_KEY)
    if raw is None:
        return default_webhook_config()
    try:
        cfg = json.loads(raw)
    except ValueError:
        return default_webhook_config()
    if not isinstance(cfg, dict):
        return default_webhook_config()
    destinations = cfg.get("destinations", [])
    if not isinstance(destinations, list):
        return default_webhook_config()
    return {"enabled": bool(cfg.get("enabled", False)),
            "destinations": [str(url) for url in destinations]}


def save_webhook_config(r, config: dict) -> None:
    r.command("SET", WEBHOOK_CFG_KEY, json.dumps(config))


def spill_webhook_deliveries(r, svc) -> None:
    deliveries = getattr(getattr(svc, "webhooks", None), "deliveries", [])
    if not deliveries:
        return
    seen_key = WEBHOOK_LOG_KEY + ":ids"
    for entry in deliveries:
        key = f"{entry.get('destination','')}:{entry.get('event_id','')}"
        if not entry.get("event_id") or r.command("SISMEMBER", seen_key, key):
            continue
        r.command("LPUSH", WEBHOOK_LOG_KEY, json.dumps(entry, default=str))
        r.command("SADD", seen_key, key)
    r.command("LTRIM", WEBHOOK_LOG_KEY, "0", str(WEBHOOK_LOG_CAP - 1))


def read_webhook_deliveries(r, limit: int = 100):
    raw = r.command("LRANGE", WEBHOOK_LOG_KEY, "0",
                    str(max(0, min(limit, WEBHOOK_LOG_CAP) - 1))) or []
    out = []
    for item in raw:
        try:
            parsed = json.loads(item)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


def load_room(r, svc, game, room_id):
    raw = r.command("GET", ROOM_KEY.format(game=game, room=room_id))
    if raw is None:
        return
    rooms = getattr(svc, "rooms", None)
    if rooms is None:
        return
    room = loads(raw, _registry())
    rooms[room_id] = room


def save_room(r, svc, game, room_id):
    rooms = getattr(svc, "rooms", None)
    if rooms is None or room_id not in rooms:
        return
    r.command("SET", ROOM_KEY.format(game=game, room=room_id),
              dumps(rooms[room_id]))
    r.command("SADD", ROOMS_SET.format(game=game), room_id)


def spill_audit(r, svc, game):
    entries = getattr(getattr(svc, "audit", None), "entries", [])
    if not entries:
        return
    key = AUDIT_KEY.format(game=game)
    seen_key = key + ":ids"
    for e in entries:
        aid = str(e.get("audit_id", ""))
        if not aid or r.command("SISMEMBER", seen_key, aid):
            continue
        r.command("LPUSH", key, json.dumps(e, default=str))
        r.command("SADD", seen_key, aid)
    r.command("LTRIM", key, "0", str(AUDIT_CAP - 1))


def lazy_sweep(r, teen, wheels=None, max_rooms=20):
    """Close->result->settle due rooms (idempotent service sweeps).

    ``wheels`` is accepted and ignored: the wheel games are retired, and the
    single game is swept below. The parameter stays so existing callers do not
    have to change.
    """
    for game, svc in [("teen-patti-pro", teen)]:
        try:
            members = r.command("SMEMBERS", ROOMS_SET.format(game=game)) or []
        except Exception:
            continue
        for room_id in members[:max_rooms]:
            token = None
            try:
                token = acquire(r, game, room_id, timeout_s=3.0)
                load_room(r, svc, game, room_id)
                svc.sweep()
                save_room(r, svc, game, room_id)
                spill_audit(r, svc, game)
            except Exception:
                continue
            finally:
                if token:
                    release(r, game, room_id, token)
