"""Teen Patti Pro engine plugin registration (Game 1, live).

Display name "Teen Patti Pro" (DearLive current code `teen_patti` resolves
here as an alias — same engine, same records).

DECLARED SHAPE (metadata only, drives no engine behaviour):
  gameType  = three-seat-card-comparison
  variant   = seat-betting-highest-hand, 3 seats (A/B/C), 3 cards per seat

This is a seat-betting highest-hand variant. It is NOT the traditional
multi-round Teen Patti betting sequence: Blind, Chaal, Pack/Fold-as-Teen-
Patti, Show, Side Show and multi-round betting are all deliberately OUT OF
SCOPE and must not be introduced because the product is called "Teen Patti".

All of card ranking, tie-break, payout and dealing are config-driven, and the
engine flow (round -> betting open -> validate -> debit -> close -> deal ->
highest hand -> publish -> settle -> history) is fixed. A future confirmed
rule set must be expressible as new config values without rewriting the
engine, UI, API, wallet, settlement, WebSocket or admin surface.
"""
from common import plugins
from common.plugins import engine_plugin
from .config import DEFAULT_CONFIG
from .engine import Room

# TBC — BUSINESS CONFIRMATION REQUIRED: cardsPerSeat is declared as 3 to match
# the current deal, but it is configurable and not yet signed off by business.
VARIANT = (
    ("type", "seat-betting-highest-hand"),
    ("seats", 3),
    ("positions", ("A", "B", "C")),
    ("cardsPerSeat", 3),
)


@engine_plugin("teen-patti-pro", "Teen Patti Pro", "1.1.0", status="live",
               tbc=DEFAULT_CONFIG.tbc,
               description="3-seat Teen Patti guessing game; server-authoritative.",
               entry="/teen-patti-pro/", dearlive_code="teen_patti",
               game_type="three-seat-card-comparison",
               variant=VARIANT,
               rules_status=plugins.RULES_STATUS_TBC)
def make_room(room_id: str, config=DEFAULT_CONFIG) -> Room:
    return Room(room_id, config)


# DearLive-current code compat (Uradhura seed internalCode `teen_patti`).
plugins.alias("teen_patti", "teen-patti-pro")
