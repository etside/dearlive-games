"""Teen Patti Pro versioned configuration.

Every TBC (business-unconfirmed) rule lives HERE — never hardcoded in the
engine. Each settlement record stamps config_version. Real-money play is
blocked unless confirmed=True (service layer returns E_TBC_BLOCKED).

Defaults below are the widely-standard Teen Patti rules, provided ONLY as
explicitly-unconfirmed working defaults (JEV req-analysis: acceptable 0.89).
"""
from dataclasses import dataclass, field
from typing import Dict, Tuple

CONFIG_VERSION = "tpp-1.0.0-tbc"


@dataclass(frozen=True)
class TeenPattiConfig:
    version: str = CONFIG_VERSION
    confirmed: bool = False  # business sign-off flips this (new version)
    # TBC rule ids (BRD refs). Engine behaviour for each is the default below.
    tbc: Tuple[str, ...] = (
        "G3-BR-01",  # variant / ranking table
        "G3-BR-02",  # 3 cards per position
        "G3-BR-03",  # pot / payout / tie handling
        "G3-BR-04",  # timer duration
        "G3-BR-05-MECH",  # RNG mechanism choice (server CSPRNG + audit proof)
        "DENOMS",    # 20/100/500/1K reference only
        "SEATS",     # fixed A/B/C
        "TIE-REMAINDER",  # carry-over default (JEV design fix)
        "A23-RANK",  # A-2-3 straight placement: lowest/highest/ace-high-14
        "JOKER",     # wild jokers in deck; count + substitution rule
    )
    jokers: int = 0  # wild jokers added to the 52-deck (reference uses 3; TBC)
    seats: Tuple[str, ...] = ("A", "B", "C")
    cards_per_hand: int = 3
    # Named chip ladders. The ladder is table configuration, not a client
    # choice: the client must never offer a chip the server will reject. "low"
    # fits a 10,000 demo balance; "high" is for a banked table where 100,000
    # is a single bet. Selected with `chip_set`.
    chip_set: str = "low"
    denoms: Tuple[int, ...] = (20, 100, 500, 1000)
    min_bet: int = 20
    max_bet: int = 100_000
    # SRS section 1: 30s betting by default, configurable per deployment.
    guess_ms: int = 30_000
    max_bets_per_player_per_round: int = 50
    # Ranking (standard): trail > pure_seq > seq > color > pair > high.
    # Ace high (A-K-Q) and Ace-low (A-2-3) straights admitted. TBC G3-BR-01.
    ranking_order: Tuple[str, ...] = ("high", "pair", "color", "seq", "pure_seq", "trail")
    # A-2-3 placement (reference algo compare §4; three live variants):
    #   "lowest"  : A-2-3 is the lowest straight (modern casino default; current).
    #   "second"  : Ace always 14 -> A-2-3 sits just below A-K-Q (esrrhs ref behavior).
    #   "highest" : A-2-3 beats A-K-Q (traditional / Teen Patti Gold style).
    ace_low_rank: str = "lowest"
    tie_policy: str = "carry_over"  # or "house" / "round_robin" (business choice)
    rake_bps: int = 0  # basis points taken from pot; 0 default (TBC)
    max_win_cap: int = 100  # max win multiplier (e.g., 100 = 100x bet); 0 = no cap
    event_log_cap: int = 500


DEFAULT_CONFIG = TeenPattiConfig()


# The two ladders, kept beside the config so a room cannot name a chip_set the
# server has no denominations for.
CHIP_SETS: Dict[str, Tuple[int, ...]] = {
    "low": (20, 100, 500, 1000),
    "high": (1000, 10_000, 50_000, 100_000),
}


def denoms_for(chip_set: str, explicit: Tuple[int, ...] = ()) -> Tuple[int, ...]:
    """Denominations for a chip_set, with `explicit` winning when given.

    Resolving this on the server is the whole point: the client reads the
    resulting `denoms` and never decides the ladder itself.
    """
    if explicit:
        return tuple(int(d) for d in explicit)
    return CHIP_SETS.get(str(chip_set or "low").lower(), CHIP_SETS["low"])
