"""Shared wheel-game service (Game 1 Greedy Monkey + Game 2 wheel slot).

Both games run the SAME server-authoritative flow; only game_id, options,
and theme differ (exactly like DearLive current: one WheelDriver, many
codes). Covers BRD G1-FR-01..10 / G2-FR-01..09 (bets, totals, close,
authoritative HMAC result, settlement, history, recent strip, earnings,
HOT flags, autobet/autoplay where approved):

- Lifecycle: UPCOMING -> BETTING_OPEN -> BETTING_CLOSED ->
  RESULT_PROCESSING -> RESULT -> SETTLED -> CLOSED (common/lifecycle.py).
- Money order (same as Teen Patti): TBC gate -> validate -> balance ->
  window -> idempotency-claim -> atomic debit -> create bet -> publish.
  Post-debit failure ALWAYS compensates (stake refunded, never lost).
- Settlement exactly-once: UNIQUE settlement.bet_id + settle lock +
  idempotent wallet credit. Payout rule = stake x winning multiplier
  (DearLive current WheelDriver payoutMultiplier; TBC flag PAYOUT-RULE
  until business approves alternatives — never silently changed).
- Auto Bet/Auto Play: per-player config {option, amount, rounds_left};
  executed server-side at round start with idempotency key
  auto:{player}:{round_id}. Only when config.auto_allowed (approved).
"""
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from common import envelope as E
from common.audit import AuditLog
from common.idempotency import (IdempotencyConflict, IdempotencyStore,
                                MemoryIdempotencyStore)
from common.lifecycle import RoundStatus, LifecycleError, transition
from common.session import (MemorySessionStore, MemoryTokenStore, SessionStore,
                            TokenError, TokenStore)
from common.wallet import (InsufficientBalance, MemoryWallet, WalletAdapter,
                           WalletError)
from common.wheel import wheel_outcome
from common.webhooks import build_event, MemoryDeliveryLog

# Reuse the Teen Patti service error type so the shared API layer's
# `except ServiceError` handles wheel errors identically (same envelope).
from games.teen_patti_pro.service import ServiceError as _BaseServiceError


class ServiceError(_BaseServiceError):
    pass


@dataclass
class WheelOption:
    option_id: str
    name: str
    weight: float = 1.0
    multiplier: float = 1.0
    icon: str = ""
    color_hex: str = "#ffffff"
    hot: bool = False  # HOT/recommendation flag (operator-set, TBC HOT-RULE)
    is_active: bool = True


@dataclass
class WheelConfig:
    game_id: str
    version: str = "wheel-1.0.0-tbc"
    confirmed: bool = False
    tbc: tuple = ("W-OPTS", "W-PAYOUT-RULE", "W-DURATION", "W-AUTO", "W-HOT",
                  "W-EARNINGS")
    round_duration_ms: int = 30_000
    betting_duration_ms: int = 20_000
    denoms: tuple = (20, 100, 500, 1000)
    min_bet: int = 20
    max_bet: int = 100_000
    max_bets_per_player_per_round: int = 50
    auto_allowed: bool = False  # Auto Bet/Auto Play only where approved
    payout_rule: str = "stake_x_multiplier"  # TBC; DearLive-current default
    options: tuple = ()
    event_log_cap: int = 500


@dataclass
class WheelBet:
    bet_id: str
    round_id: str
    player_id: str
    option_id: str
    amount: int
    decision_time_ms: int
    idempotency_key: str
    status: str = "accepted"
    auto: bool = False


@dataclass
class WheelRound:
    round_id: str
    round_no: int
    status: RoundStatus = RoundStatus.UPCOMING
    created_at_ms: int = 0
    betting_end_at_ms: int = 0
    server_seed: str = ""
    server_seed_hash: str = ""
    client_seed: str = "dearlive"
    nonce: int = 0
    winner: Optional[dict] = None
    bets: List[WheelBet] = field(default_factory=list)
    settlements: List[dict] = field(default_factory=list)
    config_version: str = ""
    events: List[dict] = field(default_factory=list)
    _seq: int = 0
    _settled: bool = False

    def emit(self, kind: str, data: dict, now_ms: int) -> dict:
        self._seq += 1
        ev = {"seq": self._seq, "kind": kind, "serverTime": now_ms, **data}
        self.events.append(ev)
        return ev


def _h(payload: dict) -> str:
    import json
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class WheelRoom:
    def __init__(self, room_id: str, config: WheelConfig):
        self.room_id = room_id
        self.config = config
        self.lock = threading.Lock()
        self.round: Optional[WheelRound] = None
        self._round_no = 0
        self._bet_seq = 0
        self.members: Dict[str, dict] = {}
        self.recent: List[dict] = []  # recent-result strip (capped)
        self.autobets: Dict[str, dict] = {}  # player -> {option_id, amount, rounds_left}
        self.earnings: Dict[str, dict] = {}  # player -> {day, net}
        self.bet_history: List[dict] = []


class WheelService:
    def __init__(self, config: WheelConfig, wallet: WalletAdapter = None,
                 idempotency: IdempotencyStore = None, tokens: TokenStore = None,
                 sessions: SessionStore = None, webhook_destinations=None,
                 webhook_secret: str = "dev-secret"):
        self.config = config
        self.game_id = config.game_id
        self.wallet = wallet or MemoryWallet()
        self.idempotency = idempotency or MemoryIdempotencyStore()
        self.tokens = tokens or MemoryTokenStore()
        self.sessions = sessions or MemorySessionStore()
        self.audit = AuditLog()
        self.webhooks = MemoryDeliveryLog()
        self.webhook_destinations = webhook_destinations or []
        self.webhook_secret = webhook_secret
        self.rooms: Dict[str, WheelRoom] = {}
        self.settled_bet_ids = set()
        self._settle_lock = threading.Lock()
        self._now = lambda: int(time.time() * 1000)

    # -- internal --
    def _room(self, room_id: str) -> WheelRoom:
        if room_id not in self.rooms:
            self.rooms[room_id] = WheelRoom(room_id, self.config)
        return self.rooms[room_id]

    def _fire(self, kind: str, data: dict):
        ev = build_event(kind, data)
        for dest in self.webhook_destinations:
            self.webhooks.record(dest, ev, "queued")
        return ev

    def _require_confirmed(self):
        if not self.config.confirmed:
            raise ServiceError(E.E_TBC_BLOCKED,
                               f"Config {self.config.version} unconfirmed — real-money blocked")

    def _options(self, room: WheelRoom) -> List[WheelOption]:
        return [o for o in room.config.options if o.is_active] or \
               [o for o in self.config.options if o.is_active]

    # -- sessions (launch token redeem; game-scoped) --
    def open_session(self, token: str) -> dict:
        try:
            lt = self.tokens.redeem(token)
        except TokenError as exc:
            raise ServiceError("INVALID_TOKEN", str(exc))
        if lt.game_id != self.game_id:
            raise ServiceError(E.E_VALIDATION, f"Token not issued for {self.game_id}")
        sess = self.sessions.create(lt.player_id, lt.room_id, lt.game_id)
        self._room(lt.room_id).members[lt.player_id] = {"joined_at": self._now()}
        self.audit.record(lt.player_id, "session.open", "session", sess.session_id)
        self._fire("game.session.created", {"session_id": sess.session_id,
                                            "player_id": lt.player_id,
                                            "room_id": lt.room_id})
        return {"session_id": sess.session_id, "player_id": lt.player_id,
                "room_id": lt.room_id, "game_id": lt.game_id}

    # -- rounds --
    def start_round(self, room_id: str, actor: str = "system") -> dict:
        room = self._room(room_id)
        with room.lock:
            if room.round is not None and room.round.status not in (
                    RoundStatus.SETTLED, RoundStatus.CLOSED):
                raise ServiceError(E.E_CONFLICT, "Previous round still active")
            room._round_no += 1
            now = self._now()
            server_seed = secrets.token_hex(32)
            r = WheelRound(
                round_id=f"{room_id}-r{room._round_no}", round_no=room._round_no,
                created_at_ms=now,
                betting_end_at_ms=now + room.config.betting_duration_ms,
                server_seed=server_seed,
                server_seed_hash=hashlib.sha256(server_seed.encode()).hexdigest(),
                nonce=room._round_no, config_version=room.config.version)
            transition(r.status, RoundStatus.BETTING_OPEN)
            r.status = RoundStatus.BETTING_OPEN
            r.emit("round.started", {"round_id": r.round_id,
                                     "betting_end_at": r.betting_end_at_ms}, now)
            room.round = r
        self.audit.record(actor, "round.start", "round", r.round_id)
        self._fire("round.started", {"round_id": r.round_id, "room_id": room_id,
                                     "betting_end_at": r.betting_end_at_ms})
        self._apply_autobets(room_id)  # server-side Auto Bet where approved
        return {"round_id": r.round_id, "round_no": r.round_no,
                "status": r.status.value, "betting_end_at": r.betting_end_at_ms}

    def ensure_round(self, room_id: str) -> dict:
        """Start a round when the table has seated players and none is active.

        Keeps a wheel playable without an external operator ticker; it never
        changes an in-flight round and never influences the drawn result.
        """
        room = self._room(room_id)
        current = room.round
        if current is not None and current.status not in (RoundStatus.SETTLED,
                                                          RoundStatus.CLOSED):
            return {"round_id": current.round_id, "status": current.status.value,
                    "started": False}
        if not room.members:
            return {"round_id": "", "status": "WAITING", "started": False}
        started = self.start_round(room_id)
        return {**started, "started": True}

    def close_betting(self, room_id: str) -> dict:
        room = self._room(room_id)
        with room.lock:
            r = room.round
            if r is None:
                raise ServiceError(E.E_CONFLICT, "No active round")
            if r.status == RoundStatus.BETTING_OPEN:
                transition(r.status, RoundStatus.BETTING_CLOSED)
                r.status = RoundStatus.BETTING_CLOSED
                r.emit("betting.closed", {"round_id": r.round_id}, self._now())
        self._fire("betting.closed", {"round_id": r.round_id})
        return {"round_id": r.round_id, "status": r.status.value}

    # -- bets --
    def _validate(self, room: WheelRoom, player_id: str, option_id: str,
                  amount: int, now: int) -> dict:
        cfg = room.config
        r = room.round
        if r is None or not (r.status == RoundStatus.BETTING_OPEN
                             and now < r.betting_end_at_ms):
            return {"ok": False, "code": "BETTING_CLOSED", "message": "Betting window closed"}
        if isinstance(amount, bool) or not isinstance(amount, int):
            return {"ok": False, "code": "VALIDATION_ERROR",
                    "message": "Amount must be an integer number of coins"}
        if amount <= 0:
            return {"ok": False, "code": "VALIDATION_ERROR",
                    "message": "Amount must be positive"}
        opts = {o.option_id for o in self._options(room)}
        if not opts:
            return {"ok": False, "code": "VALIDATION_ERROR",
                    "message": "No active betting options configured"}
        if option_id not in opts:
            return {"ok": False, "code": "VALIDATION_ERROR",
                    "message": "Unknown or inactive option"}
        if amount not in cfg.denoms:
            return {"ok": False, "code": "VALIDATION_ERROR",
                    "message": "Amount not a configured denomination"}
        if not (cfg.min_bet <= amount <= cfg.max_bet):
            return {"ok": False, "code": "VALIDATION_ERROR",
                    "message": "Amount outside min/max"}
        n = sum(1 for b in r.bets if b.player_id == player_id and b.status == "accepted")
        if n >= cfg.max_bets_per_player_per_round:
            return {"ok": False, "code": "VALIDATION_ERROR",
                    "message": "Per-round bet limit reached"}
        return {"ok": True}

    def place_bet(self, room_id: str, player_id: str, option_id: str,
                  amount: int, idempotency_key: str, auto: bool = False) -> dict:
        self._require_confirmed()
        if not idempotency_key:
            raise ServiceError(E.E_VALIDATION, "Idempotency-Key required")
        room = self._room(room_id)
        now = self._now()
        v = self._validate(room, player_id, option_id, amount, now)
        if not v["ok"]:
            code = E.E_WINDOW_CLOSED if v["code"] == "BETTING_CLOSED" else E.E_VALIDATION
            self._fire("bet.rejected", {"room_id": room_id, "player_id": player_id,
                                        "reason": v["code"]})
            raise ServiceError(code, v["message"])
        bal = self.wallet.get_balance(player_id)
        if bal.available < amount:
            self._fire("bet.rejected", {"room_id": room_id, "player_id": player_id,
                                        "reason": "INSUFFICIENT_BALANCE"})
            raise ServiceError(E.E_INSUFFICIENT, "Insufficient balance")
        payload_hash = _h({"room": room_id, "player": player_id,
                           "opt": option_id, "amt": amount})
        try:
            replay = self.idempotency.claim(f"bet:{idempotency_key}", payload_hash)
        except IdempotencyConflict:
            raise ServiceError(E.E_DUPLICATE, "Idempotency key reused with different payload")
        if replay is not None:
            return replay
        try:
            self.wallet.debit(player_id, amount, ref=f"bet:{room_id}:{idempotency_key}",
                              idempotency_key=f"bet:{idempotency_key}")
        except InsufficientBalance:
            raise ServiceError(E.E_INSUFFICIENT, "Insufficient balance")
        except WalletError as exc:
            raise ServiceError(E.E_INTERNAL, f"Wallet debit failed: {exc}")
        with room.lock:
            r = room.round
            for b in r.bets:  # idempotent replay under lock
                if b.idempotency_key == idempotency_key:
                    if (b.player_id, b.option_id, b.amount) != (player_id, option_id, amount):
                        raise ServiceError(E.E_CONFLICT, "DUPLICATE_REQUEST")
                    result = {"bet_id": b.bet_id, "round_id": b.round_id,
                              "option_id": option_id, "amount": amount}
                    self.idempotency.complete(f"bet:{idempotency_key}", result)
                    return result
            decision_time = self._now()
            if not (r.status == RoundStatus.BETTING_OPEN
                    and decision_time < r.betting_end_at_ms):
                self.wallet.credit(player_id, amount,
                                   ref=f"bet-void:{room_id}:{idempotency_key}",
                                   idempotency_key=f"bet-void:{idempotency_key}")
                self._fire("bet.rejected", {"room_id": room_id, "player_id": player_id,
                                            "reason": "BETTING_CLOSED_RACE_REFUNDED"})
                raise ServiceError(E.E_WINDOW_CLOSED, "Betting closed (stake refunded)")
            room._bet_seq += 1
            bet = WheelBet(f"{r.round_id}-b{room._bet_seq}", r.round_id, player_id,
                           option_id, amount, decision_time, idempotency_key,
                           auto=auto)
            r.bets.append(bet)
            r.emit("bet.accepted", {"bet_id": bet.bet_id, "player": player_id,
                                    "option": option_id, "amount": amount}, decision_time)
            room.bet_history.append({"bet_id": bet.bet_id, "round_id": bet.round_id,
                                     "player_id": player_id, "option_id": option_id,
                                     "amount": amount, "auto": auto,
                                     "decision_time": decision_time})
        result = {"bet_id": bet.bet_id, "round_id": bet.round_id,
                  "option_id": option_id, "amount": amount,
                  "decision_time": bet.decision_time_ms}
        self.idempotency.complete(f"bet:{idempotency_key}", result)
        self.audit.record(player_id, "bet.place", "bet", bet.bet_id, after=result)
        self._fire("bet.accepted", {"bet_id": bet.bet_id, "room_id": room_id, **result})
        return result

    # -- result + settle --
    def publish_result(self, room_id: str) -> dict:
        room = self._room(room_id)
        with room.lock:
            r = room.round
            if r is None:
                raise ServiceError(E.E_CONFLICT, "No active round")
            if r.status != RoundStatus.BETTING_CLOSED:
                raise ServiceError(E.E_CONFLICT, f"Result requires BETTING_CLOSED, have {r.status}")
            transition(r.status, RoundStatus.RESULT_PROCESSING)
            r.status = RoundStatus.RESULT_PROCESSING
            now = self._now()
            r.emit("result.processing", {"round_id": r.round_id}, now)
            opts = [{"id": o.option_id, "name": o.name, "weight": o.weight,
                     "multiplier": o.multiplier} for o in self._options(room)]
            if not opts:
                # No active options: wheel_outcome would raise a bare ValueError
                # from an empty weighted choice. Refuse cleanly and leave the
                # round in RESULT_PROCESSING so an operator can investigate.
                raise ServiceError(E.E_CONFLICT,
                                   "No active betting options: cannot determine a result")
            w = wheel_outcome(opts, r.server_seed, r.client_seed, r.nonce)
            r.winner = w
            transition(r.status, RoundStatus.RESULT)
            r.status = RoundStatus.RESULT
            r.emit("result.published", {"round_id": r.round_id, **w,
                                        "server_seed": r.server_seed}, now)
        self.audit.record("system", "result.publish", "round", r.round_id, after=w)
        self._fire("result.published", {"round_id": r.round_id, **w})
        return {"round_id": r.round_id, **w, "server_seed": r.server_seed,
                "server_seed_hash": r.server_seed_hash}

    def settle(self, room_id: str) -> dict:
        self._require_confirmed()
        room = self._room(room_id)
        with room.lock:
            r = room.round
            if r is None:
                raise ServiceError(E.E_CONFLICT, "No active round")
            if r._settled:
                return {"round_id": r.round_id, "settlements": r.settlements}
            if r.status != RoundStatus.RESULT:
                raise ServiceError(E.E_CONFLICT, f"Settle requires RESULT, have {r.status}")
            win_id = r.winner["winning_option_id"]
            mult = float(r.winner["payout_multiplier"])
            if r.settlements and not r._settled:
                # Resume an interrupted settlement. Bet statuses were already
                # finalised on the first attempt, so recomputing rows from
                # r.bets would produce nothing and silently pay nobody.
                rows = r.settlements
            else:
                rows = []
                for b in r.bets:
                    if b.status != "accepted":
                        continue
                    if b.option_id == win_id:
                        payout = int(b.amount * mult)  # TBC W-PAYOUT-RULE (current rule)
                        b.status = "won"
                    else:
                        payout = 0
                        b.status = "lost"
                    rows.append({"settlement_id": f"stl-{b.bet_id}", "bet_id": b.bet_id,
                                 "player_id": b.player_id, "option_id": b.option_id,
                                 "stake": b.amount, "payout": payout,
                                 "config_version": r.config_version})
                r.settlements = rows
            # NOTE: _settled and the status transitions happen only AFTER the
            # wallet credits succeed (see below). Marking the round settled
            # first would make a credit failure unrecoverable: a retry returns
            # the cached rows and the player is never paid.
        credited = []
        with self._settle_lock:
            for row in rows:
                if row["bet_id"] in self.settled_bet_ids:
                    continue
                if row["payout"] > 0:
                    self.wallet.credit(row["player_id"], row["payout"],
                                       ref=f"settle:{row['bet_id']}",
                                       idempotency_key=f"settle:{row['bet_id']}")
                self.settled_bet_ids.add(row["bet_id"])
                self._add_earning(row["player_id"], row["payout"] - row["stake"])
                self.audit.record("system", "settlement.credit", "settlement",
                                  row["settlement_id"], after=row)
                credited.append(row)
        with room.lock:
            if not r._settled:
                r._settled = True
                # History must reflect settlement: patch placement snapshots with
                # final status/payout (player history reads bet_history, not bets).
                by_id = {row["bet_id"]: row for row in rows}
                for h in room.bet_history:
                    if h["round_id"] == r.round_id and h["bet_id"] in by_id:
                        row = by_id[h["bet_id"]]
                        h["status"] = "won" if row["payout"] > 0 else "lost"
                        h["payout"] = row["payout"]
                transition(r.status, RoundStatus.SETTLED)
                r.status = RoundStatus.SETTLED
                r.emit("settlement.completed", {"round_id": r.round_id,
                                                "settlements": len(rows)}, self._now())
                transition(r.status, RoundStatus.CLOSED)
                r.status = RoundStatus.CLOSED
                r.emit("session.completed", {"round_id": r.round_id}, self._now())
        room.recent.insert(0, {"round_id": r.round_id,
                               "winning_option_id": win_id,
                               "winning_label": r.winner["winning_label"],
                               "multiplier": mult, "angle": r.winner["angle"]})
        room.recent[:] = room.recent[:20]
        self._fire("settlement.completed", {"round_id": r.round_id,
                                            "settlements": len(rows)})
        return {"round_id": r.round_id, "settlements": rows,
                "winner": r.winner}

    def _add_earning(self, player_id: str, net: int):
        import datetime
        day = datetime.date.today().isoformat()
        ent = self._room_earnings(player_id)
        if ent.get("day") != day:
            ent.update({"day": day, "net": 0})
        ent["net"] += net

    def _room_earnings(self, player_id: str) -> dict:
        # earnings live per-room-service; keyed by player
        if not hasattr(self, "_earnings"):
            self._earnings = {}
        return self._earnings.setdefault(player_id, {"day": "", "net": 0})

    def today_earnings(self, player_id: str) -> dict:
        import datetime
        ent = self._room_earnings(player_id)
        if ent.get("day") != datetime.date.today().isoformat():
            return {"player_id": player_id, "day": datetime.date.today().isoformat(),
                    "net": 0}
        return {"player_id": player_id, **ent}

    def sweep(self, now_ms: int = 0) -> List[dict]:
        now = now_ms or self._now()
        reports = []
        for room_id, room in list(self.rooms.items()):
            r = room.round
            if r is None or r.status != RoundStatus.BETTING_OPEN:
                continue
            if now < r.betting_end_at_ms:
                continue
            rep = {"room_id": room_id, "round_id": r.round_id, "actions": []}
            try:
                self.close_betting(room_id)
                rep["actions"].append("closed")
                self.publish_result(room_id)
                rep["actions"].append("result")
                self.settle(room_id)
                rep["actions"].append("settled")
            except (LifecycleError, ServiceError, WalletError) as exc:
                rep["error"] = f"{type(exc).__name__}: {exc}"
                self.audit.record("system", "sweep.failed", "round", r.round_id,
                                  after={"error": str(exc)})
            reports.append(rep)
        return reports

    def cancel_round(self, room_id: str, reason: str, actor: str) -> dict:
        # Cancellation refunds every accepted stake, so it is a money-moving
        # path and must be blocked while business rules are unconfirmed, the
        # same as place_bet and settle.
        self._require_confirmed()
        room = self._room(room_id)
        with room.lock:
            r = room.round
            if r is None:
                raise ServiceError(E.E_CONFLICT, "No active round")
            if r.status in (RoundStatus.RESULT, RoundStatus.SETTLED, RoundStatus.CLOSED):
                raise ServiceError(E.E_CONFLICT, "Too late to cancel")
            voided = [b for b in r.bets if b.status == "accepted"]
            for b in voided:
                b.status = "voided"
            transition(r.status, RoundStatus.CLOSED)
            r.status = RoundStatus.CLOSED
            r.emit("round.cancelled", {"round_id": r.round_id, "reason": reason,
                                       "voided": len(voided)}, self._now())
        for b in voided:
            self.wallet.credit(b.player_id, b.amount, ref=f"cancel:{b.bet_id}",
                               idempotency_key=f"cancel:{b.bet_id}")
        self.audit.record(actor, "round.cancel", "round", r.round_id,
                          after={"reason": reason, "voided": len(voided)})
        self._fire("round.cancelled", {"round_id": r.round_id, "reason": reason})
        return {"voided": len(voided)}

    # -- autobet / autoplay (only where approved) --
    def set_autobet(self, room_id: str, player_id: str, option_id: str,
                    amount: int, rounds: int) -> dict:
        # Auto Bet spends the player's balance on every future round, so it
        # needs the same confirmed-rules gate as an explicit bet. Arming an
        # auto bet while rules are unconfirmed was a real-money bypass.
        self._require_confirmed()
        room = self._room(room_id)
        if not room.config.auto_allowed:
            raise ServiceError(E.E_FORBIDDEN, "Auto Bet not approved for this game")
        opts = {o.option_id for o in self._options(room)}
        if option_id not in opts:
            raise ServiceError(E.E_VALIDATION, "Unknown or inactive option")
        if amount not in room.config.denoms or not (room.config.min_bet <= amount <= room.config.max_bet):
            raise ServiceError(E.E_VALIDATION, "Amount outside approved denominations/limits")
        if rounds < 1 or rounds > 100:
            raise ServiceError(E.E_VALIDATION, "rounds must be 1..100")
        room.autobets[player_id] = {"option_id": option_id, "amount": amount,
                                    "rounds_left": rounds}
        self.audit.record(player_id, "autobet.set", "autobet", f"{room_id}:{player_id}",
                          after=room.autobets[player_id])
        return {"player_id": player_id, **room.autobets[player_id]}

    def _apply_autobets(self, room_id: str):
        room = self._room(room_id)
        r = room.round
        for player_id, cfg in list(room.autobets.items()):
            if cfg["rounds_left"] <= 0:
                continue
            try:
                self.place_bet(room_id, player_id, cfg["option_id"], cfg["amount"],
                               f"auto:{player_id}:{r.round_id}", auto=True)
                cfg["rounds_left"] -= 1
            except ServiceError as exc:
                # Skip this round but keep the config. Record why: a silently
                # skipped auto bet is invisible to both player and operator.
                self.audit.record("system", "autobet.skipped", "autobet",
                                  f"{room_id}:{player_id}:{r.round_id}",
                                  after={"reason": exc.code, "message": str(exc),
                                         "option_id": cfg["option_id"],
                                         "amount": cfg["amount"],
                                         "rounds_left": cfg["rounds_left"]})

    # -- views --
    def state(self, room_id: str, player_id: str) -> dict:
        room = self._room(room_id)
        r = room.round
        now = self._now()
        options = [{"option_id": o.option_id, "name": o.name,
                    "multiplier": o.multiplier, "icon": o.icon,
                    "color_hex": o.color_hex, "hot": o.hot} for o in self._options(room)]
        if r is None:
            return {"game_id": self.game_id, "room_id": room_id, "round": None,
                    "options": options, "serverTime": now}
        totals: Dict[str, int] = {}
        mine: Dict[str, int] = {}
        my_total = 0
        for b in r.bets:
            if b.status in ("accepted", "won"):
                totals[b.option_id] = totals.get(b.option_id, 0) + b.amount
                if b.player_id == player_id:
                    mine[b.option_id] = mine.get(b.option_id, 0) + b.amount
                    my_total += b.amount
        reveal = r.status in (RoundStatus.RESULT, RoundStatus.SETTLED, RoundStatus.CLOSED)
        return {"game_id": self.game_id, "room_id": room_id,
                "round_id": r.round_id, "round_no": r.round_no,
                "status": r.status.value, "serverTime": now,
                "betting_end_at": r.betting_end_at_ms,
                "denoms": list(room.config.denoms),
                "options": options, "totals": totals,
                "total_bet": sum(totals.values()),
                "my_totals": mine, "my_total_bet": my_total,
                "winner": r.winner if reveal else None,
                "recent": room.recent[:10],
                "config_version": r.config_version}

    def history(self, room_id: str, player_id: str, limit: int = 50) -> dict:
        room = self._room(room_id)
        mine = [b for b in room.bet_history if b["player_id"] == player_id][-limit:]
        return {"bets": mine, "earnings_today": self.today_earnings(player_id)}

    def recent_results(self, room_id: str, limit: int = 20) -> dict:
        return {"results": self._room(room_id).recent[:limit]}

    def reconnect(self, session_id: str, last_seen_seq: int) -> dict:
        sess = self.sessions.get(session_id)
        if sess is None:
            raise ServiceError(E.E_AUTH, "Unknown session")
        self.sessions.touch(session_id)
        room = self._room(sess.room_id)
        r = room.round
        missed = []
        if r is not None:
            missed = [e for e in r.events if e["seq"] > last_seen_seq][-room.config.event_log_cap:]
        return {"snapshot": self.state(sess.room_id, sess.player_id),
                "missed_events": missed, "session_id": session_id}
