"""Common round lifecycle per BRD SRS 3.1.

UPCOMING -> BETTING_OPEN -> BETTING_CLOSED -> RESULT_PROCESSING -> RESULT
  -> SETTLED -> CLOSED

CLOSED is terminal. cancel() may move UPCOMING/BETTING_OPEN/BETTING_CLOSED
-> CLOSED (refund path handled by caller via settlement void + wallet
compensate). RESULT_PROCESSING marks the server-side result computation
window (RNG/evaluate); RESULT means the authoritative result is published.
RESULT -> SETTLED requires a recorded authoritative result.
Transitions are validated; illegal transitions raise LifecycleError.

Settlement-failure states (added for money-safety; see sweep_retry):
  SETTLED_PENDING marks a round whose result/settlement did not complete. The
  round is NOT closed and is re-driven by the retry sweep until it settles or
  exhausts its attempt budget (-> SETTLE_FAILED, which alerts). A round is
  never silently abandoned with an unsettled pot.
  The real pre-failure status is preserved on the round as `_settle_resume_from`
  because calculate_result() requires BETTING_CLOSED and settle() requires
  RESULT -- SETTLED_PENDING alone cannot say which step to re-run.
  CLOSED -> SETTLED_PENDING is the single exception to terminality: engine.settle()
  marks the round CLOSED before the service performs wallet credits, so a credit
  failure can leave a closed round partially paid. That is the only reason out of
  CLOSED, and the credit loop is idempotent (UNIQUE settlement.bet_id + wallet
  idempotency key), so re-entry is safe.
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
    SETTLED_PENDING = "SETTLED_PENDING"
    SETTLE_FAILED = "SETTLE_FAILED"


_ALLOWED = {
    RoundStatus.UPCOMING: {RoundStatus.BETTING_OPEN, RoundStatus.CLOSED},
    RoundStatus.BETTING_OPEN: {RoundStatus.BETTING_CLOSED, RoundStatus.CLOSED},
    RoundStatus.BETTING_CLOSED: {RoundStatus.RESULT_PROCESSING, RoundStatus.CLOSED,
                                RoundStatus.SETTLED_PENDING},
    RoundStatus.RESULT_PROCESSING: {RoundStatus.RESULT, RoundStatus.CLOSED,
                                     RoundStatus.SETTLED_PENDING},
    RoundStatus.RESULT: {RoundStatus.SETTLED, RoundStatus.CLOSED,
                         RoundStatus.SETTLED_PENDING},
    RoundStatus.SETTLED: {RoundStatus.CLOSED},
    # Only reachable when settlement did not complete; see module docstring.
    RoundStatus.CLOSED: {RoundStatus.SETTLED_PENDING},
    # Resume targets mirror the three points a settlement can fail at.
    RoundStatus.SETTLED_PENDING: {RoundStatus.BETTING_CLOSED, RoundStatus.RESULT,
                                  RoundStatus.CLOSED, RoundStatus.SETTLE_FAILED},
    # Operator-closable after the attempt budget is exhausted.
    RoundStatus.SETTLE_FAILED: {RoundStatus.CLOSED, RoundStatus.SETTLED_PENDING},
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


# --- settlement-failure helpers (pure; no engine knowledge) ---

SETTLE_MAX_ATTEMPTS = 5
SETTLE_BACKOFF_BASE_MS = 1000
SETTLE_BACKOFF_CAP_MS = 60_000


def settle_backoff_ms(attempt: int) -> int:
    """Exponential backoff for settlement retries. attempt is 1-based.

    1s, 2s, 4s, 8s, 16s ... capped at 60s. Total wall time to exhaustion
    across SETTLE_MAX_ATTEMPTS is bounded and short enough that a genuine
    outage is alerted on quickly, while not hot-looping on a persistent fault.
    """
    if attempt < 1:
        attempt = 1
    return min(SETTLE_BACKOFF_BASE_MS * (2 ** (attempt - 1)), SETTLE_BACKOFF_CAP_MS)


def needs_settlement_retry(status: RoundStatus) -> bool:
    """True when a round is awaiting settlement and must not be abandoned."""
    return status == RoundStatus.SETTLED_PENDING
