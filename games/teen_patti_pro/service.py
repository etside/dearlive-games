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
from common.idempotency import (IdempotencyStore, MemoryIdempotencyStore,
                                IdempotencyConflict, ClaimResult)
from common.lifecycle import (RoundStatus, SETTLE_MAX_ATTEMPTS, needs_settlement_retry)
from common.session import TokenStore, MemoryTokenStore, SessionStore, MemorySessionStore, TokenError
from common.wallet import WalletAdapter, MemoryWallet, InsufficientBalance, WalletError
from common.webhooks import build_event, MemoryDeliveryLog
from .config import TeenPattiConfig, DEFAULT_CONFIG
from .engine import Room, LifecycleError


def _h(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class ServiceError(Exception):
    def __init__(self, code: str, message: str, retry_after: int = 0):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


class TeenPattiService:
    GAME_ID = "teen-patti-pro"

    @property
    def game_id(self) -> str:
        return self.GAME_ID

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
        self.round_history: Dict[str, List[dict]] = {}
        self.event_log: List[dict] = []
        self.settlement_alerts: List[dict] = []  # never dropped: operator must see stranded pots
        self.HISTORY_CAP = 200
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
        self.event_log.append(ev)
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
        room = self._room(room_id)
        room.config = self.config
        r = room.start_round(self._now())
        self.audit.record(actor, "round.start", "round", r.round_id)
        self._fire("round.started", {"round_id": r.round_id, "room_id": room_id,
                                     "betting_end_at": r.betting_end_at_ms})
        self._skill("on_round_start", {"room_id": room_id, "round_id": r.round_id})
        return {"round_id": r.round_id, "status": r.status.value,
                "betting_end_at": r.betting_end_at_ms}

    def ensure_round(self, room_id: str) -> dict:
        """Start a round when the table has seated players and none is active.

        Keeps a table playable without an external operator ticker; it never
        changes an in-flight round and never fabricates cards or outcomes.
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
        r = self._room(room_id).close_betting(self._now())
        self._fire("betting.closed", {"round_id": r.round_id, "room_id": room_id})
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
        # 8: idempotency claim BEFORE money moves. claim() returns an explicit
        # state so a fresh claim can never be confused with an in-flight
        # duplicate (the old contract returned None for both).
        payload_hash = _h({"room": room_id, "player": player_id, "pos": position, "amt": amount})
        key = f"bet:{idempotency_key}"
        try:
            outcome = self.idempotency.claim(key, payload_hash)
        except IdempotencyConflict:
            raise ServiceError(E.E_DUPLICATE, "Idempotency key reused with different payload")
        if outcome.state is ClaimResult.REPLAY:
            return outcome.result  # exact replay, no second debit
        if outcome.state in (ClaimResult.IN_FLIGHT, ClaimResult.NOT_SEEN):
            # Another worker holds this key. Do NOT execute: proceeding here is
            # what allowed a duplicate debit. Client should retry.
            raise ServiceError(E.E_CONFLICT,
                               "A request with this Idempotency-Key is in flight",
                               retry_after=1)
        # 9: atomic debit (adapter idempotent on key too)
        try:
            self.wallet.debit(player_id, amount, ref=f"bet:{room_id}:{idempotency_key}",
                              idempotency_key=key)
        except InsufficientBalance:
            # No money moved: release so the client can retry this key.
            self.idempotency.release(key, payload_hash)
            raise ServiceError(E.E_INSUFFICIENT, "Insufficient balance")
        except WalletError as exc:
            self.idempotency.release(key, payload_hash)
            raise ServiceError(E.E_INTERNAL, f"Wallet debit failed: {exc}")
        except Exception as exc:
            # An adapter that leaks a non-WalletError (transport error, timeout)
            # must still release, or the player is locked out of this key for
            # the full TTL even though no money moved.
            self.idempotency.release(key, payload_hash)
            raise ServiceError(E.E_INTERNAL,
                               f"Wallet debit failed: {type(exc).__name__}: {exc}")
        # 10: create bet under room lock (re-checks window; race-fixed).
        # ANY failure here happens AFTER debit -> always compensate (append-only
        # credit); a lost debit is worse than a redundant refund row. The bet
        # was NOT created, so the key is safe to release and let them retry.
        try:
            bet = room.place_bet(player_id, position, amount, idempotency_key, self._now())
        except LifecycleError as exc:
            msg = str(exc)
            self.wallet.credit(player_id, amount,
                               ref=f"bet-void:{room_id}:{idempotency_key}",
                               idempotency_key=f"bet-void:{idempotency_key}")
            self.idempotency.release(key, payload_hash)
            if "BETTING_CLOSED" in msg:
                self._fire("bet.rejected", {"room_id": room_id, "player_id": player_id,
                                            "reason": "BETTING_CLOSED_RACE_REFUNDED"})
                raise ServiceError(E.E_WINDOW_CLOSED, "Betting closed (stake refunded)")
            self._fire("bet.rejected", {"room_id": room_id, "player_id": player_id,
                                        "reason": "POST_DEBIT_CONFLICT_REFUNDED"})
            raise ServiceError(E.E_CONFLICT, msg + " (stake refunded)")
        result = {"bet_id": bet.bet_id, "round_id": bet.round_id, "position": position,
                  "amount": amount, "decision_time": bet.decision_time_ms}
        # Past this point the bet EXISTS and money moved: do NOT release the
        # key, or a retry could place a second bet against the same stake.
        self.idempotency.complete(key, result)
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
        self._fire("result.published", {"round_id": r.round_id, "room_id": room_id,
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
        try:
            with self._settle_lock:  # atomic check-add-credit per bet
                for row in rows:
                    if row["bet_id"] in self.settled_bet_ids:
                        continue  # UNIQUE settlement.bet_id: never pay twice
                    if row["payout"] > 0:
                        self.wallet.credit(row["player_id"], row["payout"],
                                           ref=f"settle:{row['bet_id']}",
                                           idempotency_key=f"settle:{row['bet_id']}")
                    self.settled_bet_ids.add(row["bet_id"])
                    credited.append(row)
                    self.audit.record("system", "settlement.credit", "settlement",
                                      row["settlement_id"], after=row)
        except (WalletError, ServiceError) as exc:
            # engine.settle() has already marked the round CLOSED and set
            # _settled, so a credit failure here leaves a TERMINAL round with a
            # PARTIAL payout. Park it so the retry sweep can finish paying.
            # Re-raised so the caller still sees the failure.
            self._park_settlement(room_id, room, exc, self._now())
            self.audit.record("system", "settlement.credit_failed", "round",
                              room.round.round_id,
                              after={"error": str(exc), "paid": len(credited),
                                     "owed": len(rows) - len(credited),
                                     **room.settlement_health(self._now())})
            raise
        self._fire("settlement.completed", {"round_id": room.round.round_id,
                                            "room_id": room_id,
                                            "settlements": len(rows)})
        self._skill("on_settle", {"room_id": room_id, "round_id": room.round.round_id})
        self._record_history(room)
        return {"round_id": room.round.round_id, "settlements": rows,
                "carry_out": room.round.carry_out}

    def _record_history(self, room) -> None:
        """Append one immutable row per settled round (replay-safe)."""
        r = room.round
        log = self.round_history.setdefault(room.room_id, [])
        if any(row["round_id"] == r.round_id for row in log):
            return
        log.append({
            "round_id": r.round_id,
            "round_no": r.round_no,
            "config_version": r.config_version,
            "winners": list(r.winner_positions),
            "pot": sum(b.amount for b in r.bets if b.status in ("accepted", "won", "lost")),
            "settlements": [dict(row) for row in r.settlements],
            "carry_out": r.carry_out,
            "settled_at": int(time.time() * 1000),
        })
        if len(log) > self.HISTORY_CAP:
            del log[:len(log) - self.HISTORY_CAP]

    def history(self, room_id: str, limit: int = 50) -> List[dict]:
        return list(reversed(self.round_history.get(room_id, [])))[:max(1, int(limit))]

    def leave_table(self, room_id: str, player_id: str) -> dict:
        result = self._room(room_id).leave_session(player_id)
        self.audit.record(player_id, "player.leave", "room", room_id, after=result)
        self._fire("player.left", {"room_id": room_id, "player_id": player_id})
        return result

    def sweep(self, now_ms: int = 0) -> List[dict]:
        """Timer-expiry driver (call every second from scheduler/operator loop).

        Two passes, in order:
          1. Re-drive rounds parked in SETTLED_PENDING (settlement retry).
          2. Drive BETTING_OPEN rounds past their window: close -> result ->
             settle, all idempotent and audited.

        A round that fails settlement is NEVER abandoned: it is parked in
        SETTLED_PENDING and re-driven here with exponential backoff until it
        settles or exhausts SETTLE_MAX_ATTEMPTS (-> SETTLE_FAILED + alert).
        Never fails the sweep on one room's error (records and continues).
        """
        now = now_ms or self._now()
        reports = []
        for room_id, room in list(self.rooms.items()):
            r = room.round
            if r is None:
                continue
            if r.status == RoundStatus.SETTLED_PENDING:
                rep = {"room_id": room_id, "round_id": r.round_id, "actions": []}
                try:
                    if room.settle_attempts_exhausted():
                        room.mark_settle_failed(now)
                        self._alert_settle_failed(room_id, room, now)
                        rep["actions"].append("settle_failed")
                    elif room.settle_retry_due(now):
                        self._retry_settlement(room_id, room, now)
                        rep["actions"].append("settlement_retried")
                    else:
                        rep["actions"].append("settlement_backoff")
                except (LifecycleError, ServiceError, WalletError) as exc:
                    rep["error"] = f"{type(exc).__name__}: {exc}"
                    self.audit.record("system", "settlement.retry_failed", "round",
                                      r.round_id, after={"error": str(exc)})
                reports.append(rep)
                continue
            if r.status != RoundStatus.BETTING_OPEN:
                continue
            if now < r.betting_end_at_ms:
                continue
            rep = {"room_id": room_id, "round_id": r.round_id, "actions": []}
            try:
                room.close_betting(now)
                rep["actions"].append("closed")
                self._fire("betting.closed", {"round_id": r.round_id, "room_id": room_id})
                self.publish_result(room_id)  # audited + webhooked result path
                rep["actions"].append("result")
                self.settle(room_id, r.round_id)
                rep["actions"].append("settled")
            except (LifecycleError, ServiceError, WalletError) as exc:
                # Park for retry. Previously this only recorded the error and
                # left the round outside BETTING_OPEN, so no future sweep could
                # ever reach it again and the pot was stranded permanently.
                rep["error"] = f"{type(exc).__name__}: {exc}"
                self._park_settlement(room_id, room, exc, now)
                self.audit.record("system", "sweep.failed", "round", r.round_id,
                                  after={"error": str(exc),
                                         "parked": room.settlement_health(now)})
            reports.append(rep)
        return reports

    def _park_settlement(self, room_id: str, room, exc: Exception, now: int) -> None:
        """Move a round into SETTLED_PENDING, preserving the resume point."""
        try:
            room.mark_settle_pending(f"{type(exc).__name__}: {exc}", now)
        except LifecycleError:
            # Already parked or no round; nothing further to do here. The audit
            # row written by the caller still records the failure.
            return
        self._fire("settlement.pending", {
            "round_id": room.round.round_id, "room_id": room_id,
            "error": str(exc), **room.settlement_health(now)})

    def _retry_settlement(self, room_id: str, room, now: int) -> None:
        """Re-drive one parked round from its recorded resume point.

        Safe to run repeatedly: calculate_result() is only re-run when the
        resume point is BETTING_CLOSED, room.settle() is _settled-guarded, and
        the credit loop skips bet_ids already in settled_bet_ids. A round that
        failed during the credit loop resumes at CLOSED, so this reduces to
        re-entering the (idempotent) credit loop.
        """
        resumed = room.restore_resume_status()
        if resumed == RoundStatus.BETTING_CLOSED:
            # publish_result() drives calculate_result() itself; calling both
            # would raise (the second call needs BETTING_CLOSED, not RESULT).
            self.publish_result(room_id)
        self.settle(room_id, room.round.round_id)
        self._record_history(room)

    def _unpaid_winnings(self, room) -> int:
        """Money owed to players but not yet credited for this round.

        This is the figure an operator must act on: the sum of settlement
        payouts whose bet_id has not been through the exactly-once credit loop.
        """
        r = room.round
        if r is None or not r.settlements:
            return 0
        return sum(row["payout"] for row in r.settlements
                   if row.get("payout", 0) > 0
                   and row["bet_id"] not in self.settled_bet_ids)

    def _alert_settle_failed(self, room_id: str, room, now: int) -> None:
        """Operator alert for a settlement that exhausted its retry budget."""
        health = room.settlement_health(now)
        owed = self._unpaid_winnings(room)
        self.audit.record("system", "settlement.failed", "round", room.round.round_id,
                          after={**health, "unpaid_winnings": owed})
        self._fire("settlement.failed", {
            "round_id": room.round.round_id, "room_id": room_id,
            "severity": "critical", "unpaid_winnings": owed, **health})
        self.settlement_alerts.append({
            "round_id": room.round.round_id, "room_id": room_id,
            "attempts": health["settle_attempts"], "error": health["settle_error"],
            "unpaid_winnings": owed,
            "pending_age_ms": health["pending_age_ms"], "at": now})

    def settlement_health_report(self, now_ms: int = 0) -> dict:
        """Operator dashboard counters. Pending/failed rounds are never hidden."""
        now = now_ms or self._now()
        pending, failed = [], []
        for room_id, room in self.rooms.items():
            r = room.round
            if r is None:
                continue
            if r.status == RoundStatus.SETTLED_PENDING:
                pending.append((room_id, {**room.settlement_health(now),
                                          "unpaid_winnings": self._unpaid_winnings(room)}))
            elif r.status == RoundStatus.SETTLE_FAILED:
                failed.append((room_id, {**room.settlement_health(now),
                                         "unpaid_winnings": self._unpaid_winnings(room)}))
        oldest = max([h["pending_age_ms"] for _rid, h in pending], default=0)
        return {
            "settled_pending": len(pending),
            "settle_failed": len(failed),
            "oldest_pending_age_ms": oldest,
            "unpaid_winnings_total": sum(h["unpaid_winnings"] for _rid, h in pending + failed),
            "settle_max_attempts": SETTLE_MAX_ATTEMPTS,
            "pending": [{"room_id": rid, **h} for rid, h in pending],
            "failed": [{"room_id": rid, **h} for rid, h in failed],
        }

    def cancel_round(self, room_id: str, reason: str, actor: str) -> dict:
        room = self._room(room_id)
        voided = room.cancel(reason, self._now())
        for b in voided:
            self.wallet.credit(b.player_id, b.amount, ref=f"cancel:{b.bet_id}",
                               idempotency_key=f"cancel:{b.bet_id}")
        self.audit.record(actor, "round.cancel", "round", room.round.round_id,
                          after={"reason": reason, "voided": len(voided)})
        self._fire("round.cancelled", {"round_id": room.round.round_id,
                                       "room_id": room_id, "reason": reason})
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
