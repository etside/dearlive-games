"""HMAC-signed webhook envelope + in-memory delivery log.

Production: swap MemoryDeliveryLog for a queued sender with retry/backoff/DLQ.
Signature: hex(HMAC-SHA256(secret, raw_body)). Receivers must verify before
trusting. Secret rotation: support (key_id, secret) pairs.
"""
import hashlib
import hmac
import json
import time
import uuid
from typing import Any, Dict, List


EVENTS = ("game.session.created", "player.joined", "player.left",
          "round.started", "bet.accepted", "bet.rejected", "betting.closed",
          "result.published", "settlement.completed", "session.completed",
          "round.cancelled", "error")


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(secret: str, body: bytes, signature: str) -> bool:
    return hmac.compare_digest(sign(secret, body), signature)


def build_event(kind: str, data: Dict[str, Any]) -> dict:
    assert kind in EVENTS, f"unknown webhook event {kind}"
    return {"event_id": uuid.uuid4().hex, "kind": kind, "data": data,
            "serverTime": int(time.time() * 1000)}


class MemoryDeliveryLog:
    def __init__(self):
        self.deliveries: List[dict] = []

    def record(self, destination: str, event: dict, status: str,
               attempts: int = 1, error: str = "") -> dict:
        rec = {"destination": destination, "event_id": event["event_id"],
               "kind": event["kind"], "status": status, "attempts": attempts,
               "error": error, "at": int(time.time() * 1000)}
        self.deliveries.append(rec)
        return rec

    def pending(self) -> List[dict]:
        return [d for d in self.deliveries if d["status"] not in ("delivered",)]
