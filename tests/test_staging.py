"""Staging serverless stack tests (deployability: Vercel adapter).

Every HTTP call goes through staging/wsgi.app with FRESH service instances
(like separate serverless invocations). Persistence across calls proves
Redis-backed rooms/wallet/sessions/idempotency — nothing rides on process
memory. Requires local Redis (CI: redis-server) via REDIS_HOST/PORT env.
"""
import io
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("REDIS_HOST", "127.0.0.1")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ["APP_ENV"] = "staging"

from tests.test_asset_lifecycle import CountedCase  # noqa: E402


def wsgi_call(method, path, body=None, headers=None, query=""):
    from staging import wsgi as W
    data = json.dumps(body).encode() if body is not None else None
    env = {"REQUEST_METHOD": method, "PATH_INFO": path,
           "QUERY_STRING": query, "CONTENT_LENGTH": str(len(data or b"")),
           "CONTENT_TYPE": "application/json",
           "wsgi.input": io.BytesIO(data or b""), "wsgi.errors": io.StringIO()}
    for k, v in (headers or {}).items():
        env["HTTP_" + k.upper().replace("-", "_")] = v
    captured = {}

    def start_response(status, rheaders):
        captured["status"] = status
        captured["headers"] = dict(rheaders)

    payload = b"".join(W.app(env, start_response))
    return int(captured["status"].split()[0]), json.loads(payload or b"null")


class StagingStackTest(CountedCase):
    @classmethod
    def setUpClass(cls):
        from integrations.redis_store import MinimalRedis
        r = MinimalRedis()
        for key in r.command("KEYS", "stg:*") or []:
            r.command("DEL", key)
        for key in r.command("KEYS", "dearlive:*") or []:
            r.command("DEL", key)

    def test_login_funds_and_full_flow_across_invocations(self):
        st, body = wsgi_call("POST", "/api/v1/staging/test-login",
                             {"player": "stg-p1", "room": "stg-room",
                              "game": "teen-patti-pro"})
        self.counted(st == 200, f"test-login 200: {body}")
        tok = body["data"]["launch_token"]
        st, body = wsgi_call("POST", "/api/v1/sessions", {"launch_token": tok})
        self.counted(st == 200, "session opened")
        sid = body["data"]["session_id"]
        auth = {"Authorization": f"Bearer {sid}"}
        st, body = wsgi_call("GET", "/api/v1/games/teen-patti-pro/rounds/current?room=stg-room"
                             if False else "/api/v1/games/teen-patti-pro/rounds/current",
                             headers=auth, query="room=stg-room")
        # wallet balance comes from the rooms/wallet view
        st, body = wsgi_call("GET", "/api/v1/games/teen-patti-pro/rooms/stg-room/wallet",
                             headers=auth)
        self.counted(st == 200 and body["data"]["available"] == 20000, f"funded 20000: {body}")
        st, _ = wsgi_call("POST", "/api/v1/games/teen-patti-pro/rooms/stg-room/rounds/start",
                           {}, {"X-Admin-Key": "dev-admin-key"})
        self.counted(st == 200, "round started")
        st, body = wsgi_call("POST", "/api/v1/games/teen-patti-pro/rooms/stg-room/bets",
                             {"position": "A", "amount": 100},
                             {"Idempotency-Key": "stg-k1", **auth})
        self.counted(st == 200, f"bet accepted: {body}")
        st, body = wsgi_call("GET", "/api/v1/games/teen-patti-pro/rooms/stg-room/wallet",
                             headers=auth)
        self.counted(body["data"]["available"] == 19900, "debited across invocations")
        for op in ("close", "result", "settle"):
            st, _ = wsgi_call("POST", f"/api/v1/games/teen-patti-pro/rooms/stg-room/rounds/{op}",
                               {}, {"X-Admin-Key": "dev-admin-key"})
            self.counted(st == 200, f"{op} ok")
        st, body = wsgi_call("GET", "/api/v1/games/teen-patti-pro/history",
                             headers=auth, query="room=stg-room")
        bets = body["data"]["bets"] if st == 200 else []
        self.counted(len(bets) == 1 and bets[0]["status"] in ("won", "lost"),
                     f"settled history: {bets}")

    def test_faucet_idempotent_and_prod_refused(self):
        st, a = wsgi_call("POST", "/api/v1/staging/test-wallet/grant",
                           {"player": "stg-p2", "amount": 5000,
                            "idempotency_key": "faucet-1"})
        self.counted(st == 200, f"grant ok: {a}")
        st, b = wsgi_call("POST", "/api/v1/staging/test-wallet/grant",
                           {"player": "stg-p2", "amount": 5000,
                            "idempotency_key": "faucet-1"})
        self.counted(st == 200 and b["data"]["txn_id"] == a["data"]["txn_id"],
                     "faucet replay returns same txn")
        self.counted(b["data"]["available"] == 5000, "credited exactly once")

    def test_staging_refuses_production(self):
        os.environ["APP_ENV"] = "production"
        try:
            st, _ = wsgi_call("POST", "/api/v1/staging/test-login", {"player": "x"})
            self.counted(st in (403, 503), f"prod refused: {st}")
        finally:
            os.environ["APP_ENV"] = "staging"


if __name__ == "__main__":
    unittest.main()
