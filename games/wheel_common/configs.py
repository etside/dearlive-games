"""Default wheel configurations (ALL option sets are TBC W-OPTS).

Values below mirror DearLive current shapes (weight + multiplier + icon +
colorHex + HOT flag) so the developer replaces them with approved
option lists via admin config — never by editing game logic.
"""
from dataclasses import replace

from .service import WheelConfig, WheelOption

GREEDY_DEFAULT_OPTIONS = (
    WheelOption("banana", "Banana", weight=30, multiplier=2.0, icon="🍌",
                color_hex="#fde68a"),
    WheelOption("apple", "Apple", weight=25, multiplier=2.5, icon="🍎",
                color_hex="#ef4444"),
    WheelOption("grapes", "Grapes", weight=20, multiplier=3.0, icon="🍇",
                color_hex="#8b5cf6"),
    WheelOption("mango", "Mango", weight=15, multiplier=5.0, icon="🥭",
                color_hex="#f97316", hot=True),
    WheelOption("crown", "Crown", weight=5, multiplier=20.0, icon="👑",
                color_hex="#facc15", hot=True),
)

BABY_KING_DEFAULT_OPTIONS = (
    WheelOption("tiger", "Tiger", weight=25, multiplier=2.0, icon="🐯",
                color_hex="#f97316"),
    WheelOption("lion", "Lion", weight=20, multiplier=3.0, icon="🦁",
                color_hex="#f59e0b", hot=True),
    WheelOption("panda", "Panda", weight=25, multiplier=2.5, icon="🐼",
                color_hex="#e5e7eb"),
    WheelOption("dragon", "Dragon", weight=10, multiplier=8.0, icon="🐲",
                color_hex="#22c55e"),
    WheelOption("crown", "Baby Crown", weight=5, multiplier=25.0, icon="👑",
                color_hex="#facc15", hot=True),
)

GREEDY_LION_DEFAULT_OPTIONS = (
    WheelOption("cub", "Lion Cub", weight=30, multiplier=2.0, icon="🦁",
                color_hex="#fde68a"),
    WheelOption("mane", "Golden Mane", weight=25, multiplier=2.5, icon="🦁",
                color_hex="#f59e0b"),
    WheelOption("pride", "Pride", weight=20, multiplier=3.0, icon="🐾",
                color_hex="#f97316", hot=True),
    WheelOption("savanna", "Savanna King", weight=15, multiplier=5.0, icon="🌅",
                color_hex="#ef4444"),
    WheelOption("crown", "Lion Crown", weight=5, multiplier=20.0, icon="👑",
                color_hex="#facc15", hot=True),
)


def _fresh(defaults: tuple) -> tuple:
    """Copy the option template so callers cannot mutate shared state.

    The factories used to hand every WheelConfig the SAME options list, so
    deactivating an option in one service silently disabled it in every other
    service built in the process.
    """
    return tuple(replace(option) for option in defaults)


def greedy_config() -> WheelConfig:
    return WheelConfig(game_id="greedy-monkey", version="greedy-1.0.0-tbc",
                       options=_fresh(GREEDY_DEFAULT_OPTIONS))


def baby_king_config() -> WheelConfig:
    return WheelConfig(game_id="baby-king", version="baby-king-1.0.0-tbc",
                       options=_fresh(BABY_KING_DEFAULT_OPTIONS))


def greedy_lion_config() -> WheelConfig:
    return WheelConfig(game_id="greedy-lion", version="greedy-lion-1.0.0-tbc",
                       options=_fresh(GREEDY_LION_DEFAULT_OPTIONS))
