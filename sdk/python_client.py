"""DearLive Games SDK (Python) — configurable integration client.

The DearLive developer sets api_base to their own GAMES_BASE_URL.
Proves: launch → session → balance → join/state → bet → result →
settle(admin) → wallet → reconnect. Idempotency keys are caller-supplied.
"""
import json
import urllib.request
import uuid


class DearLiveGameClient:
    def __init__(self, api_base, session_id="", room="default", admin_key=""):
        if not api_base:
            raise ValueError("api_base (GAMES_BASE_URL) required")
        self.api = api_base.rstrip("/")
        self.session = session_id
        self.room = room
        self.admin_key = admin_key

    def _call(self, method, path, body=None, extra_headers=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json"}
        if self.session:
            headers["Authorization"] = f"Bearer {self.session}"
        if self.admin_key and "/rounds/" in path:
            headers["X-Admin-Key"] = self.admin_key
        headers.update(extra_headers or {})
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.api + path, data=data,
                                     method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode())
        if not payload.get("success"):
            raise RuntimeError(f"{payload.get('code')}: {payload.get('message')}")
        return payload["data"]

    # player
    def open_session(self, launch_token):
        d = self._call("POST", "/api/v1/sessions", {"launch_token": launch_token})
        self.session = d["session_id"]
        self.room = d.get("room_id", self.room)
        return d

    def state(self):
        return self._call("GET", f"/api/v1/games/teen-patti-pro/rounds/current?room={self.room}")

    def place_bet(self, position, amount, key=None):
        return self._call("POST", f"/api/v1/games/teen-patti-pro/rooms/{self.room}/bets",
                          {"position": position, "amount": amount},
                          {"Idempotency-Key": key or uuid.uuid4().hex})

    def wallet(self):
        return self._call("GET", f"/api/v1/games/teen-patti-pro/rooms/{self.room}/wallet")

    def history(self):
        return self._call("GET", f"/api/v1/games/teen-patti-pro/history?room={self.room}")

    def reconnect(self, last_seen_seq=0):
        return self._call("POST", f"/api/v1/games/teen-patti-pro/rooms/{self.room}/reconnect",
                          {"session_id": self.session, "last_seen_seq": last_seen_seq})

    # operator
    def round_start(self):
        return self._call("POST", f"/api/v1/games/teen-patti-pro/rooms/{self.room}/rounds/start")

    def round_close(self):
        return self._call("POST", f"/api/v1/games/teen-patti-pro/rooms/{self.room}/rounds/close")

    def round_result(self):
        return self._call("POST", f"/api/v1/games/teen-patti-pro/rooms/{self.room}/rounds/result")

    def round_settle(self):
        return self._call("POST", f"/api/v1/games/teen-patti-pro/rooms/{self.room}/rounds/settle")
