"""Common round lifecycle per BRD SRS 3.1.

UPCOMING -> BETTING_OPEN -> BETTING_CLOSED -> RESULT_PROCESSING -> RESULT
  -> SETTLED -> CLOSED

CLOSED is terminal. cancel() may move UPCOMING/BETTING_OPEN/BETTING_CLOSED
-> CLOSED (refund path handled by caller via settlement void + wallet
compensate). RESULT_PROCESSING marks the server-side result computation
window (RNG/evaluate); RESULT means the authoritative result is published.
RESULT -> SETTLED requires a recorded authoritative result.
Transitions are validated; illegal transitions raise LifecycleError.
"""
from enum import Enum


class RoundStatus(str, Enum):
    UPCOMING = "UPCOMING"
    BETTING_OPEN = "BETTING_OPEN"
    BETTING_CLOSED = "BETTING_CLOSED"
    RESULT_PROCESSING = "RESULT_PROCESSING"
    RESULT = "RESULT"
    SETTLED = "SETTLED"
    CLOSED = "CLOSED"


_ALLOWED = {
    RoundStatus.UPCOMING: {RoundStatus.BETTING_OPEN, RoundStatus.CLOSED},
    RoundStatus.BETTING_OPEN: {RoundStatus.BETTING_CLOSED, RoundStatus.CLOSED},
    RoundStatus.BETTING_CLOSED: {RoundStatus.RESULT_PROCESSING, RoundStatus.CLOSED},
    RoundStatus.RESULT_PROCESSING: {RoundStatus.RESULT, RoundStatus.CLOSED},
    RoundStatus.RESULT: {RoundStatus.SETTLED, RoundStatus.CLOSED},
    RoundStatus.SETTLED: {RoundStatus.CLOSED},
    RoundStatus.CLOSED: set(),
}


class LifecycleError(ValueError):
    pass


def can_transition(frm: RoundStatus, to: RoundStatus) -> bool:
    return to in _ALLOWED.get(frm, set())


def transition(frm: RoundStatus, to: RoundStatus) -> RoundStatus:
    if not can_transition(frm, to):
        raise LifecycleError(f"Illegal round transition {frm} -> {to}")
    return to


def bets_accepted(status: RoundStatus) -> bool:
    """Bets are accepted ONLY in BETTING_OPEN (server-side window check sits above)."""
    return status == RoundStatus.BETTING_OPEN
