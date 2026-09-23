"""Provider wallet: idempotent financial operations over the DearLive adapter
plus an immutable transaction ledger.

Money of record stays with the operator. Every call here is forwarded to the
configured WalletAdapter (production: HttpDearLiveWallet -> DearLive) and each
committed transaction appends one ledger row that is never updated or deleted.
Rollback never rewrites history: it appends a compensating credit bound to the
original reference and is itself idempotent.
"""
import json
import threading
import time
import uuid
from typing import Optional, Tuple

from common import envelope as E
from common.wallet import Balance, InsufficientBalance, TxnRef, WalletError

IDEMPOTENCY_PREFIX = "pvdr:idem:"
LEDGER_PREFIX = "pvdr:ledger:"
TXN_PREFIX = "pvdr:txn:"
MAX_AMOUNT = 10 ** 12
MAX_REFERENCE_LEN = 128
MAX_IDEMPOTENCY_LEN = 128

TYPE_DEBIT = "debit"
TYPE_CREDIT = "credit"
TYPE_ROLLBACK = "rollback"
STATUS_SUCCESS = "SUCCESS"
STATUS_ROLLED_BACK = "ROLLED_BACK"


class WalletError_(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code = code
        self.status = status


class ResultStore:
    def claim(self, key: str, ttl_s: int) -> Tuple[bool, Optional[dict]]:
        """(freshly_claimed, replay_result). replay_result None + fresh False
        means another request currently holds the key."""
        raise NotImplementedError

    def complete(self, key: str, result: dict, ttl_s: int) -> None:
        raise NotImplementedError

    def get(self, key: str) -> Optional[dict]:
        raise NotImplementedError


class MemoryResultStore(ResultStore):
    def __init__(self):
        self._entries: dict = {}
        self._lock = threading.Lock()

    def claim(self, key, ttl_s):
        with self._lock:
            now = time.time()
            ent = self._entries.get(key)
            if ent is not None and ent["expires"] > now:
                return False, ent.get("result")
            self._entries[key] = {"result": None, "expires": now + ttl_s}
            return True, None

    def complete(self, key, result, ttl_s):
        with self._lock:
            self._entries[key] = {"result": result,
                                  "expires": time.time() + ttl_s}

    def get(self, key):
        with self._lock:
            ent = self._entries.get(key)
            if ent is None or ent["expires"] <= time.time():
                return None
            return ent.get("result")


class RedisResultStore(ResultStore):
    def __init__(self, redis, prefix: str = IDEMPOTENCY_PREFIX):
        self.r = redis
        self.prefix = prefix

    def claim(self, key, ttl_s):
        full = self.prefix + key
        if self.r.command("SET", full, '{"state":"claimed"}', "NX", "EX", str(int(ttl_s))) == "OK":
            return True, None
        raw = self.r.command("GET", full)
        if raw is None:
            return True, None
        data = json.loads(raw)
        return False, data if data.get("state") == "complete" else None

    def complete(self, key, result, ttl_s):
        body = dict(result)
        body["state"] = "complete"
        self.r.command("SET", self.prefix + key, json.dumps(body),
                       "EX", str(int(ttl_s)))

    def get(self, key):
        raw = self.r.command("GET", self.prefix + key)
        if raw is None:
            return None
        data = json.loads(raw)
        return data if data.get("state") == "complete" else None


class Ledger:
    def append(self, entry: dict) -> dict:
        raise NotImplementedError

    def find_by_reference(self, reference: str) -> Optional[dict]:
        raise NotImplementedError

    def find_by_idempotency_key(self, key: str) -> Optional[dict]:
        raise NotImplementedError

    def list_player(self, player_id: str, limit: int = 50) -> list:
        raise NotImplementedError


class MemoryLedger(Ledger):
    def __init__(self):
        self._rows = []
        self._lock = threading.Lock()

    def append(self, entry):
        with self._lock:
            self._rows.append(entry)
        return entry

    def find_by_reference(self, reference):
        for row in reversed(self._rows):
            if row.get("reference") == reference:
                return row
        return None

    def find_by_idempotency_key(self, key):
        for row in reversed(self._rows):
            if row.get("idempotency_key") == key:
                return row
        return None

    def list_player(self, player_id, limit=50):
        rows = [r for r in self._rows if r.get("player_id") == player_id]
        return list(reversed(rows))[:max(1, int(limit))]


class RedisLedger(Ledger):
    """Append-only per-player list plus a reference index.

    Rows are never rewritten or removed; rollback is a new compensating row.
    """

    def __init__(self, redis, cap: int = 0):
        self.r = redis
        self.cap = int(cap)

    def append(self, entry):
        body = json.dumps(entry)
        self.r.command("RPUSH", LEDGER_PREFIX + entry["player_id"], body)
        self.r.command("SET", TXN_PREFIX + entry["txn_id"], body)
        self.r.command("SET", "pvdr:ref:" + entry["reference"], entry["txn_id"])
        self.r.command("SET", "pvdr:idemref:" + entry["idempotency_key"],
                       entry["txn_id"])
        if self.cap > 0:
            self.r.command("LTRIM", LEDGER_PREFIX + entry["player_id"],
                           "0", str(self.cap - 1))
        return entry

    def _row(self, txn_id):
        if not txn_id:
            return None
        raw = self.r.command("GET", TXN_PREFIX + txn_id)
        return json.loads(raw) if raw else None

    def find_by_reference(self, reference):
        return self._row(self.r.command("GET", "pvdr:ref:" + reference))

    def find_by_idempotency_key(self, key):
        return self._row(self.r.command("GET", "pvdr:idemref:" + key))

    def list_player(self, player_id, limit=50):
        raw = self.r.command("LRANGE", LEDGER_PREFIX + player_id,
                             str(-max(1, int(limit))), "-1")
        rows = []
        for item in reversed(raw or []):
            try:
                rows.append(json.loads(item))
            except ValueError:
                continue
        return rows


def _text(value, field, max_len, required=True, pattern=None):
    text = str(value or "").strip()
    if required and not text:
        raise WalletError_(E.E_VALIDATION, f"{field} is required")
    if len(text) > max_len:
        raise WalletError_(E.E_VALIDATION, f"{field} exceeds {max_len} characters")
    if pattern and not pattern.match(text):
        raise WalletError_(E.E_VALIDATION, f"{field} has an unsupported format")
    return text


class ProviderWallet:
    """Server-side wallet facade exposed at /api/v1/wallet/*."""

    def __init__(self, adapter, ledger: Ledger, results: ResultStore,
                 currency: str = "COIN", idempotency_ttl_s: int = 86400,
                 audit=None):
        self.adapter = adapter
        self.ledger = ledger
        self.results = results
        self.currency = (currency or "COIN").upper()
        self.idempotency_ttl_s = int(idempotency_ttl_s)
        self.audit = audit

    # -- validation --
    def _player(self, body):
        return _text(body.get("player_id"), "player_id", 128)

    def _amount(self, body, allow_zero=False):
        raw = body.get("amount")
        if isinstance(raw, bool) or raw is None:
            raise WalletError_(E.E_VALIDATION, "amount is required")
        try:
            amount = int(raw)
        except (TypeError, ValueError):
            raise WalletError_(E.E_VALIDATION, "amount must be an integer")
        if amount < 0 or (amount == 0 and not allow_zero):
            raise WalletError_(E.E_VALIDATION, "amount must be positive")
        if amount > MAX_AMOUNT:
            raise WalletError_(E.E_VALIDATION, "amount is out of range")
        return amount

    def _currency(self, body):
        currency = str(body.get("currency") or self.currency).strip().upper()
        if currency != self.currency:
            raise WalletError_(E.E_VALIDATION,
                               f"currency must be {self.currency}")
        return currency

    def _reference(self, body):
        return _text(body.get("reference"), "reference", MAX_REFERENCE_LEN)

    def _idempotency(self, header_key, body):
        raw = header_key or body.get("idempotency_key") or ""
        return _text(raw, "Idempotency-Key", MAX_IDEMPOTENCY_LEN)

    def _record(self, actor, action, entity, entity_id, after=None):
        if self.audit is not None:
            self.audit.record(actor, action, entity, entity_id, after=after or {})

    def _entry(self, kind, player_id, amount, currency, reference,
               idempotency_key, game_code, round_id, actor, txn_id=None,
               original_reference="", reason=""):
        return {
            "txn_id": txn_id or ("ptxn-" + uuid.uuid4().hex),
            "type": kind,
            "status": STATUS_SUCCESS,
            "player_id": player_id,
            "amount": int(amount),
            "currency": currency,
            "reference": reference,
            "idempotency_key": idempotency_key,
            "game_code": game_code or "",
            "round_id": round_id or "",
            "original_reference": original_reference,
            "reason": reason,
            "actor": actor or "provider",
            "created_at": int(time.time() * 1000),
        }

    def _replay(self, key, payload_fingerprint, apply_fn):
        fresh, stored = self.results.claim(key, self.idempotency_ttl_s)
        if not fresh:
            if stored is None:
                raise WalletError_(E.E_DUPLICATE,
                                   "a request with this Idempotency-Key is in flight",
                                   409)
            if stored.get("fingerprint") != payload_fingerprint:
                raise WalletError_(
                    E.E_DUPLICATE,
                    "Idempotency-Key was already used with a different payload", 409)
            stored = dict(stored)
            stored["replayed"] = True
            return stored
        return None

    def _finish(self, key, fingerprint, entry):
        body = dict(entry)
        body["fingerprint"] = fingerprint
        body["replayed"] = False
        self.results.complete(key, body, self.idempotency_ttl_s)
        return body

    def _fingerprint(self, payload):
        import hashlib
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    # -- operations --
    def get_balance(self, player_id: str) -> Balance:
        player_id = _text(player_id, "player_id", 128)
        try:
            return self.adapter.get_balance(player_id)
        except WalletError:
            raise
        except Exception as exc:
            raise WalletError_(E.E_INTERNAL, f"balance lookup failed: {exc}", 502)

    def debit(self, body, header_key="", actor="provider") -> dict:
        player_id = self._player(body)
        amount = self._amount(body)
        currency = self._currency(body)
        reference = self._reference(body)
        key = self._idempotency(header_key, body)
        game_code = _text(body.get("game_code"), "game_code", 64, required=False)
        round_id = _text(body.get("round_id"), "round_id", 128, required=False)
        fingerprint = self._fingerprint({"op": TYPE_DEBIT, "player": player_id,
                                        "amount": amount, "ref": reference,
                                        "currency": currency})
        store_key = key
        replay = self._replay(key, fingerprint, None)
        if replay is not None:
            return replay
        try:
            txn = self.adapter.debit(player_id, amount, ref=reference,
                                     idempotency_key=key)
        except InsufficientBalance:
            raise WalletError_(E.E_INSUFFICIENT, "insufficient balance", 402)
        except WalletError as exc:
            raise WalletError_(E.E_INTERNAL, f"debit failed: {exc}", 502)
        entry = self._entry(TYPE_DEBIT, player_id, amount, currency, reference,
                            key, game_code, round_id, actor,
                            txn_id=getattr(txn, "txn_id", None))
        self.ledger.append(entry)
        self._record(actor, "wallet.debit", "transaction", entry["txn_id"],
                     after=entry)
        return self._finish(key, fingerprint, entry)

    def credit(self, body, header_key="", actor="provider") -> dict:
        player_id = self._player(body)
        amount = self._amount(body)
        currency = self._currency(body)
        reference = self._reference(body)
        key = self._idempotency(header_key, body)
        game_code = _text(body.get("game_code"), "game_code", 64, required=False)
        round_id = _text(body.get("round_id"), "round_id", 128, required=False)
        fingerprint = self._fingerprint({"op": TYPE_CREDIT, "player": player_id,
                                        "amount": amount, "ref": reference,
                                        "currency": currency})
        store_key = key
        replay = self._replay(key, fingerprint, None)
        if replay is not None:
            return replay
        try:
            txn = self.adapter.credit(player_id, amount, ref=reference,
                                      idempotency_key=key)
        except InsufficientBalance:
            raise WalletError_(E.E_INSUFFICIENT, "insufficient balance", 402)
        except WalletError as exc:
            raise WalletError_(E.E_INTERNAL, f"credit failed: {exc}", 502)
        entry = self._entry(TYPE_CREDIT, player_id, amount, currency, reference,
                            key, game_code, round_id, actor,
                            txn_id=getattr(txn, "txn_id", None))
        self.ledger.append(entry)
        self._record(actor, "wallet.credit", "transaction", entry["txn_id"],
                     after=entry)
        return self._finish(key, fingerprint, entry)

    def rollback(self, body, header_key="", actor="provider") -> dict:
        player_id = self._player(body)
        original = _text(body.get("original_reference"), "original_reference",
                         MAX_REFERENCE_LEN)
        reason = _text(body.get("reason"), "reason", 128)
        key = self._idempotency(header_key, body)
        original_row = self.ledger.find_by_reference(original)
        if original_row is None:
            raise WalletError_(E.E_NOT_FOUND,
                               f"no transaction found for {original}", 404)
        if original_row.get("player_id") != player_id:
            raise WalletError_(E.E_FORBIDDEN,
                               "original transaction belongs to another player", 403)
        if original_row.get("type") != TYPE_DEBIT:
            raise WalletError_(E.E_VALIDATION,
                               "only a debit transaction can be rolled back", 409)
        if original_row.get("status") == STATUS_ROLLED_BACK:
            raise WalletError_(E.E_DUPLICATE,
                               "original transaction was already rolled back", 409)
        amount = int(original_row["amount"])
        currency = original_row.get("currency", self.currency)
        fingerprint = self._fingerprint({"op": TYPE_ROLLBACK, "player": player_id,
                                        "original": original, "amount": amount,
                                        "reason": reason})
        store_key = key
        replay = self._replay(key, fingerprint, None)
        if replay is not None:
            return replay
        guard = self.results.claim(f"rollback:{original}",
                                   self.idempotency_ttl_s)
        if not guard[0] and guard[1] is None:
            prior = self.ledger.find_by_reference(key)
            if prior is not None and prior.get("original_reference") == original:
                prior = dict(prior)
                prior["replayed"] = True
                return prior
            raise WalletError_(E.E_DUPLICATE,
                               "original transaction was already rolled back", 409)
        try:
            txn = self.adapter.credit(player_id, amount,
                                      ref=f"rollback:{original}",
                                      idempotency_key=f"rollback:{key}")
        except WalletError as exc:
            raise WalletError_(E.E_INTERNAL, f"rollback failed: {exc}", 502)
        entry = self._entry(TYPE_ROLLBACK, player_id, amount, currency, key,
                            key, original_row.get("game_code", ""),
                            original_row.get("round_id", ""), actor,
                            txn_id=getattr(txn, "txn_id", None),
                            original_reference=original, reason=reason)
        entry["reverses_txn_id"] = original_row.get("txn_id", "")
        self.ledger.append(entry)
        self._record(actor, "wallet.rollback", "transaction", entry["txn_id"],
                     after=entry)
        return self._finish(key, fingerprint, entry)

    def transactions(self, player_id: str, limit: int = 50) -> list:
        player_id = _text(player_id, "player_id", 128)
        return self.ledger.list_player(player_id, limit)


def replay_marker(entry: dict) -> TxnRef:
    return TxnRef(entry.get("txn_id", ""), entry.get("idempotency_key", ""))
