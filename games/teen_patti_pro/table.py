"""Precomputed lookup-table evaluator (reference-inspired upgrade).

Like esrrhs/teenpatti_algorithm's teenpatti_data.txt, every possible no-joker
hand is ranked ONCE; scoring is then an O(1) dict lookup instead of per-hand
computation. Differences from the reference:
  - Built at runtime from our own evaluate_hand (single source of truth — the
    table can never disagree with the live evaluator; verified by test).
  - Covers all three ace_low_rank modes (reference hardcodes one).
  - No 1.3MB data file to ship/version; build takes ~0.3s, lazy + thread-safe.
  - Joker hands bypass the table via best_expansion (same as reference's
    resolve-then-lookup query path).

Key encoding mirrors the reference: byte=(suit<<4)|value, cards sorted,
key=c1*10000+c2*100+c3. Dense position index = rank order among unique
strengths (their rank_index concept).
"""
import itertools
import threading
from typing import Dict, List, Tuple

from .engine import SUITS, RANKS, Card, evaluate_hand

_SUIT_BITS = {"S": 0, "H": 1, "D": 2, "C": 3}
MODES = ("lowest", "second", "highest")

_lock = threading.Lock()
_TABLES: Dict[str, Dict[int, Tuple[int, Tuple]]] = {}
_POSITIONS: Dict[str, Dict[Tuple[int, Tuple], int]] = {}
_BUILT = False


def encode(hand: List[Card]) -> int:
    ordered = sorted(((_SUIT_BITS[c[1]] << 4) | c[0]) for c in hand)
    return ordered[0] * 10000 + ordered[1] * 100 + ordered[2]


def build():
    """Build all mode tables. Idempotent; returns (hands_indexed, modes)."""
    global _BUILT
    with _lock:
        if _BUILT:
            return sum(len(t) for t in _TABLES.values())
        deck = [(r, s) for s in SUITS for r in RANKS]
        total = 0
        for mode in MODES:
            table: Dict[int, Tuple[int, Tuple]] = {}
            for combo in itertools.combinations(deck, 3):
                table[encode(list(combo))] = evaluate_hand(list(combo), mode)
            strengths = sorted(set(table.values()), reverse=True)
            _POSITIONS[mode] = {s: i for i, s in enumerate(strengths)}
            _TABLES[mode] = table
            total += len(table)
        _BUILT = True
        return total


def score(hand: List[Card], mode: str = "lowest") -> Tuple[int, Tuple]:
    """O(1) table score for no-joker hands. Raises on jokers (resolve first)."""
    if any(c[0] == 0 for c in hand):
        raise ValueError("table holds no-joker hands only; use score_hand()")
    if mode not in _TABLES or not _BUILT:
        build()
    return _TABLES[mode][encode(hand)]


def position(hand: List[Card], mode: str = "lowest") -> int:
    """Dense rank position (0 = strongest). Reference rank_index equivalent."""
    return _POSITIONS[mode][score(hand, mode)]


def stats() -> dict:
    if not _BUILT:
        build()
    return {"modes": list(_TABLES), "hands_per_mode": len(next(iter(_TABLES.values()))),
            "strength_levels": {m: len(_POSITIONS[m]) for m in _TABLES}}
