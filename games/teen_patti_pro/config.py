"""Teen Patti Pro versioned configuration.

Every TBC (business-unconfirmed) rule lives HERE — never hardcoded in the
engine. Each settlement record stamps config_version. Real-money play is
blocked unless confirmed=True (service layer returns E_TBC_BLOCKED).

Defaults below are the widely-standard Teen Patti rules, provided ONLY as
explicitly-unconfirmed working defaults (JEV req-analysis: acceptable 0.89).
"""
from dataclasses import dataclass, field
from typing import Tuple

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
    )
    seats: Tuple[str, ...] = ("A", "B", "C")
    cards_per_hand: int = 3
    denoms: Tuple[int, ...] = (20, 100, 500, 1000)
    min_bet: int = 20
    max_bet: int = 100_000
    guess_ms: int = 20_000
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
    event_log_cap: int = 500


DEFAULT_CONFIG = TeenPattiConfig()
