CODE UNDER REVIEW: Teen Patti Pro deterministic engine (games/teen_patti_pro/engine.py) + service layer (service.py). Python stdlib only. Server-authoritative. Key invariants claimed:
(a) per-round mutex serializes bet-decision vs close; decision_time stamped under lock; invariant decision_time < betting_end_at enforced;
(b) idempotent bet replay by key; key-reuse-with-different-payload raises;
(c) debit-before-create with immediate compensation if window slammed shut post-debit;
(d) settle deterministic + replay-safe (_settled flag) + UNIQUE settlement.bet_id at service + idempotent wallet credit;
(e) tie split: equal per winning POSITION then pro-rata within position, dust carried (no seat bias);
(f) pot conservation: payouts + carry_out == pot;
(g) reconnect snapshot redacts hole cards pre-RESULT; result.published event redacted in replay pre-reveal;
(h) cancel only before RESULT, void + compensating credit.
JEV is ADVISORY ONLY. Flag real bugs/races/security holes. Do not invent DearLive APIs.
--- BEGIN CODE ---
"""Teen Patti Pro deterministic game engine (server-authoritative).

Pure logic: no sockets, no wallet, no DB, no clock reads inside decisions
(time is an injected parameter -> fully testable + replayable).
Thread-safety: each Room owns a threading.Lock serializing bet-decision vs
close (JEV design-review fix for late_bet_race). Idempotencyówki: engine is
deterministic; exactly-once is enforced by the service layer via
UNIQUE settlement.bet_id + idempotency store.

Audit: every round stores seed_hex + deck_commitment (sha256 of the
shuffled deck order) in the result record -> reproducible review.
"""
import hashlib
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from common.lifecycle import RoundStatus, transition, LifecycleError
from .config import TeenPattiConfig, DEFAULT_CONFIG

SUITS = ("S", "H", "D", "C")  # spades hearts diamonds clubs
RANKS = tuple(range(2, 15))  # 2..14 (11=J 12=Q 13=K 14=A)

Card = Tuple[int, str]  # (rank, suit)


def build_deck() -> List[Card]:
    return [(r, s) for s in SUITS for r in RANKS]


def shuffle_deck(seed_hex: str) -> List[Card]:
    """Deterministic Fisher-Yates driven by seed. Same seed -> same deck."""
    deck = build_deck()
    rng = random.Random(int(seed_hex, 16))
    rng.shuffle(deck)
    return deck


def deck_commitment(deck: List[Card]) -> str:
    return hashlib.sha256(repr(deck).encode()).hexdigest()


def _is_sequence(ranks: List[int]) -> Tuple[bool, int]:
    """Returns (is_seq, high). Admits A-K-Q (high 14) and A-2-3 (high 3)."""
    s = sorted(ranks)
    if s == [2, 3, 14]:
        return True, 3
    if s[2] - s[0] == 2 and len(set(s)) == 3:
        return True, s[2]
    return False, 0


def evaluate_hand(hand: List[Card]) -> Tuple[int, Tuple]:
    """-> (category_rank, tiebreak). Higher wins. Standard Teen Patti
    (TBC G3-BR-01 default): trail 6 > pure_seq 5 > seq 4 > color 3 > pair 2 > high 1.
    """
    ranks = sorted(c[0] for c in hand)
    suits = [c[1] for c in hand]
    flush = len(set(suits)) == 1
    seq, seq_high = _is_sequence(ranks)
    if ranks[0] == ranks[2]:
        return 6, (ranks[0],)
    if flush and seq:
        return 5, (seq_high,)
    if seq:
        return 4, (seq_high,)
    if flush:
        return 3, tuple(sorted(ranks, reverse=True))
    if ranks[0] == ranks[1]:
        return 2, (ranks[0], ranks[2])
    if ranks[1] == ranks[2]:
        return 2, (ranks[1], ranks[0])
    return 1, tuple(sorted(ranks, reverse=True))


@dataclass
class Bet:
    bet_id: str
    round_id: str
    player_id: str
    position: str
    amount: int
    decision_time_ms: int
    idempotency_key: str
    status: str = "accepted"  # accepted | won | lost | voided


@dataclass
class Round:
    round_id: str
    round_no: int
    status: RoundStatus = RoundStatus.UPCOMING
    created_at_ms: int = 0
    betting_end_at_ms: int = 0
    seed_hex: str = ""
    deck_commit: str = ""
    hands: Dict[str, List[Card]] = field(default_factory=dict)
    winner_positions: List[str] = field(default_factory=list)
    bets: List[Bet] = field(default_factory=list)
    settlements: List[dict] = field(default_factory=list)
    carry_in: int = 0
    carry_out: int = 0
    config_version: str = ""
    events: List[dict] = field(default_factory=list)
    _seq: int = 0
    _settled: bool = False

    def emit(self, kind: str, data: dict, now_ms: int) -> dict:
        self._seq += 1
        ev = {"seq": self._seq, "kind": kind, "serverTime": now_ms, **data}
        self.events.append(ev)
        return ev


class Room:
    """One Teen Patti table. All mutating paths take self.lock."""

    def __init__(self, room_id: str, config: TeenPattiConfig = DEFAULT_CONFIG):
        self.room_id = room_id
        self.config = config
        self.lock = threading.Lock()
        self.round: Optional[Round] = None
        self._round_no = 0
        self._bet_seq = 0
        self.carry_over = 0
        self.members: Dict[str, dict] = {}

    # ---- sessions ----
    def create_session(self, player_id: str) -> dict:
        self.members[player_id] = {"joined_at": int(time.time() * 1000)}
        return {"room_id": self.room_id, "player_id": player_id}

    # ---- rounds ----
    def start_round(self, now_ms: int, seed_hex: Optional[str] = None) -> Round:
        import secrets
        with self.lock:
            if self.round is not None and self.round.status not in (RoundStatus.SETTLED, RoundStatus.CLOSED):
                raise LifecycleError("Previous round still active")
            self._round_no += 1
            r = Round(round_id=f"{self.room_id}-r{self._round_no}",
                      round_no=self._round_no, created_at_ms=now_ms,
                      betting_end_at_ms=now_ms + self.config.guess_ms,
                      seed_hex=seed_hex or secrets.token_hex(16),
                      carry_in=self.carry_over,
                      config_version=self.config.version)
            deck = shuffle_deck(r.seed_hex)
            r.deck_commit = deck_commitment(deck)
            r.hands = {p: deck[i * 3:(i + 1) * 3] for i, p in enumerate(self.config.seats)}
            transition(r.status, RoundStatus.BETTING_OPEN)
            r.status = RoundStatus.BETTING_OPEN
            r.emit("round.started", {"round_id": r.round_id,
                                     "betting_end_at": r.betting_end_at_ms}, now_ms)
            self.round = r
            self.carry_over = 0
            return r

    def _window_open(self, r: Round, now_ms: int) -> bool:
        return r.status == RoundStatus.BETTING_OPEN and now_ms < r.betting_end_at_ms

    def validate_bet(self, player_id: str, position: str, amount: int, now_ms: int) -> dict:
        """Pure validation (no lock, no side effects)."""
        cfg = self.config
        r = self.round
        if r is None or not self._window_open(r, now_ms):
            return {"ok": False, "code": "BETTING_CLOSED", "message": "Betting window closed"}
        if position not in cfg.seats:
            return {"ok": False, "code": "VALIDATION_ERROR", "message": "Unknown position"}
        if amount not in cfg.denoms:
            return {"ok": False, "code": "VALIDATION_ERROR", "message": "Amount not a configured denomination"}
        if not (cfg.min_bet <= amount <= cfg.max_bet):
            return {"ok": False, "code": "VALIDATION_ERROR", "message": "Amount outside min/max"}
        n = sum(1 for b in r.bets if b.player_id == player_id and b.status == "accepted")
        if n >= cfg.max_bets_per_player_per_round:
            return {"ok": False, "code": "VALIDATION_ERROR", "message": "Per-round bet limit reached"}
        return {"ok": True}

    def place_bet(self, player_id: str, position: str, amount: int,
                  idempotency_key: str, now_ms: int) -> Bet:
        """Full decision under lock: re-check window AFTER lock (race fix),
        replay same key, stamp decision_time, enforce invariant."""
        with self.lock:
            r = self.round
            if r is None:
                raise LifecycleError("No active round")
            # Idempotent replay first (same key -> same bet, no double debit).
            for b in r.bets:
                if b.idempotency_key == idempotency_key:
                    if (b.player_id, b.position, b.amount) != (player_id, position, amount):
                        from common import envelope as _e  # noqa
                        raise LifecycleError("DUPLICATE_REQUEST: key reused with different payload")
                    return b
            decision_time = now_ms  # server clock read under lock
            if not self._window_open(r, decision_time):
                raise LifecycleError("BETTING_CLOSED")
            v = self.validate_bet(player_id, position, amount, decision_time)
            if not v["ok"]:
                raise LifecycleError(v["code"] + ": " + v["message"])
            assert decision_time < r.betting_end_at_ms  # invariant, else no-debit reject above
            self._bet_seq += 1
            bet = Bet(f"{r.round_id}-b{self._bet_seq}", r.round_id, player_id,
                      position, amount, decision_time, idempotency_key)
            r.bets.append(bet)
            r.emit("bet.accepted", {"bet_id": bet.bet_id, "player": player_id,
                                    "position": position, "amount": amount}, decision_time)
            return bet

    def close_betting(self, now_ms: int) -> Round:
        """Timer expiry path. Same lock: flips status first (race fix)."""
        with self.lock:
            r = self.round
            if r is None:
                raise LifecycleError("No active round")
            if r.status == RoundStatus.BETTING_OPEN:
                transition(r.status, RoundStatus.BETTING_CLOSED)
                r.status = RoundStatus.BETTING_CLOSED
                r.emit("betting.closed", {"round_id": r.round_id}, now_ms)
            return r

    def calculate_result(self, now_ms: int) -> Round:
        with self.lock:
            r = self.round
            if r is None:
                raise LifecycleError("No active round")
            if r.status != RoundStatus.BETTING_CLOSED:
                raise LifecycleError(f"Result requires BETTING_CLOSED, have {r.status}")
            scored = {p: evaluate_hand(h) for p, h in r.hands.items()}
            best = max(scored.values())
            r.winner_positions = sorted(p for p, s in scored.items() if s == best)
            transition(r.status, RoundStatus.RESULT)
            r.status = RoundStatus.RESULT
            r.emit("result.published", {
                "round_id": r.round_id,
                "winners": r.winner_positions,
                "hands": {p: [f"{rk}{st}" for rk, st in h] for p, h in r.hands.items()},
                "deck_commit": r.deck_commit, "seed": r.seed_hex}, now_ms)
            return r

    def settle(self, now_ms: int) -> List[dict]:
        """Deterministic, idempotent-by-construction: re-running returns the
        SAME rows (service layer additionally guards UNIQUE settlement.bet_id)."""
        with self.lock:
            r = self.round
            if r is None:
                raise LifecycleError("No active round")
            if r._settled:
                return r.settlements  # replay after CLOSED: identical rows, no double-pay
            if r.status != RoundStatus.RESULT:
                raise LifecycleError(f"Settle requires RESULT, have {r.status}")
            pot = sum(b.amount for b in r.bets if b.status == "accepted") + r.carry_in
            rake = pot * self.config.rake_bps // 10_000
            distributable = pot - rake
            winners = set(r.winner_positions)
            win_bets = [b for b in r.bets if b.position in winners and b.status == "accepted"]
            rows: List[dict] = []
            for b in r.bets:  # losers recorded with payout 0 (traceability)
                if b.status == "accepted" and b.position not in winners:
                    b.status = "lost"
                    rows.append({"settlement_id": f"stl-{b.bet_id}", "bet_id": b.bet_id,
                                 "player_id": b.player_id, "payout": 0,
                                 "config_version": r.config_version})
            if win_bets and distributable > 0:
                # Tie-fair split: distributable divided EQUALLY among winning
                # POSITIONS first (no bias to heavily-staked side), then
                # pro-rata by stake within each position. Floor dust -> carry.
                npos = len(winners)
                per_pos = distributable // npos
                dust = distributable - per_pos * npos
                for pos in sorted(winners):
                    pbets = [b for b in win_bets if b.position == pos]
                    stake = sum(b.amount for b in pbets)
                    paid = 0
                    for i, b in enumerate(sorted(pbets, key=lambda x: x.bet_id)):
                        share = per_pos * b.amount // stake if stake else 0
                        if i == len(pbets) - 1:
                            share = per_pos - paid
                        else:
                            paid += share
                        b.status = "won"
                        rows.append({"settlement_id": f"stl-{b.bet_id}", "bet_id": b.bet_id,
                                     "player_id": b.player_id, "payout": share,
                                     "config_version": r.config_version})
                r.carry_out = dust
            else:
                # No winnable bets (zero bets, or no bets on winners):
                # carry distributable forward (JEV tie fix); losers already recorded.
                r.carry_out = distributable
            self.carry_over = r.carry_out
            r.settlements = rows
            r._settled = True
            transition(r.status, RoundStatus.SETTLED)
            r.status = RoundStatus.SETTLED
            r.emit("settlement.completed", {"round_id": r.round_id, "pot": pot,
                                            "rake": rake, "carry_out": r.carry_out}, now_ms)
            transition(r.status, RoundStatus.CLOSED)
            r.status = RoundStatus.CLOSED
            r.emit("session.completed", {"round_id": r.round_id}, now_ms)
            return rows

    def cancel(self, reason: str, now_ms: int) -> List[Bet]:
        """Void path: accepted bets -> voided (service layer compensates wallet).
        Only before RESULT."""
        with self.lock:
            r = self.round
            if r is None:
                raise LifecycleError("No active round")
            if r.status in (RoundStatus.RESULT, RoundStatus.SETTLED, RoundStatus.CLOSED):
                raise LifecycleError("Too late to cancel")
            voided = [b for b in r.bets if b.status == "accepted"]
            for b in voided:
                b.status = "voided"
            transition(r.status, RoundStatus.CLOSED)
            r.status = RoundStatus.CLOSED
            r.emit("round.cancelled", {"round_id": r.round_id, "reason": reason,
                                       "voided": len(voided)}, now_ms)
            return voided

    # ---- views ----
    def snapshot(self, viewer: str, now_ms: int) -> dict:
        r = self.round
        if r is None:
            return {"room_id": self.room_id, "round": None, "serverTime": now_ms}
        reveal = r.status in (RoundStatus.RESULT, RoundStatus.SETTLED, RoundStatus.CLOSED)
        pots: Dict[str, int] = {}
        mine = 0
        for b in r.bets:
            if b.status in ("accepted", "won"):
                pots[b.position] = pots.get(b.position, 0) + b.amount
                if b.player_id == viewer:
                    mine += b.amount
        return {
            "room_id": self.room_id,
            "round_id": r.round_id,
            "round_no": r.round_no,
            "status": r.status.value,
            "serverTime": now_ms,
            "betting_end_at": r.betting_end_at_ms,
            "pots": pots, "pot_total": sum(pots.values()) + r.carry_in,
            "my_bet": mine, "carry_in": r.carry_in,
            "hands": ({p: [f"{rk}{st}" for rk, st in h] for p, h in r.hands.items()}
                      if reveal else {p: ["**"] * 3 for p in r.hands}),
            "winners": r.winner_positions if reveal else [],
            "config_version": r.config_version,
        }

    def events_since(self, seq: int, viewer: str) -> List[dict]:
        """Missed-event replay for reconnect. Redacts pre-RESULT hands."""
        r = self.round
        if r is None:
            return []
        reveal = r.status in (RoundStatus.RESULT, RoundStatus.SETTLED, RoundStatus.CLOSED)
        out = []
        for ev in r.events:
            if ev["seq"] <= seq:
                continue
            ev = dict(ev)
            if ev["kind"] == "result.published" and not reveal:
                ev = {k: v for k, v in ev.items() if k not in ("hands", "seed")}
            out.append(ev)
        return out[-self.config.event_log_cap:]
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
    def __init__(self, config: TeenPattiConfig = DEFAULT_CONFIG,
                 wallet: WalletAdapter = None,
                 idempotency: IdempotencyStore = None,
                 tokens: TokenStore = None,
                 sessions: SessionStore = None,
                 webhook_destinations: List[str] = None,
                 webhook_secret: str = "dev-secret"):
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
        self._now = lambda: int(time.time() * 1000)

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
        return {"round_id": r.round_id, "status": r.status.value,
                "betting_end_at": r.betting_end_at_ms}

    def close_betting(self, room_id: str) -> dict:
        r = self._room(room_id).close_betting(self._now())
        self._fire("betting.closed", {"round_id": r.round_id})
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
        # 10: create bet under room lock (re-checks window; race-fixed)
        try:
            bet = room.place_bet(player_id, position, amount, idempotency_key, self._now())
        except LifecycleError as exc:
            # Window slammed shut after debit: compensate immediately (append-only).
            msg = str(exc)
            if "BETTING_CLOSED" in msg:
                self.wallet.credit(player_id, amount,
                                   ref=f"bet-void:{room_id}:{idempotency_key}",
                                   idempotency_key=f"bet-void:{idempotency_key}")
                self._fire("bet.rejected", {"room_id": room_id, "player_id": player_id,
                                            "reason": "BETTING_CLOSED_RACE_REFUNDED"})
                raise ServiceError(E.E_WINDOW_CLOSED, "Betting closed (stake refunded)")
            raise ServiceError(E.E_CONFLICT, msg)
        result = {"bet_id": bet.bet_id, "round_id": bet.round_id, "position": position,
                  "amount": amount, "decision_time": bet.decision_time_ms}
        self.idempotency.complete(f"bet:{idempotency_key}", result)
        self.audit.record(player_id, "bet.place", "bet", bet.bet_id, after=result)
        self._fire("bet.accepted", {"bet_id": bet.bet_id, "room_id": room_id, **result})
        return result

    # ---- result + settle ----
    def publish_result(self, room_id: str) -> dict:
        room = self._room(room_id)
        r = room.calculate_result(self._now())
        self.audit.record("system", "result.publish", "round", r.round_id,
                          after={"winners": r.winner_positions})
        self._fire("result.published", {"round_id": r.round_id,
                                        "winners": r.winner_positions})
        return {"round_id": r.round_id, "winners": r.winner_positions,
                "hands": {p: [f"{rk}{st}" for rk, st in h] for p, h in r.hands.items()},
                "deck_commit": r.deck_commit, "seed": r.seed_hex}

    def settle(self, room_id: str, round_id: str = "") -> dict:
        self._require_confirmed()
        room = self._room(room_id)
        rows = room.settle(self._now())
        credited = []
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
        return {"round_id": room.round.round_id, "settlements": rows,
                "carry_out": room.round.carry_out}

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
