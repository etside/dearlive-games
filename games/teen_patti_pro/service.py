"""TeenPattiService — authoritative service layer.

Wires: Room engine + WalletAdapter + IdempotencyStore + TokenStore +
SessionStore + AuditLog + webhooks. Enforces:
  - TBC gate (real-money blocked unless config.confirmed)
  - bet order: auth -> game -> round -> action -> amount -> min/max ->
    balance -> window -> idempotency -> atomic debit -> create bet -> publish
  - settlement exactly-once (UNIQUE settlement.bet_id set + idempotent credit)
  - JEV is never consulted here (advisory only, offline reviews).
"""
import hashlib
import json
import threading
import time
from typing import Any, Dict, List

from common import envelope as E
from common.audit import AuditLog
from common.idempotency import IdempotencyStore, MemoryIdempotencyStore, IdempotencyConflict
from common.lifecycle import RoundStatus
from common.session import TokenStore, MemoryTokenStore, SessionStore, MemorySessionStore, TokenError
from common.wallet import WalletAdapter, MemoryWallet, InsufficientBalance, WalletError
from common.webhooks import build_event, MemoryDeliveryLog
from .config import TeenPattiConfig, DEFAULT_CONFIG
from .engine import Room, LifecycleError


def _h(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class ServiceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class TeenPattiService:
    GAME_ID = "teen-patti-pro"

    def __init__(self, config: TeenPattiConfig = DEFAULT_CONFIG,
                 wallet: WalletAdapter = None,
                 idempotency: IdempotencyStore = None,
                 tokens: TokenStore = None,
                 sessions: SessionStore = None,
                 webhook_destinations: List[str] = None,
                 webhook_secret: str = "dev-secret",
                 skills=None):  # common.skills.SkillBus (optional; never blocks money)
        self.config = config
        self.wallet = wallet or MemoryWallet()
        self.idempotency = idempotency or MemoryIdempotencyStore()
        self.tokens = tokens or MemoryTokenStore()
        self.sessions = sessions or MemorySessionStore()
        self.audit = AuditLog()
        self.webhooks = MemoryDeliveryLog()
        self.webhook_destinations = webhook_destinations or []
        self.webhook_secret = webhook_secret
        self.rooms: Dict[str, Room] = {}
        self.settled_bet_ids = set()  # UNIQUE settlement.bet_id guard
        self._settle_lock = threading.Lock()  # check-add-credit must be atomic
        self.skills = skills
        self._now = lambda: int(time.time() * 1000)

    def _skill(self, hook: str, payload: dict):
        """Post-commit observer fan-out. Exceptions isolated inside the bus;
        skills can never alter money outcomes (they run after commit)."""
        if self.skills is not None:
            try:
                self.skills.emit(self.GAME_ID, hook, payload)
            except Exception:
                pass  # bus already audits; service must not fail on observers

    # ---- internal ----
    def _room(self, room_id: str) -> Room:
        if room_id not in self.rooms:
            self.rooms[room_id] = Room(room_id, self.config)
        return self.rooms[room_id]

    def _fire(self, kind: str, data: dict):
        ev = build_event(kind, data)
        for dest in self.webhook_destinations:
            self.webhooks.record(dest, ev, "queued")
        return ev

    def _require_confirmed(self):
        if not self.config.confirmed:
            raise ServiceError(E.E_TBC_BLOCKED,
                               f"Config {self.config.version} unconfirmed TBC rules "
                               f"{list(self.config.tbc)} — real-money action blocked")

    # ---- sessions (launch token redeem) ----
    def open_session(self, token: str) -> dict:
        try:
            lt = self.tokens.redeem(token)
        except TokenError as exc:
            raise ServiceError("INVALID_TOKEN", str(exc))
        if lt.game_id != "teen-patti-pro":
            raise ServiceError(E.E_VALIDATION, "Token not issued for teen-patti-pro")
        sess = self.sessions.create(lt.player_id, lt.room_id, lt.game_id)
        self._room(lt.room_id).create_session(lt.player_id)
        self.audit.record(lt.player_id, "session.open", "session", sess.session_id)
        self._fire("game.session.created", {"session_id": sess.session_id,
                                            "player_id": lt.player_id,
                                            "room_id": lt.room_id})
        return {"session_id": sess.session_id, "player_id": lt.player_id,
                "room_id": lt.room_id, "game_id": lt.game_id}

    # ---- rounds ----
    def start_round(self, room_id: str, actor: str = "system") -> dict:
        r = self._room(room_id).start_round(self._now())
        self.audit.record(actor, "round.start", "round", r.round_id)
        self._fire("round.started", {"round_id": r.round_id, "room_id": room_id,
                                     "betting_end_at": r.betting_end_at_ms})
        self._skill("on_round_start", {"room_id": room_id, "round_id": r.round_id})
        return {"round_id": r.round_id, "status": r.status.value,
                "betting_end_at": r.betting_end_at_ms}

    def close_betting(self, room_id: str) -> dict:
        r = self._room(room_id).close_betting(self._now())
        self._fire("betting.closed", {"round_id": r.round_id})
        self._skill("on_close", {"room_id": room_id, "round_id": r.round_id})
        return {"round_id": r.round_id, "status": r.status.value}

    # ---- bets (full order enforced) ----
    def place_bet(self, room_id: str, player_id: str, position: str,
                  amount: int, idempotency_key: str) -> dict:
        self._require_confirmed()
        if not idempotency_key:
            raise ServiceError(E.E_VALIDATION, "Idempotency-Key required")
        room = self._room(room_id)
        now = self._now()
        # 1-6: game/round/action/amount/min-max pre-validation (pure, no side effects)
        v = room.validate_bet(player_id, position, amount, now)
        if not v["ok"]:
            code = E.E_WINDOW_CLOSED if v["code"] == "BETTING_CLOSED" else E.E_VALIDATION
            self._fire("bet.rejected", {"room_id": room_id, "player_id": player_id,
                                        "reason": v["code"]})
            raise ServiceError(code, v["message"])
        # 7: balance check (read-only; debit below is the atomic guard)
        bal = self.wallet.get_balance(player_id)
        if bal.available < amount:
            self._fire("bet.rejected", {"room_id": room_id, "player_id": player_id,
                                        "reason": "INSUFFICIENT_BALANCE"})
            raise ServiceError(E.E_INSUFFICIENT, "Insufficient balance")
        # 8: idempotency claim BEFORE money moves
        payload_hash = _h({"room": room_id, "player": player_id, "pos": position, "amt": amount})
        try:
            replay = self.idempotency.claim(f"bet:{idempotency_key}", payload_hash)
        except IdempotencyConflict:
            raise ServiceError(E.E_DUPLICATE, "Idempotency key reused with different payload")
        if replay is not None:
            return replay  # exact replay, no second debit
        # 9: atomic debit (adapter idempotent on key too)
        try:
            self.wallet.debit(player_id, amount, ref=f"bet:{room_id}:{idempotency_key}",
                              idempotency_key=f"bet:{idempotency_key}")
        except InsufficientBalance:
            raise ServiceError(E.E_INSUFFICIENT, "Insufficient balance")
        except WalletError as exc:
            raise ServiceError(E.E_INTERNAL, f"Wallet debit failed: {exc}")
        # 10: create bet under room lock (re-checks window; race-fixed).
        # ANY failure here happens AFTER debit -> always compensate (append-only
        # credit); a lost debit is worse than a redundant refund row.
        try:
            bet = room.place_bet(player_id, position, amount, idempotency_key, self._now())
        except LifecycleError as exc:
            msg = str(exc)
            self.wallet.credit(player_id, amount,
                               ref=f"bet-void:{room_id}:{idempotency_key}",
                               idempotency_key=f"bet-void:{idempotency_key}")
            if "BETTING_CLOSED" in msg:
                self._fire("bet.rejected", {"room_id": room_id, "player_id": player_id,
                                            "reason": "BETTING_CLOSED_RACE_REFUNDED"})
                raise ServiceError(E.E_WINDOW_CLOSED, "Betting closed (stake refunded)")
            self._fire("bet.rejected", {"room_id": room_id, "player_id": player_id,
                                        "reason": "POST_DEBIT_CONFLICT_REFUNDED"})
            raise ServiceError(E.E_CONFLICT, msg + " (stake refunded)")
        result = {"bet_id": bet.bet_id, "round_id": bet.round_id, "position": position,
                  "amount": amount, "decision_time": bet.decision_time_ms}
        self.idempotency.complete(f"bet:{idempotency_key}", result)
        self.audit.record(player_id, "bet.place", "bet", bet.bet_id, after=result)
        self._fire("bet.accepted", {"bet_id": bet.bet_id, "room_id": room_id, **result})
        self._skill("on_bet", {"room_id": room_id, "bet_id": bet.bet_id})
        return result

    # ---- result + settle ----
    def publish_result(self, room_id: str) -> dict:
        room = self._room(room_id)
        r = room.calculate_result(self._now())
        self.audit.record("system", "result.publish", "round", r.round_id,
                          after={"winners": r.winner_positions})
        self._fire("result.published", {"round_id": r.round_id,
                                        "winners": r.winner_positions})
        self._skill("on_result", {"room_id": room_id, "round_id": r.round_id})
        from .engine import fmt_card
        return {"round_id": r.round_id, "winners": r.winner_positions,
                "hands": {p: [fmt_card(c) for c in h] for p, h in r.resolved.items()},
                "raw_hands": {p: [fmt_card(c) for c in h] for p, h in r.hands.items()},
                "deck_commit": r.deck_commit, "seed": r.seed_hex}

    def settle(self, room_id: str, round_id: str = "") -> dict:
        self._require_confirmed()
        room = self._room(room_id)
        rows = room.settle(self._now())
        credited = []
        with self._settle_lock:  # atomic check-add-credit per bet
            for row in rows:
                if row["bet_id"] in self.settled_bet_ids:
                    continue  # UNIQUE settlement.bet_id: never pay twice
                self.settled_bet_ids.add(row["bet_id"])
                if row["payout"] > 0:
                    self.wallet.credit(row["player_id"], row["payout"],
                                       ref=f"settle:{row['bet_id']}",
                                       idempotency_key=f"settle:{row['bet_id']}")
                credited.append(row)
                self.audit.record("system", "settlement.credit", "settlement",
                                  row["settlement_id"], after=row)
        self._fire("settlement.completed", {"round_id": room.round.round_id,
                                            "settlements": len(rows)})
        self._skill("on_settle", {"room_id": room_id, "round_id": room.round.round_id})
        return {"round_id": room.round.round_id, "settlements": rows,
                "carry_out": room.round.carry_out}

    def sweep(self, now_ms: int = 0) -> List[dict]:
        """Timer-expiry driver (call every second from scheduler/operator loop).
        For each room with an expired BETTING_OPEN window: close -> result ->
        settle, all idempotent and audited. Never fails the sweep on one room's
        error (records and continues). Returns per-room action reports."""
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
                room.close_betting(now)
                rep["actions"].append("closed")
                self._fire("betting.closed", {"round_id": r.round_id})
                room.calculate_result(now)
                rep["actions"].append("result")
                self._fire("result.published", {"round_id": r.round_id,
                                                "winners": r.winner_positions})
                self.settle(room_id, r.round_id)
                rep["actions"].append("settled")
            except (LifecycleError, ServiceError, WalletError) as exc:
                rep["error"] = f"{type(exc).__name__}: {exc}"
                self.audit.record("system", "sweep.failed", "round", r.round_id,
                                  after={"error": str(exc)})
            reports.append(rep)
        return reports

    def cancel_round(self, room_id: str, reason: str, actor: str) -> dict:
        room = self._room(room_id)
        voided = room.cancel(reason, self._now())
        for b in voided:
            self.wallet.credit(b.player_id, b.amount, ref=f"cancel:{b.bet_id}",
                               idempotency_key=f"cancel:{b.bet_id}")
        self.audit.record(actor, "round.cancel", "round", room.round.round_id,
                          after={"reason": reason, "voided": len(voided)})
        self._fire("round.cancelled", {"round_id": room.round.round_id,
                                       "reason": reason})
        self._skill("on_cancel", {"room_id": room_id, "reason": reason,
                                  "voided": len(voided)})
        return {"voided": len(voided)}

    # ---- views ----
    def state(self, room_id: str, player_id: str) -> dict:
        return self._room(room_id).snapshot(player_id, self._now())

    def reconnect(self, session_id: str, last_seen_seq: int) -> dict:
        sess = self.sessions.get(session_id)
        if sess is None:
            raise ServiceError(E.E_AUTH, "Unknown session")
        self.sessions.touch(session_id)
        room = self._room(sess.room_id)
        return {"snapshot": room.snapshot(sess.player_id, self._now()),
                "missed_events": room.events_since(last_seen_seq, sess.player_id),
                "session_id": session_id}
