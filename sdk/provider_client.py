"""Teen Patti Pro provider client (Python, stdlib only).

Reference operator integration: one API key, signed requests, idempotent
money operations. Every method returns the `data` object or raises
ProviderError with the API error code.

    from sdk.provider_client import TeenPattiProviderClient

    client = TeenPattiProviderClient("https://api.example.com",
                                    api_key="tp_live_xxx",
                                    api_secret="...")
    session = client.create_session("player_10025")
    print(session["launch_url"])
"""
import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.request


class ProviderError(RuntimeError):
    def __init__(self, code, message, status=0):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status


class TeenPattiProviderClient:
    def __init__(self, base_url, api_key, api_secret, timeout=15,
                 game_code="teen_patti_pro", currency="COIN"):
        if not base_url or not api_key or not api_secret:
            raise ValueError("base_url, api_key and api_secret are required")
        self.base = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout = timeout
        self.game_code = game_code
        self.currency = currency

    def canonical(self, method, path, timestamp, nonce, body):
        return "\n".join([method.upper(), path, str(timestamp), nonce,
                          hashlib.sha256(body or b"").hexdigest()]).encode()

    def sign(self, method, path, timestamp, nonce, body):
        return hmac.new(self.api_secret.encode(),
                        self.canonical(method, path, timestamp, nonce, body),
                        hashlib.sha256).hexdigest()

    def call(self, method, path, body=None, idempotency_key=None, query=""):
        payload = json.dumps(body).encode() if body is not None else b""
        timestamp = int(time.time())
        nonce = secrets.token_hex(8)
        sign_path = path.split("?", 1)[0]
        headers = {"Accept": "application/json", "X-API-Key": self.api_key,
                   "X-Timestamp": str(timestamp), "X-Nonce": nonce,
                   "X-Signature": self.sign(method, sign_path, timestamp, nonce,
                                             payload)}
        if payload:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        url = self.base + path + (("?" + query) if query else "")
        request = urllib.request.Request(url, data=payload or None,
                                         method=method.upper(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode() or "{}"
            try:
                data = json.loads(raw)
            except ValueError:
                raise ProviderError("HTTP_ERROR", raw, exc.code)
            raise ProviderError(data.get("code", "HTTP_ERROR"),
                                data.get("message", ""), exc.code)
        if not data.get("success"):
            raise ProviderError(data.get("code", "ERROR"),
                                data.get("message", ""))
        return data.get("data")

    # provider meta
    def health(self):
        request = urllib.request.Request(self.base + "/api/v1/provider/health")
        with urllib.request.urlopen(request, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode())

    def games(self):
        return self.call("GET", "/api/v1/games")

    # sessions
    def create_session(self, player_id, language="en", platform="web",
                       return_url="", table_id="", amount=None):
        body = {"player_id": player_id, "game_code": self.game_code,
                "currency": self.currency, "language": language,
                "platform": platform, "return_url": return_url}
        if table_id:
            body["table_id"] = table_id
        if amount is not None:
            body["amount"] = amount
        return self.call("POST", "/api/v1/sessions", body)

    def session(self, session_id):
        return self.call("GET", f"/api/v1/sessions/{session_id}")

    def end_session(self, session_id):
        return self.call("DELETE", f"/api/v1/sessions/{session_id}")

    # tables
    def tables(self):
        return self.call("GET", "/api/v1/teen-patti/tables")["tables"]

    def table(self, table_id):
        return self.call("GET", f"/api/v1/teen-patti/tables/{table_id}")

    def join(self, table_id, player_id="", session_id="", session_token="",
             amount=None):
        body = {}
        if player_id:
            body["player_id"] = player_id
        if session_id:
            body["session_id"] = session_id
        if session_token:
            body["session_token"] = session_token
        if amount is not None:
            body["amount"] = amount
        return self.call("POST", f"/api/v1/teen-patti/tables/{table_id}/join", body)

    def leave(self, table_id, player_id="", session_token=""):
        body = {"player_id": player_id, "session_token": session_token}
        return self.call("POST", f"/api/v1/teen-patti/tables/{table_id}/leave", body)

    def state(self, table_id, player_id="", session_token=""):
        query = f"player_id={player_id}" if player_id else ""
        path = f"/api/v1/teen-patti/tables/{table_id}/state"
        if session_token:
            return self.call("GET", path, query=f"player_id={session_token}")
        return self.call("GET", path, query=query)

    def history(self, table_id, limit=50):
        return self.call("GET", f"/api/v1/teen-patti/tables/{table_id}/history",
                         query=f"limit={limit}")

    def bet(self, table_id, position, amount, player_id="", session_id="",
            session_token="", idempotency_key=None):
        body = {"action": "bet", "position": position, "amount": amount,
                "player_id": player_id, "session_id": session_id,
                "session_token": session_token}
        body = {k: v for k, v in body.items() if v}
        return self.call("POST", f"/api/v1/teen-patti/tables/{table_id}/action",
                         body, idempotency_key=idempotency_key or secrets.token_hex(8))

    # wallet
    def balance(self, player_id):
        return self.call("GET", f"/api/v1/players/{player_id}/balance")

    def debit(self, player_id, amount, reference, round_id="",
              idempotency_key=None):
        return self.call("POST", "/api/v1/wallet/debit",
                         {"player_id": player_id, "amount": amount,
                          "currency": self.currency, "game_code": self.game_code,
                          "round_id": round_id, "reference": reference},
                         idempotency_key=idempotency_key or reference)

    def credit(self, player_id, amount, reference, round_id="",
               idempotency_key=None):
        return self.call("POST", "/api/v1/wallet/credit",
                         {"player_id": player_id, "amount": amount,
                          "currency": self.currency, "game_code": self.game_code,
                          "round_id": round_id, "reference": reference},
                         idempotency_key=idempotency_key or reference)

    def rollback(self, player_id, original_reference, reason,
                 idempotency_key=None):
        key = idempotency_key or f"{original_reference}:rollback"
        return self.call("POST", "/api/v1/wallet/rollback",
                         {"player_id": player_id,
                          "original_reference": original_reference,
                          "reason": reason}, idempotency_key=key)

    def transactions(self, player_id, limit=50):
        return self.call("GET", f"/api/v1/wallet/transactions/{player_id}",
                         query=f"limit={limit}")
