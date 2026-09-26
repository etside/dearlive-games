"""Maps internal event kinds onto the two wire vocabularies.

There are three naming schemes in play and conflating them is how integrations
break:

* **internal kinds** -- what the engine emits and what the event log stores:
  ``round.started``, ``result.published``. Skills subscribe to these
  (``common/skills.py``) and the reveal-redaction path keys on
  ``result.published``, so renaming them in place would silently break both.
* **WebSocket events** (SRS section 8) -- ``round.opened``,
  ``result.declared``, ``settlement.started``.
* **Webhook events** (SRS section 14) -- ``game.round.started``,
  ``game.result.published``, ``game.settlement.completed``.

Sections 8 and 14 genuinely disagree: the spec calls the same moment
``round.opened`` on the socket and ``game.round.started`` on the webhook. So
one internal vocabulary cannot satisfy both, and the translation has to be
explicit and tested rather than implied by whichever name happens to be used
in the emit call.

Both mappings are total for every internal kind the engine can produce: an
unmapped kind raises at import-check time (see ``test_wire_event_names``)
rather than reaching a client as an unrecognised event.
"""

from __future__ import annotations

from typing import Dict

# SRS section 8: events the client receives over the WebSocket.
WS_EVENTS = (
    "round.created",
    "round.opened",
    "round.updated",
    "betting.closed",
    "result.processing",
    "result.declared",
    "settlement.started",
    "settlement.completed",
    "balance.updated",
    "round.closed",
    "error",
)

# SRS section 14: events delivered to the client's webhook endpoint.
WEBHOOK_EVENTS = (
    "game.session.created",
    "game.round.started",
    "game.bet.accepted",
    "game.result.published",
    "game.settlement.completed",
    "game.error",
)

# SRS section 14 names six webhook events. Internal kinds with no SRS
# counterpart (a player leaving, a rejected bet) are folded into game.error
# rather than invented as new event names, because a consumer that sees an
# unlisted event name has no contract to code against.

# internal kind -> WebSocket event name
WS_NAMES: Dict[str, str] = {
    "session.created": "round.created",
    # The service emits this one already-prefixed, unlike every other kind.
    "game.session.created": "round.created",
    "round.created": "round.created",
    "round.started": "round.opened",
    "round.tick": "round.updated",
    "round.updated": "round.updated",
    "bet.accepted": "round.updated",
    "bet.rejected": "round.updated",
    "betting.closed": "betting.closed",
    "result.processing": "result.processing",
    "result.published": "result.declared",
    "settlement.started": "settlement.started",
    "settlement.pending": "settlement.started",
    "settlement.failed": "error",
    "settlement.completed": "settlement.completed",
    "round.cancelled": "round.closed",
    "player.left": "round.updated",
    "error": "error",
}

# internal kind -> webhook event name
WEBHOOK_NAMES: Dict[str, str] = {
    "session.created": "game.session.created",
    "game.session.created": "game.session.created",
    "round.created": "game.round.started",
    "round.started": "game.round.started",
    "round.tick": "game.round.started",
    "bet.accepted": "game.bet.accepted",
    "bet.rejected": "game.error",
    "betting.closed": "game.result.published",
    "result.processing": "game.result.published",
    "result.published": "game.result.published",
    "settlement.started": "game.settlement.completed",
    "settlement.pending": "game.settlement.completed",
    "settlement.completed": "game.settlement.completed",
    "round.cancelled": "game.error",
    "settlement.failed": "game.error",
    "player.left": "game.error",
    "error": "game.error",
}

# SRS section 8 events the engine does not emit yet. Listed so the gap is
# explicit rather than discovered by a client waiting for an event that never
# arrives. `balance.updated` is the one still missing: emitting it per credit
# means touching every wallet path, and a client polling
# GET /api/v1/wallet/balance is correct today.
DECLARED_ONLY = {
    "balance.updated": None,
}


def ws_name(kind: str) -> str:
    """WebSocket name for an internal kind.

    An unknown kind maps to ``error`` rather than being passed through: a
    client that receives an event name it does not know has no way to handle
    it, whereas an explicit error frame at least has a path.
    """
    return WS_NAMES.get(kind, "error")


def webhook_name(kind: str) -> str:
    """Webhook event name for an internal kind."""
    return WEBHOOK_NAMES.get(kind, "game.error")
