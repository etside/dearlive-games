"""Production DearLive adapters (HTTP + documented contract).

DearLive REMAINS the authoritative owner of users and wallet balances.
The game server NEVER stores a production money balance; every financial
read/write goes through DearLive's wallet API via this adapter.

The DearLive developer configures endpoints/credentials/currency via env
(see common/config.py + .env.example). Game logic is untouched.

Expected DearLive wallet endpoints (developer maps these 1:1; path names
are defaults the developer may override via WALLET_*_PATH env):
  GET_BALANCE            GET  {base}/wallets/{player_id}/balance
  DEBIT_BET              POST {base}/wallets/debit   {player_id, amount, ref, idempotency_key, currency}
  CREDIT_WIN             POST {base}/wallets/credit  {player_id, amount, ref, idempotency_key, currency}
  REFUND                 POST {base}/wallets/refund  {player_id, amount, ref, idempotency_key, currency}
  GET_TRANSACTION_STATUS GET  {base}/wallets/txn/{idempotency_key}

Timeout rule (strict): on timeout/transport error AFTER a debit/credit was
sent, the adapter MUST call GET_TRANSACTION_STATUS before retrying, so a
committed upstream txn is never double-applied. All amounts are integers
(minor coin units); currency is env-configured (default COIN).

Local dev/tests keep using integrations/dearlive_mock.py — this module is
only constructed when DEARLIVE_API_BASE_URL / WALLET_BASE_URL is set.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from common.wallet import (Balance, InsufficientBalance, TxnRef, WalletAdapter,
                           WalletError)


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


class DearLiveApiError(WalletError):
    def __init__(self, message: str, status: int = 0, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code


class _Http:
    """Minimal stdlib HTTP helper with auth headers + timeout."""

    def __init__(self, base_url: str, api_key: str = "", client_id: str = "",
                 client_secret: str = "", auth_type: str = "", timeout_ms: int = 8000):
        self.base = base_url.rstrip("/")
        self.api_key = api_key
        self.client_id = client_id
        self.client_secret = client_secret
        self.auth_type = (auth_type or "Bearer").strip()
        self.timeout = timeout_ms / 1000.0

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["X-Api-Key"] = self.api_key
        if self.client_id:
            h["X-Client-Id"] = self.client_id
        # Secret is never logged; sent only as configured by DearLive.
        if self.client_secret and self.auth_type.lower() == "bearer":
            h["Authorization"] = f"Bearer {self.client_secret}"
        return h

    def call(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        url = self.base + path
        data = json.dumps(body or {}).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method.upper(),
                                     headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8") or "{}"
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8") or "{}")
            except Exception:
                payload = {}
            msg = str(payload.get("message") or payload.get("error") or exc)
            raise DearLiveApiError(msg, status=exc.code,
                                   code=str(payload.get("code", "")))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise WalletError(f"wallet transport error: {exc}")


class HttpDearLiveWallet(WalletAdapter):
    """WalletAdapter over DearLive's real wallet HTTP API.

    Idempotency: every debit/credit/refund carries the caller-supplied
    idempotency_key; the server treats (ref, key) as UNIQUE. Replays return
    the original txn without moving money.
    """

    def __init__(self, base_url: str = "", api_key: str = "", client_id: str = "",
                 client_secret: str = "", auth_type: str = "Bearer",
                 currency: str = "COIN", timeout_ms: int = 8000,
                 balance_path: str = "/wallets/{player_id}/balance",
                 debit_path: str = "/wallets/debit",
                 credit_path: str = "/wallets/credit",
                 refund_path: str = "/wallets/refund",
                 txn_status_path: str = "/wallets/txn/{key}"):
        base_url = base_url or _env("WALLET_BASE_URL", _env("DEARLIVE_API_BASE_URL", ""))
        if not base_url:
            raise WalletError("WALLET_BASE_URL (or DEARLIVE_API_BASE_URL) is not configured")
        self.http = _Http(base_url, api_key or _env("WALLET_API_KEY"),
                          client_id or _env("WALLET_CLIENT_ID"),
                          client_secret or _env("WALLET_CLIENT_SECRET"),
                          auth_type or _env("DEARLIVE_AUTH_TYPE", "Bearer"),
                          timeout_ms)
        self.currency = currency or _env("COIN_CURRENCY", _env("WALLET_CURRENCY", "COIN"))
        self.paths = {"balance": balance_path, "debit": debit_path,
                      "credit": credit_path, "refund": refund_path,
                      "txn": txn_status_path}

    # -- reads --
    def get_balance(self, player_id: str) -> Balance:
        path = self.paths["balance"].format(
            player_id=urllib.parse.quote(player_id, safe=""))
        try:
            res = self.http.call("GET", path)
        except DearLiveApiError as exc:
            raise WalletError(f"get_balance failed: {exc}")
        data = res.get("data", res)
        try:
            available = int(data["available"])
        except (KeyError, TypeError, ValueError):
            raise WalletError("get_balance: upstream missing integer 'available'")
        currency = str(data.get("currency", self.currency))
        return Balance(player_id, available, currency)

    # -- writes --
    def _write(self, kind: str, player_id: str, amount: int, ref: str,
               idempotency_key: str) -> TxnRef:
        body = {"player_id": player_id, "amount": int(amount), "ref": ref,
                "idempotency_key": idempotency_key, "currency": self.currency}
        try:
            res = self.http.call("POST", self.paths[kind], body)
        except DearLiveApiError as exc:
            if exc.status in (402, 409, 422) or "INSUFFICIENT" in exc.code.upper():
                raise InsufficientBalance(f"{kind}: {exc}")
            # Timeout/5xx AFTER send: caller must check txn status before retry.
            raise WalletError(f"{kind} failed (check txn status before retry): {exc}")
        data = res.get("data", res)
        txn_id = str(data.get("txn_id") or data.get("transaction_id") or ref)
        return TxnRef(txn_id, idempotency_key)

    def debit(self, player_id: str, amount: int, ref: str,
              idempotency_key: str) -> TxnRef:
        if amount <= 0:
            raise WalletError("amount must be positive")
        return self._write("debit", player_id, amount, ref, idempotency_key)

    def credit(self, player_id: str, amount: int, ref: str,
               idempotency_key: str) -> TxnRef:
        if amount < 0:
            raise WalletError("amount must be non-negative")
        return self._write("credit", player_id, amount, ref, idempotency_key)

    def void_debit(self, player_id: str, ref: str,
                   idempotency_key: str) -> Optional[TxnRef]:
        # Compensating path per contract: REFUND of the original stake amount
        # is issued by the service layer via credit(); this records intent.
        body = {"player_id": player_id, "ref": ref,
                "idempotency_key": idempotency_key, "currency": self.currency}
        try:
            res = self.http.call("POST", self.paths["refund"], body)
        except DearLiveApiError as exc:
            raise WalletError(f"refund failed: {exc}")
        data = res.get("data", res)
        txn_id = str(data.get("txn_id") or data.get("transaction_id") or ref)
        return TxnRef(txn_id, idempotency_key)

    def transaction_status(self, idempotency_key: str) -> dict:
        """GET_TRANSACTION_STATUS — mandatory before retry after timeout."""
        path = self.paths["txn"].format(key=urllib.parse.quote(idempotency_key, safe=""))
        try:
            res = self.http.call("GET", path)
        except DearLiveApiError as exc:
            raise WalletError(f"transaction_status failed: {exc}")
        return res.get("data", res)


class DearLiveIdentityClient:
    """Player/room identity reads owned by DearLive (game caches nothing authoritative).

    Endpoints (developer-mapped, override via *\_PATH env if needed):
      PLAYER_PROFILE GET {base}/players/{id}
      PLAYER_STATUS  GET {base}/players/{id}/status   -> {status: active|banned|suspended}
      PLAYER_BALANCE GET {base}/players/{id}/balance  -> {available: int, currency}
    """

    def __init__(self, base_url: str = "", api_key: str = "", client_id: str = "",
                 client_secret: str = "", auth_type: str = "Bearer",
                 timeout_ms: int = 8000):
        base_url = base_url or _env("DEARLIVE_API_BASE_URL", "")
        if not base_url:
            raise DearLiveApiError("DEARLIVE_API_BASE_URL is not configured", code="CONFIG")
        self.http = _Http(base_url, api_key or _env("DEARLIVE_API_KEY"),
                          client_id or _env("DEARLIVE_CLIENT_ID"),
                          client_secret or _env("DEARLIVE_CLIENT_SECRET"),
                          auth_type or _env("DEARLIVE_AUTH_TYPE", "Bearer"),
                          timeout_ms or int(_env("WALLET_TIMEOUT_MS", "8000")))

    def player_status(self, player_id: str) -> str:
        res = self.http.call("GET", f"/players/{urllib.parse.quote(player_id, safe='')}/status")
        return str(res.get("data", res).get("status", "active"))

    def player_profile(self, player_id: str) -> dict:
        res = self.http.call("GET", f"/players/{urllib.parse.quote(player_id, safe='')}")
        return res.get("data", res)

    def assert_playable(self, player_id: str) -> None:
        if self.player_status(player_id).lower() in ("banned", "suspended", "blocked"):
            from common.session import TokenError
            raise TokenError(f"player {player_id} not playable (PLAYER_STATUS)")


def build_wallet_from_env() -> WalletAdapter:
    """Factory: real HTTP wallet when WALLET/DEARLIVE base URL is set,
    else local mock wallet (sandbox/dev only — never production)."""
    if _env("WALLET_BASE_URL", _env("DEARLIVE_API_BASE_URL", "")):
        return HttpDearLiveWallet()
    from integrations.dearlive_mock import MockDearLiveWallet
    return MockDearLiveWallet()
