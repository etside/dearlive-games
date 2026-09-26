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
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from common.lifecycle import (RoundStatus, transition, LifecycleError,
                              SETTLE_MAX_ATTEMPTS, settle_backoff_ms)
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


def best_expansion(hand: List[Card], ace_low_rank: str = "lowest",
                   ranking_order: Optional[Tuple[str, ...]] = None) -> List[Card]:
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
            key = evaluate_hand(resolved, ace_low_rank, ranking_order)
            if best_key is None or key > best_key:
                best, best_key = resolved, key
    return best


def score_hand(hand: List[Card], ace_low_rank: str = "lowest",
               ranking_order: Optional[Tuple[str, ...]] = None) -> Tuple[int, Tuple]:
    """Authoritative hand score: resolve jokers first, then O(1) table lookup
    for plain hands (parity-guaranteed with evaluate_hand by construction).

    The lookup table is precomputed under the DEFAULT ranking. Under an
    admin-configured order the cached numbers are wrong -- the category names
    are still right but their strengths are not -- so a non-default order
    bypasses the cache. That is the correct trade: an admin who reorders the
    ranking is a rare, deliberate act, and silently scoring against a stale
    table would be far worse than a slower evaluation.
    """
    custom = bool(ranking_order) and tuple(ranking_order) != DEFAULT_RANKING_ORDER
    if any(is_joker(c) for c in hand):
        return evaluate_hand(best_expansion(hand, ace_low_rank, ranking_order),
                             ace_low_rank, ranking_order)
    if custom:
        return evaluate_hand(hand, ace_low_rank, ranking_order)
    from . import table as _table
    try:
        return _table.score(hand, ace_low_rank)
    except (ValueError, KeyError):
        return evaluate_hand(hand, ace_low_rank, ranking_order)  # fail-open


# The hand categories the evaluator can produce, weakest first. This is the
# vocabulary an operator reorders in `ranking_order`; it is deliberately a
# module constant so a typo in an admin-supplied order is a loud KeyError at
# validation time rather than a silently ignored rule.
HAND_CATEGORIES = ("high", "pair", "color", "seq", "pure_seq", "trail")

# The documented default, weakest first. Kept here so score_hand can tell an
# admin-configured order from the default without importing the config module
# (which would be circular: config imports nothing from engine, but engine
# importing config at module scope would be).
DEFAULT_RANKING_ORDER = HAND_CATEGORIES


def rank_scale(ranking_order: Tuple[str, ...]) -> Dict[str, int]:
    """Map each category to its numeric strength from an admin-supplied order.

    BR-11 requires the ranking to be admin-configurable. The previous code
    returned hardcoded 6/5/4/3/2/1 and never read `ranking_order` at all, so the
    config field was decoration: an operator could reorder it and nothing would
    change. Now the order in the config *is* the order in force.
    """
    if not ranking_order:
        return {}
    unknown = [c for c in ranking_order if c not in HAND_CATEGORIES]
    if unknown:
        raise ValueError(f"unknown hand categories in ranking_order: {unknown}")
    if len(set(ranking_order)) != len(ranking_order):
        raise ValueError(f"duplicate categories in ranking_order: {ranking_order}")
    # Weakest first -> rank 1, strongest -> rank N. Higher wins.
    return {cat: i + 1 for i, cat in enumerate(ranking_order)}


def evaluate_hand(hand: List[Card], ace_low_rank: str = "lowest",
                  ranking_order: Optional[Tuple[str, ...]] = None) -> Tuple[int, Tuple]:
    """-> (category_rank, tiebreak). Higher wins.

    Default order (TBC G3-BR-01): trail > pure_seq > seq > color > pair > high,
    which is the same as returning 6/5/4/3/2/1. Pass `ranking_order` to apply
    an admin-configured ordering; the tiebreak within a category is unchanged
    because it is a property of the cards, not of the ordering.
    """
    ranks = sorted(c[0] for c in hand)
    suits = [c[1] for c in hand]
    flush = len(set(suits)) == 1
    seq, seq_tb = _is_sequence(ranks, ace_low_rank)

    if ranks[0] == ranks[2]:
        category, tiebreak = "trail", (ranks[0],)
    elif flush and seq:
        category, tiebreak = "pure_seq", seq_tb
    elif seq:
        category, tiebreak = "seq", seq_tb
    elif flush:
        category, tiebreak = "color", tuple(sorted(ranks, reverse=True))
    elif ranks[0] == ranks[1]:
        category, tiebreak = "pair", (ranks[0], ranks[2])
    elif ranks[1] == ranks[2]:
        category, tiebreak = "pair", (ranks[1], ranks[0])
    else:
        category, tiebreak = "high", tuple(sorted(ranks, reverse=True))

    if ranking_order:
        return rank_scale(ranking_order)[category], tiebreak
    # No order supplied: the documented default, weakest first.
    return HAND_CATEGORIES.index(category) + 1, tiebreak


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
    # BR-14 payout ceiling for this bet (max_win_cap x amount). None = no cap.
    cap_limit: Optional[int] = None


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
    config_snapshot: dict = field(default_factory=dict)
    events: List[dict] = field(default_factory=list)
    _seq: int = 0
    _settled: bool = False
    # --- settlement-failure bookkeeping (see common.lifecycle) ---
    _settle_attempts: int = 0
    _settle_error: str = ""
    _settle_pending_since_ms: int = 0
    _settle_last_attempt_ms: int = 0
    # The real status to restore before a retry. SETTLED_PENDING cannot say
    # whether calculate_result() or settle() still needs to run.
    _settle_resume_from: Optional[str] = None

    # BR-14: per-bet payout ceiling, set by _cap_overage when the configured
    # max_win_cap binds. Empty means no cap applied. cap_binds records the
    # change_ids affected so the result payload can report it.
    cap_binds: List[dict] = field(default_factory=list)

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

    def leave_session(self, player_id: str) -> dict:
        with self.lock:
            removed = player_id in self.members
            self.members.pop(player_id, None)
            return {"room_id": self.room_id, "player_id": player_id,
                    "removed": removed, "seats": sorted(self.members)}

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
                       config_version=self.config.version,
                       config_snapshot=asdict(self.config))
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
            # Only seats that actually staked are candidates. Previously every
            # dealt seat was scored, so with 3 seats and 2 players the best hand
            # landing on the EMPTY seat voided the pot: both players lost their
            # entire stake to cards nobody had bet on and the money carried
            # forward. Only money in the pot may win it.
            staked = {b.position for b in r.bets if b.status == "accepted"}
            candidates = {p: h for p, h in r.resolved.items() if p in staked}
            if not candidates:
                # Nobody staked: no result to declare.
                r.winner_positions = []
                transition(r.status, RoundStatus.RESULT)
                r.status = RoundStatus.RESULT
                r.emit("result.published", {
                    "round_id": r.round_id, "winners": [],
                    "hands": {p: [fmt_card(c) for c in h]
                              for p, h in r.resolved.items()},
                    "raw_hands": {p: [fmt_card(c) for c in h]
                                  for p, h in r.hands.items()},
                    "deck_commit": r.deck_commit, "seed": r.seed_hex}, now_ms)
                return r
            scored = {p: evaluate_hand(h, self.config.ace_low_rank,
                                        self.config.ranking_order)
                      for p, h in candidates.items()}
            best = max(scored.values())
            r.winner_positions = sorted(p for p, s in scored.items() if s == best)
            # BR-14: cap a single win at max_win_cap x the winner's own stake.
            # A cap is meaningless unless it can change the payout, so it is
            # applied where the money splits, not merely reported.
            self._cap_overage(r)
            transition(r.status, RoundStatus.RESULT)
            r.status = RoundStatus.RESULT
            r.emit("result.published", {
                "round_id": r.round_id,
                "winners": r.winner_positions,
                "hands": {p: [fmt_card(c) for c in h] for p, h in r.resolved.items()},
                "raw_hands": {p: [fmt_card(c) for c in h] for p, h in r.hands.items()},
                "deck_commit": r.deck_commit, "seed": r.seed_hex}, now_ms)
            return r

    def _cap_overage(self, r: Round) -> None:
        """Record how much a max-win cap would claw back, per winning bet.

        Deliberately non-destructive at this point: the winner set is already
        published and the cap is applied in settle(), where payouts are built.
        Storing the per-bet allowance here keeps the arithmetic in one place
        and lets the result payload report that a cap bound, which is the
        information an operator needs to explain a smaller-than-expected win.
        """
        cap_mult = getattr(self.config, "max_win_cap", 0) or 0
        r.cap_binds = []
        if cap_mult <= 0:
            return
        for b in r.bets:
            if b.position in set(r.winner_positions) and b.status == "accepted":
                b.cap_limit = cap_mult * b.amount

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
                shares = [distributable * b.amount // stake if stake else 0
                          for b in ordered]
                # Last one absorbs the rounding remainder, as before.
                if shares:
                    shares[-1] = distributable - sum(shares[:-1])

                # BR-14: clamp each payout to its own ceiling. The clamped
                # amount does NOT vanish -- it goes back to carry_out, so the
                # house keeps it rather than the money being created or lost.
                # Previously max_win_cap was a config field nothing read, so an
                # operator who set a cap had no cap.
                capped = []
                for b, share in zip(ordered, shares):
                    cap_applied = False
                    if b.cap_limit is not None and share > b.cap_limit:
                        capped.append({"bet_id": b.bet_id,
                                       "uncapped_payout": share,
                                       "cap_limit": b.cap_limit})
                        share = b.cap_limit
                        cap_applied = True
                    rows.append({"settlement_id": f"stl-{b.bet_id}",
                                 "bet_id": b.bet_id,
                                 "player_id": b.player_id, "payout": share,
                                 "cap_applied": cap_applied,
                                 "config_version": r.config_version})
                    b.status = "won"
                r.cap_binds = capped
                clawback = sum(c["uncapped_payout"] - c["cap_limit"]
                               for c in capped)
                r.carry_out = clawback
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

    # ---- settlement-failure retry (money safety) ----
    # A round whose result/settlement raised must never be silently abandoned.
    # These methods only move status + bookkeeping; the service layer owns the
    # re-drive, because only it knows how to re-run the wallet credit loop.

    def mark_settle_pending(self, error: str, now_ms: int) -> Round:
        """Record a settlement failure and park the round in SETTLED_PENDING.

        Preserves the current status in _settle_resume_from so a retry knows
        which step to re-run. Increments the attempt counter.
        """
        with self.lock:
            r = self.round
            if r is None:
                raise LifecycleError("No active round")
            if r.status in (RoundStatus.SETTLED_PENDING, RoundStatus.SETTLE_FAILED):
                return r  # already parked; do not double-count or clobber resume point
            r._settle_resume_from = r.status.value
            transition(r.status, RoundStatus.SETTLED_PENDING)
            r.status = RoundStatus.SETTLED_PENDING
            r._settle_attempts += 1
            r._settle_error = str(error)
            if r._settle_pending_since_ms == 0:
                r._settle_pending_since_ms = now_ms
            r._settle_last_attempt_ms = now_ms
            r.emit("settlement.pending", {
                "round_id": r.round_id,
                "attempt": r._settle_attempts,
                "max_attempts": SETTLE_MAX_ATTEMPTS,
                "resume_from": r._settle_resume_from,
                "error": str(error),
                "pending_since": r._settle_pending_since_ms,
            }, now_ms)
            return r

    def settle_retry_due(self, now_ms: int) -> bool:
        """True when a parked round is past its backoff and attempts remain."""
        with self.lock:
            r = self.round
            if r is None or r.status != RoundStatus.SETTLED_PENDING:
                return False
            if r._settle_attempts >= SETTLE_MAX_ATTEMPTS:
                return False
            if r._settle_last_attempt_ms == 0:
                return True
            wait = settle_backoff_ms(r._settle_attempts)
            return (now_ms - r._settle_last_attempt_ms) >= wait

    def settle_attempts_exhausted(self) -> bool:
        with self.lock:
            r = self.round
            return (r is not None and r.status == RoundStatus.SETTLED_PENDING
                    and r._settle_attempts >= SETTLE_MAX_ATTEMPTS)

    def restore_resume_status(self) -> RoundStatus:
        """Restore the pre-failure status so calculate_result()/settle() accept it.

        No transition() validation here: we are undoing our own SETTLED_PENDING
        marker, and the resumed step validates the restored status itself.
        """
        with self.lock:
            r = self.round
            if r is None:
                raise LifecycleError("No active round")
            if r.status != RoundStatus.SETTLED_PENDING:
                return r.status
            resume = r._settle_resume_from or RoundStatus.RESULT.value
            r.status = RoundStatus(resume)
            return r.status

    def mark_settle_failed(self, now_ms: int) -> Round:
        """Give up after SETTLE_MAX_ATTEMPTS and alert. Never silently closes."""
        with self.lock:
            r = self.round
            if r is None:
                raise LifecycleError("No active round")
            if r.status != RoundStatus.SETTLED_PENDING:
                return r
            transition(r.status, RoundStatus.SETTLE_FAILED)
            r.status = RoundStatus.SETTLE_FAILED
            r.emit("settlement.failed", {
                "round_id": r.round_id,
                "attempts": r._settle_attempts,
                "error": r._settle_error,
                "pending_since": r._settle_pending_since_ms,
                "pot_at_risk": sum(b.amount for b in r.bets if b.status in ("accepted", "won")),
            }, now_ms)
            return r

    def settlement_health(self, now_ms: int) -> dict:
        """Per-round settlement-failure state.

        `stake_at_risk` is context only; the authoritative "money owed but
        unpaid" figure is computed by the service, which alone knows which
        settlement rows have actually been credited.
        """
        with self.lock:
            r = self.round
            if r is None:
                return {"status": None, "settle_attempts": 0, "settle_error": "",
                        "pending_since_ms": 0, "pending_age_ms": 0,
                        "stake_at_risk": 0, "resume_from": None}
            stake = sum(b.amount for b in r.bets if b.status in ("accepted", "won"))
            age = (now_ms - r._settle_pending_since_ms) if r._settle_pending_since_ms else 0
            return {"status": r.status.value,
                    "settle_attempts": r._settle_attempts,
                    "settle_error": r._settle_error,
                    "pending_since_ms": r._settle_pending_since_ms,
                    "pending_age_ms": max(0, age),
                    "stake_at_risk": stake,
                    "resume_from": r._settle_resume_from}

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
        # Seat -> player id. The client resolves each seat's avatar from the
        # admin panel by player id. Not a new disclosure: the table endpoint
        # already lists every member's id, and hands/pots are already keyed by
        # position, so the mapping is derivable by anyone playing.
        seats: Dict[str, str] = {}
        for b in r.bets:
            if b.player_id:
                seats.setdefault(b.position, b.player_id)
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
            "seats": seats,
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
