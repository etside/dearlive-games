"""The single response envelope.

SRS section 7 defines the shape:

    {success, code, message, data, serverTime, requestId}

`serverTime` is ISO8601 as the SRS specifies, so an operator reading a log line
or a webhook payload sees a date rather than needing to divide by 1000. That
change would break every consumer that wanted a number, so `serverTimeMs`
carries epoch milliseconds alongside it. Both are always present: a client
that guesses which one it is getting is a client that breaks on the next
change.
"""
import time
import uuid
from datetime import datetime, timezone


def now_iso(ms: int = None) -> str:
    """ISO8601 UTC, millisecond precision, e.g. 2026-09-26T12:00:00.123+00:00."""
    if ms is None:
        ms = int(time.time() * 1000)
    return (datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
            .isoformat(timespec="milliseconds"))


def now_ms() -> int:
    return int(time.time() * 1000)


def ok(data=None, message="OK", code="OK", request_id=None):
    return {
        "success": True,
        "code": code,
        "message": message,
        "data": data,
        "serverTime": now_iso(), "serverTimeMs": now_ms(),
        "requestId": request_id or uuid.uuid4().hex,
    }


def err(message, code="BAD_REQUEST", data=None, request_id=None):
    return {
        "success": False,
        "code": code,
        "message": message,
        "data": data,
        "serverTime": now_iso(), "serverTimeMs": now_ms(),
        "requestId": request_id or uuid.uuid4().hex,
    }


# Stable error codes (client must handle by code, never by message text).
E_AUTH = "UNAUTHENTICATED"
E_FORBIDDEN = "FORBIDDEN"
E_NOT_FOUND = "NOT_FOUND"
E_VALIDATION = "VALIDATION_ERROR"
E_WINDOW_CLOSED = "BETTING_CLOSED"
E_INSUFFICIENT = "INSUFFICIENT_BALANCE"
E_DUPLICATE = "DUPLICATE_REQUEST"
E_CONFLICT = "STATE_CONFLICT"
E_RATE_LIMIT = "RATE_LIMITED"
E_INTERNAL = "INTERNAL_ERROR"
E_TBC_BLOCKED = "TBC_RULE_UNCONFIRMED"  # real-money action on unconfirmed config
