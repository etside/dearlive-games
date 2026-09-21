"""API response envelope per BRD SRS 10.1.

{success, code, message, data, serverTime, requestId}
Server time is ALWAYS authoritative (client clocks untrusted).
"""
import time
import uuid


def ok(data=None, message="OK", code="OK", request_id=None):
    return {
        "success": True,
        "code": code,
        "message": message,
        "data": data,
        "serverTime": int(time.time() * 1000),
        "requestId": request_id or uuid.uuid4().hex,
    }


def err(message, code="BAD_REQUEST", data=None, request_id=None):
    return {
        "success": False,
        "code": code,
        "message": message,
        "data": data,
        "serverTime": int(time.time() * 1000),
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
