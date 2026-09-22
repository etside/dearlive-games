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

Card = Tuple[int, str]  # (rank, suit); joker is the JOKER sentinel below
JOKER: Card = (0, "J")


def is_joker(c: Card) -> bool:
    return c == JOKER


def fmt_card(c: Card) -> str:
    return "JOK" if is_joker(c) else f"{c[0]}{c[1]}"


def build_deck(n_jokers: int = 0) -> List[Card]:
    if not 0 <= n_jokers <= 3:
        raise ValueError("jokers must be 0..3")
    return [(r, s) for s in SUITS for r in RANKS] + [JOKER] * n_jokers


def shuffle_deck(seed_hex: str, n_jokers: int = 0) -> List[Card]:
    """Deterministic Fisher-Yates driven by seed. Same seed -> same deck.
    Jokers (if configured) are part of the single shuffled deck, so their
    positions are covered by the same seed + deck commitment audit."""
    deck = build_deck(n_jokers)
    rng = random.Random(int(seed_hex, 16))
    rng.shuffle(deck)
    return deck


def deck_commitment(deck: List[Card]) -> str:
    return hashlib.sha256(repr(deck).encode()).hexdigest()


def _is_sequence(ranks: List[int], ace_low_rank: str = "lowest") -> Tuple[bool, Tuple]:
    """Returns (is_seq, tiebreak_tuple). Admits A-K-Q and A-2-3.
    A-2-3 placement follows config (reference compare §4):
      lowest -> tiebreak (3,) i.e. below 2-3-4;
      second -> tiebreak (14, 3, 2), just below A-K-Q (esrrhs behavior);
      highest -> tiebreak (15,) i.e. above A-K-Q (traditional style)."""
    s = sorted(ranks)
    if s == [2, 3, 14]:
        if ace_low_rank == "highest":
            return True, (15,)
        if ace_low_rank == "second":
            return True, (14, 3, 2)
        return True, (3,)
    if s[2] - s[0] == 2 and len(set(s)) == 3:
        # Full descending ranks (not just high): required so A-K-Q (14,13,12)
        # beats A-2-3-as-(14,3,2) in "second" mode. Order-identical to (high,)
        # for all normal straights since their highs always differ.
        return True, (s[2], s[1], s[0])
    return False, ()


def best_expansion(hand: List[Card], ace_low_rank: str = "lowest") -> List[Card]:
    """Resolve wild jokers to the optimal substitution (reference getMax).
    No jokers -> hand unchanged. Rules mirror esrrhs maxCards: substitutes must
    come from the 52-deck excluding cards already present (no duplicates).
      3 jokers        -> trail of Aces (global max).
      2 jokers + [x]  -> trail of x's rank (three suits != x's suit).
      1 joker + [a,b] -> best of 52-minus-present candidates by evaluate_hand.
    """
    js = sum(1 for c in hand if is_joker(c))
    if js == 0:
        return list(hand)
    plain = [c for c in hand if not is_joker(c)]
    if js == 3:
        return [(14, "S"), (14, "H"), (14, "D")]
    if js == 2:
        # Keep the dealt card (audit fidelity, like reference maxCards keeping
        # `left`); add two same-rank cards in the other suits -> trail of x.
        (x,) = plain
        others = [(x[0], s) for s in SUITS if s != x[1]][:2]
        return [x] + others
    # 1 joker: exhaustive over legal substitutes (<=52 evals, trivial cost).
    present = set(plain)
    best, best_key = None, None
    for r in RANKS:
        for s in SUITS:
            cand = (r, s)
            if cand in present:
                continue
            resolved = plain + [cand]
            key = evaluate_hand(resolved, ace_low_rank)
            if best_key is None or key > best_key:
                best, best_key = resolved, key
    return best


def score_hand(hand: List[Card], ace_low_rank: str = "lowest") -> Tuple[int, Tuple]:
    """Authoritative hand score: resolve jokers first, then O(1) table lookup
    for plain hands (parity-guaranteed with evaluate_hand by construction)."""
    if any(is_joker(c) for c in hand):
        return evaluate_hand(best_expansion(hand, ace_low_rank), ace_low_rank)
    from . import table as _table
    try:
        return _table.score(hand, ace_low_rank)
    except (ValueError, KeyError):
        return evaluate_hand(hand, ace_low_rank)  # fail-open to live evaluator


def evaluate_hand(hand: List[Card], ace_low_rank: str = "lowest") -> Tuple[int, Tuple]:
    """-> (category_rank, tiebreak). Higher wins. Standard Teen Patti
    (TBC G3-BR-01 default): trail 6 > pure_seq 5 > seq 4 > color 3 > pair 2 > high 1.
    """
    ranks = sorted(c[0] for c in hand)
    suits = [c[1] for c in hand]
    flush = len(set(suits)) == 1
    seq, seq_tb = _is_sequence(ranks, ace_low_rank)
    if ranks[0] == ranks[2]:
        return 6, (ranks[0],)
    if flush and seq:
        return 5, seq_tb
    if seq:
        return 4, seq_tb
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
    resolved: Dict[str, List[Card]] = field(default_factory=dict)  # jokers expanded
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
            deck = shuffle_deck(r.seed_hex, self.config.jokers)
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
            transition(r.status, RoundStatus.RESULT_PROCESSING)
            r.status = RoundStatus.RESULT_PROCESSING
            r.emit("result.processing", {"round_id": r.round_id}, now_ms)
            r.resolved = {p: best_expansion(h, self.config.ace_low_rank)
                          for p, h in r.hands.items()}
            scored = {p: evaluate_hand(h, self.config.ace_low_rank)
                      for p, h in r.resolved.items()}
            best = max(scored.values())
            r.winner_positions = sorted(p for p, s in scored.items() if s == best)
            transition(r.status, RoundStatus.RESULT)
            r.status = RoundStatus.RESULT
            r.emit("result.published", {
                "round_id": r.round_id,
                "winners": r.winner_positions,
                "hands": {p: [fmt_card(c) for c in h] for p, h in r.resolved.items()},
                "raw_hands": {p: [fmt_card(c) for c in h] for p, h in r.hands.items()},
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
                # Dead-heat rule (JEV money-out FLAG split_unfair 0.89 — FIXED):
                # equal-per-position could make a "winner" LOSE money
                # (e.g. stakes A=100/B=900 tie -> B side paid 500 < staked 900).
                # Industry-standard dead heat: distributable shared PRO-RATA
                # by stake across ALL winning bets. Guarantees payout >= stake
                # per bet when rake == 0 (no winner ever loses on a win).
                # Floor dust -> carry_out (no seat bias). TBC G3-BR-03.
                stake = sum(b.amount for b in win_bets)
                ordered = sorted(win_bets, key=lambda x: x.bet_id)
                paid = 0
                for i, b in enumerate(ordered):
                    share = distributable * b.amount // stake if stake else 0
                    if i == len(ordered) - 1:
                        share = distributable - paid
                    else:
                        paid += share
                    b.status = "won"
                    rows.append({"settlement_id": f"stl-{b.bet_id}", "bet_id": b.bet_id,
                                 "player_id": b.player_id, "payout": share,
                                 "config_version": r.config_version})
                r.carry_out = 0
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
            "hands": ({p: [fmt_card(c) for c in r.resolved.get(p, h)]
                       for p, h in r.hands.items()}
                      if reveal else {p: ["**"] * 3 for p in r.hands}),
            "raw_hands": ({p: [fmt_card(c) for c in h] for p, h in r.hands.items()}
                          if reveal and self.config.jokers else {}),
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
                ev = {k: v for k, v in ev.items()
                      if k not in ("hands", "raw_hands", "seed")}
            out.append(ev)
        return out[-self.config.event_log_cap:]
