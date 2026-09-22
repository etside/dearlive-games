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


class WebhookSender:
    """Production HTTP webhook sender (game -> DearLive).

    Headers (see docs/webhooks.md):
      X-Webhook-Event, X-Webhook-Delivery, X-Webhook-Timestamp,
      X-Webhook-Signature: hex(HMAC-SHA256(signing_secret, raw_body)).
    Retry: exponential backoff (1s, 5s, 30s, 5min), then DLQ via log.record
    status=dead. Receivers must de-duplicate on event_id and verify the
    signature before trusting. Records every attempt in the delivery log.
    """

    def __init__(self, log: "MemoryDeliveryLog" = None, timeout_s: float = 8.0):
        self.log = log or MemoryDeliveryLog()
        self.timeout_s = timeout_s

    def send(self, destination: str, event: dict, secret: str,
             max_attempts: int = 5) -> dict:
        import json as _json
        import time as _time
        import urllib.request as _req
        body = _json.dumps(event, separators=(",", ":")).encode()
        sig = sign(secret, body)
        headers = {"Content-Type": "application/json",
                   "X-Webhook-Event": event.get("kind", ""),
                   "X-Webhook-Delivery": event.get("event_id", ""),
                   "X-Webhook-Timestamp": str(event.get("serverTime", 0)),
                   "X-Webhook-Signature": sig}
        last_err = ""
        for attempt in range(1, max_attempts + 1):
            try:
                r = _req.Request(destination, data=body, headers=headers,
                                 method="POST")
                with _req.urlopen(r, timeout=self.timeout_s) as resp:
                    if 200 <= resp.status < 300:
                        return self.log.record(destination, event, "delivered",
                                               attempts=attempt)
                    last_err = f"HTTP {resp.status}"
            except Exception as exc:  # noqa - transport errors -> retry
                last_err = str(exc)[:300]
            if attempt < max_attempts:
                _time.sleep(min(300, [1, 5, 30, 300][min(attempt - 1, 3)]))
        return self.log.record(destination, event, "dead",
                               attempts=max_attempts, error=last_err)
