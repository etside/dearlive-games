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
LOCK_TTL_MS = 15000
AUDIT_CAP = 500


def _registry():
    from games.teen_patti_pro import config as tcfg
    from games.teen_patti_pro import engine as teng
    from games.wheel_common import service as wsvc
    return {
        "games.teen_patti_pro.engine.Bet": teng.Bet,
        "games.teen_patti_pro.engine.Round": teng.Round,
        "games.teen_patti_pro.engine.Room": teng.Room,
        "games.teen_patti_pro.config.TeenPattiConfig": tcfg.TeenPattiConfig,
        "games.wheel_common.service.WheelBet": wsvc.WheelBet,
        "games.wheel_common.service.WheelRound": wsvc.WheelRound,
        "games.wheel_common.service.WheelRoom": wsvc.WheelRoom,
        "games.wheel_common.service.WheelConfig": wsvc.WheelConfig,
        "games.wheel_common.service.WheelOption": wsvc.WheelOption,
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
    from games.wheel_common.configs import (baby_king_config, greedy_config,
                                            greedy_lion_config)
    from games.wheel_common.service import WheelService
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
    wheels = {}
    for mk in (greedy_config, baby_king_config, greedy_lion_config):
        wc = mk()
        wc.confirmed = True
        wheels[wc.game_id] = WheelService(
            config=wc, wallet=RedisWallet(r),
            tokens=RedisTokenStore(r), sessions=RedisSessionStore(r),
            idempotency=RedisIdempotencyStore(r), webhook_secret="staging-secret")
    return r, teen, wheels


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


def lazy_sweep(r, teen, wheels, max_rooms=20):
    """Close->result->settle due rooms (idempotent service sweeps)."""
    for game, svc in [("teen-patti-pro", teen), *[(g, s) for g, s in wheels.items()]]:
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
