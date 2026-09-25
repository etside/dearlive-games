"""Default wheel configurations (ALL option sets are TBC W-OPTS).

Values below mirror DearLive current shapes (weight + multiplier + icon +
colorHex + HOT flag) so the developer replaces them with approved
option lists via admin config — never by editing game logic.
"""
from dataclasses import replace

from .service import WheelConfig, WheelOption

# Asset references point to generated SVG files (generated in Phase A3)
# Format: "asset://greedy-monkey/banana" etc.
GREEDY_DEFAULT_OPTIONS = (
    WheelOption("banana", "Banana", weight=30, multiplier=2.0,
                icon="asset://greedy-monkey/banana", color_hex="#fde68a"),
    WheelOption("apple", "Apple", weight=25, multiplier=2.5,
                icon="asset://greedy-monkey/apple", color_hex="#ef4444"),
    WheelOption("grapes", "Grapes", weight=20, multiplier=3.0,
                icon="asset://greedy-monkey/grapes", color_hex="#8b5cf6"),
    WheelOption("mango", "Mango", weight=15, multiplier=5.0,
                icon="asset://greedy-monkey/mango", color_hex="#f97316", hot=True),
    WheelOption("coconut", "Coconut", weight=5, multiplier=20.0,
                icon="asset://greedy-monkey/coconut", color_hex="#facc15", hot=True),
)

BABY_KING_DEFAULT_OPTIONS = (
    WheelOption("toy_car", "Toy Car", weight=25, multiplier=2.0,
                icon="asset://baby-king/toy_car", color_hex="#f97316"),
    WheelOption("toy_rocket", "Toy Rocket", weight=20, multiplier=3.0,
                icon="asset://baby-king/toy_rocket", color_hex="#f59e0b", hot=True),
    WheelOption("teddy", "Teddy Bear", weight=25, multiplier=2.5,
                icon="asset://baby-king/teddy", color_hex="#e5e7eb"),
    WheelOption("gem", "Gem", weight=10, multiplier=8.0,
                icon="asset://baby-king/gem", color_hex="#22c55e"),
    WheelOption("baby_crown", "Baby Crown", weight=5, multiplier=25.0,
                icon="asset://baby-king/baby_crown", color_hex="#facc15", hot=True),
)

GREEDY_LION_DEFAULT_OPTIONS = (
    WheelOption("cub", "Lion Cub", weight=30, multiplier=2.0,
                icon="asset://greedy-lion/cub", color_hex="#fde68a"),
    WheelOption("mane", "Golden Mane", weight=25, multiplier=2.5,
                icon="asset://greedy-lion/mane", color_hex="#f59e0b"),
    WheelOption("pride", "Pride", weight=20, multiplier=3.0,
                icon="asset://greedy-lion/pride", color_hex="#f97316", hot=True),
    WheelOption("savanna", "Savanna King", weight=15, multiplier=5.0,
                icon="asset://greedy-lion/savanna", color_hex="#ef4444"),
    WheelOption("lion_crown", "Lion Crown", weight=5, multiplier=20.0,
                icon="asset://greedy-lion/lion_crown", color_hex="#facc15", hot=True),
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
