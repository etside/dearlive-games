"""Provider table catalog and matchmaking over the existing room engine.

A provider "table" is a configured room profile (limits, currency, seats). The
game engine is untouched: a table id is a room id, and occupancy/status are
read from the authoritative Room snapshot. Matchmaking only selects among
configured tables; it never fabricates a result or a balance.
"""
import os
from dataclasses import dataclass
from typing import List, Optional

DEFAULT_TABLES = (
    "teen-patti-low:Low Stakes:10:100:6:COIN",
    "teen-patti-mid:Mid Stakes:100:1000:6:COIN",
    "teen-patti-high:High Stakes:1000:10000:6:COIN",
)
DEFAULT_MIN_BET = 10
DEFAULT_MAX_BET = 10000
DEFAULT_MAX_PLAYERS = 6
MAX_SEATS = 12


@dataclass(frozen=True)
class Table:
    table_id: str
    label: str
    min_bet: int
    max_bet: int
    max_players: int
    currency: str = "COIN"

    def view(self) -> dict:
        return {"table_id": self.table_id, "label": self.label,
                "min_bet": self.min_bet, "max_bet": self.max_bet,
                "max_players": self.max_players, "currency": self.currency,
                "enabled": True}


class TableCatalog:
    def __init__(self, tables: Optional[List[Table]] = None):
        self._tables: dict = {}
        for table in (tables or []):
            self._tables[table.table_id] = table

    @classmethod
    def from_env(cls, raw: Optional[str] = None) -> "TableCatalog":
        if raw is None:
            raw = os.environ.get("PROVIDER_TABLES", "")
        if not raw.strip():
            raw = ",".join(DEFAULT_TABLES)
        tables = []
        for spec in raw.split(","):
            spec = spec.strip()
            if not spec:
                continue
            parts = [p.strip() for p in spec.split(":")]
            table_id = parts[0]
            if not table_id:
                continue
            label = parts[1] if len(parts) > 1 and parts[1] else table_id
            min_bet = _int(parts[2], DEFAULT_MIN_BET) if len(parts) > 2 else DEFAULT_MIN_BET
            max_bet = _int(parts[3], DEFAULT_MAX_BET) if len(parts) > 3 else DEFAULT_MAX_BET
            max_players = (_int(parts[4], DEFAULT_MAX_PLAYERS)
                           if len(parts) > 4 else DEFAULT_MAX_PLAYERS)
            currency = (parts[5].upper() if len(parts) > 5 and parts[5] else "COIN")
            tables.append(Table(table_id, label, max(1, min_bet),
                                max(max_bet, min_bet), min(MAX_SEATS, max(1, max_players)),
                                currency))
        return cls(tables or [Table("default", "Default", DEFAULT_MIN_BET,
                                    DEFAULT_MAX_BET, DEFAULT_MAX_PLAYERS)])

    def ids(self) -> List[str]:
        return list(self._tables)

    def get(self, table_id: str) -> Optional[Table]:
        return self._tables.get(table_id)

    def require(self, table_id: str) -> Optional[Table]:
        table = self._tables.get(table_id)
        if table is not None:
            return table
        if table_id in ("default", ""):
            return self._tables.get("default")
        return None

    def all(self) -> List[Table]:
        return list(self._tables.values())


def _int(value, fallback):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def room_status(room) -> dict:
    round_obj = getattr(room, "round", None)
    members = getattr(room, "members", {}) or {}
    if round_obj is None:
        status = "WAITING"
    else:
        status = getattr(getattr(round_obj, "status", None), "value", "WAITING")
    return {
        "round_id": getattr(round_obj, "round_id", "") or "",
        "status": status,
        "betting_end_at": getattr(round_obj, "betting_end_at_ms", 0) or 0,
        "players": len(members),
    }


def table_view(table: Table, room=None) -> dict:
    view = table.view()
    view["live"] = room_status(room) if room is not None else {
        "round_id": "", "status": "WAITING", "betting_end_at": 0, "players": 0}
    return view


def list_tables(catalog: TableCatalog, service) -> List[dict]:
    rooms = getattr(service, "rooms", {}) or {}
    return [table_view(table, rooms.get(table.table_id))
            for table in catalog.all()]


def table_detail(catalog: TableCatalog, service, table_id: str) -> Optional[dict]:
    table = catalog.require(table_id)
    if table is None:
        return None
    rooms = getattr(service, "rooms", {}) or {}
    room = rooms.get(table.table_id)
    view = table_view(table, room)
    view["seats"] = sorted((getattr(room, "members", {}) or {}).keys()) \
        if room is not None else []
    return view


def seat_count(service, table_id: str) -> int:
    rooms = getattr(service, "rooms", {}) or {}
    room = rooms.get(table_id)
    if room is None:
        return 0
    return len(getattr(room, "members", {}) or {})


def select_table(catalog: TableCatalog, service, amount: Optional[int] = None,
                 currency: str = "COIN") -> Optional[Table]:
    """Pick a table that can host the next round.

    Fills the fullest eligible table first so seats are consolidated, then
    falls back to the least-occupied one so a player is never stuck waiting.
    """
    currency = (currency or "COIN").upper()
    candidates = [t for t in catalog.all() if t.currency == currency]
    if not candidates:
        candidates = catalog.all()
    if not candidates:
        return None
    if amount is not None:
        eligible = [t for t in candidates
                    if t.min_bet <= int(amount) <= t.max_bet]
        candidates = eligible or candidates
    open_tables = [t for t in candidates
                   if seat_count(service, t.table_id) < t.max_players]
    pool = open_tables or candidates
    pool.sort(key=lambda t: (-seat_count(service, t.table_id), t.table_id))
    return pool[0]
