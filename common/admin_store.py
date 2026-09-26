"""Postgres store for admin deep-control entities.

Why this exists
---------------
The specification for the admin panel says every field must come from the
database. Before this, admin reads were served from in-process state
(``Handler.svc.config``, ``Handler.game_enabled``) and the dashboard returned
hardcoded zeros, so there was nothing to make real.

Four specification tables mapped onto tables that already exist
(``coin_package``, ``game_configuration``, ``audit_log``), and this module
covers the four that did not: ``profit_risk_config``, ``player_override``,
``vip_tier`` and ``withdrawal_request``. It also provides version history and
rollback on top of the already-versioned ``game_configuration`` table, so
"changes apply to NEXT round only" is a property of the data rather than a
convention.

Design notes
------------
Connection handling is deliberately minimal, mirroring
``common/settlement_postgres.py``: the caller supplies a DB-API-like cursor
factory, and the module stays standard-library-only so it works with psycopg,
psycopg2, or a project adapter without adding a driver dependency here.

Two rules are enforced in SQL rather than trusted to callers:

* **Never mutate a live config row.** Saving a new profit/risk setting inserts
  the next ``version`` and flips ``active`` inside one transaction, so a
  concurrent reader always sees exactly one complete configuration.
* **Never delete an override or an audit row.** Overrides are revoked
  (``revoked_at``) and remain queryable, because the audit trail is the only
  record of why a player's edge differed.
"""
import json
from typing import Any, Dict, List, Optional

PROFIT_RISK_CONFIG_ID = "default"

# Column order for every profit_risk_config read. Kept in one place so a
# migration adding a column has exactly one place to update.
_PR_FIELDS = ("config_id", "version", "base_house_edge_pct", "vip_profit_adj_pct",
              "max_payout_per_round", "rng_weight", "max_daily_loss_per_player",
              "active", "created_by", "created_at")

_PR_SELECT = (
    "SELECT config_id, version, base_house_edge_pct, vip_profit_adj_pct, "
    "max_payout_per_round, rng_weight, max_daily_loss_per_player, active, "
    "created_by, created_at FROM profit_risk_config")


class AdminStoreUnavailable(RuntimeError):
    """No database is configured, so admin reads cannot be served truthfully.

    Raised instead of returning fabricated or empty-but-successful data. The
    HTTP layer turns this into a 503 with this message, which is the whole
    point: an admin panel that silently shows zeros is worse than one that
    refuses to load.
    """


def _num(value: Any) -> Any:
    """Coerce NUMERIC/COUNT results to plain floats/ints.

    Drivers disagree: psycopg returns ``Decimal`` for NUMERIC, psycopg2 does
    too, and SQL proxies frequently hand back a ``str``. None of those survive
    ``json.dumps``, so normalise here and keep the HTTP layer free of driver
    types. Booleans are left alone (they are not numeric in this schema).
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if hasattr(value, "quantize") and hasattr(value, "as_tuple"):
        return float(value)          # Decimal-like
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            return value             # genuinely non-numeric text
    return value


def _now_ms() -> int:
    """Current epoch milliseconds. Indirection so tests can freeze the clock."""
    import time
    return int(time.time() * 1000)


def _pct_change(current: int, previous: int) -> Optional[float]:
    """Percentage change, or None when there is no baseline to compare against.

    Returning None rather than 0.0 matters: "no change" and "nothing to
    compare against" are different facts, and a dashboard that renders both as
    "0%" is lying about one of them.
    """
    if previous in (0, None):
        return None
    return round((current - previous) * 100.0 / float(previous), 1)


def _row_to_pr(row) -> Dict[str, Any]:
    out = {}
    for name, value in zip(_PR_FIELDS, row):
        out[name] = _num(value)
    out["active"] = bool(out["active"])
    return out


class AdminStore:
    """Interface for admin reads/writes. See module docstring."""

    # -- dashboard --
    def dashboard_kpis(self) -> Dict[str, Any]: ...

    # -- token packages (coin_package) --
    def list_packages(self, active_only: bool = False) -> List[Dict[str, Any]]: ...
    def get_package(self, package_id: str) -> Optional[Dict[str, Any]]: ...
    def create_package(self, **kw) -> Dict[str, Any]: ...
    def update_package(self, package_id: str, **kw) -> Dict[str, Any]: ...
    def archive_package(self, package_id: str) -> bool: ...

    # -- platform settings (platform_config key/value) --
    def get_settings(self) -> Dict[str, Any]: ...
    def put_settings(self, values: Dict[str, Any],
                     actor: str = "") -> Dict[str, Any]: ...

    # -- audit (audit_log) --
    def list_audit(self, actor: Optional[str] = None, action: Optional[str] = None,
                   entity: Optional[str] = None, since_ms: Optional[int] = None,
                   limit: int = 100) -> List[Dict[str, Any]]: ...

    # -- game enable / disable (game) --
    def list_games_admin(self) -> List[Dict[str, Any]]: ...
    def set_game_enabled(self, game_id: str, enabled: bool,
                         status: Optional[str] = None,
                         message: str = "") -> Dict[str, Any]: ...

    # -- profit & risk --
    def get_active_profit_risk(self) -> Optional[Dict[str, Any]]: ...
    def list_profit_risk_versions(self, limit: int = 10) -> List[Dict[str, Any]]: ...
    def save_profit_risk(self, values: Dict[str, Any], actor: str) -> Dict[str, Any]: ...

    # -- player overrides --
    def create_player_override(self, **kw) -> Dict[str, Any]: ...
    def get_live_player_override(self, player_id: str) -> Optional[Dict[str, Any]]: ...
    def revoke_player_override(self, override_id: str, actor: str) -> bool: ...
    def list_player_overrides(self, player_id: Optional[str] = None,
                              limit: int = 50) -> List[Dict[str, Any]]: ...

    # -- vip tiers --
    def list_vip_tiers(self) -> List[Dict[str, Any]]: ...
    def upsert_vip_tier(self, tier_id: str, name: str, rank: int,
                        house_edge_adj_pct: float = 0.0,
                        perks: Optional[dict] = None) -> Dict[str, Any]: ...

    # -- withdrawals --
    def create_withdrawal_request(self, request_id: str, player_id: str,
                                  amount: int, currency: str = "COIN",
                                  reason: str = "", actor: str = "") -> Dict[str, Any]: ...
    def list_withdrawal_requests(self, status: Optional[str] = None,
                                 limit: int = 50) -> List[Dict[str, Any]]: ...
    def set_withdrawal_status(self, request_id: str, status: str, actor: str,
                              reason: str = "") -> Dict[str, Any]: ...

    # -- game config versioning --
    def list_game_config_versions(self, game_id: str,
                                  limit: int = 10) -> List[Dict[str, Any]]: ...
    def get_game_config_version(self, game_id: str,
                                version: str) -> Optional[Dict[str, Any]]: ...
    def save_game_config_version(self, game_id: str, payload: Dict[str, Any],
                                 confirmed: bool = False,
                                 tbc: Optional[list] = None) -> Dict[str, Any]: ...
    def rollback_game_config(self, game_id: str, version: str) -> Dict[str, Any]: ...


class UnavailableAdminStore:
    """Stands in when no database is configured.

    Every operation raises, so a route that forgets to check fails loudly at
    the first call rather than rendering a plausible-looking empty dashboard.

    Note this deliberately does *not* inherit :class:`AdminStore`. Inheriting
    would make ``__getattr__`` unreachable for any method the base class
    defines -- attribute lookup would find the base method and silently
    succeed. The raisers are attached explicitly below, and
    ``tests/test_admin_store.py`` asserts that every base method is covered.
    """

    def __init__(self, reason: str = "DATABASE_URL is not configured"):
        self._reason = reason

    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        raise AdminStoreUnavailable(self._reason)

    def __repr__(self):
        return f"<UnavailableAdminStore {self._reason!r}>"


def _unavailable_method(name):
    def _raise(self, *args, **kwargs):
        raise AdminStoreUnavailable(
            f"{name} needs a database: {self._reason}")
    _raise.__name__ = name
    _raise.__qualname__ = f"UnavailableAdminStore.{name}"
    return _raise


# Cover every public method on the interface. Derived from the interface rather
# than hand-listed so a new AdminStore method cannot be forgotten here.
for _name in dir(AdminStore):
    if not _name.startswith("_") and callable(getattr(AdminStore, _name, None)):
        setattr(UnavailableAdminStore, _name, _unavailable_method(_name))


class PostgresAdminStore(AdminStore):
    """cursor_factory() -> a fresh DB-API cursor whose connection is reachable
    as ``cursor.connection`` (psycopg, psycopg2, or a project adapter)."""

    def __init__(self, cursor_factory):
        self._cursor_factory = cursor_factory

    # -- internals --

    def _commit(self, cur):
        conn = getattr(cur, "connection", None)
        if conn is not None and hasattr(conn, "commit"):
            conn.commit()

    def _rollback(self, cur):
        conn = getattr(cur, "connection", None)
        if conn is not None and hasattr(conn, "rollback"):
            conn.rollback()

    # -- token packages (coin_package) --
    #
    # "Delete" is a soft archive (is_active = FALSE) rather than a row removal.
    # A package may already have been bought, and the wallet_transaction rows
    # from that purchase must not be left pointing at a package that no longer
    # exists.

    _PKG_COLS = ("package_id", "name", "coins", "price_minor", "currency",
                 "bonus_percent", "bonus_coins", "is_active", "sort_order",
                 "tags", "created_at", "updated_at")

    def list_packages(self, active_only: bool = False) -> List[Dict[str, Any]]:
        cur = self._cursor_factory()
        try:
            sql = "SELECT " + ", ".join(self._PKG_COLS) + " FROM coin_package "
            if active_only:
                sql += "WHERE is_active "
            sql += "ORDER BY sort_order, package_id"
            cur.execute(sql)
            return [self._pkg_row(r) for r in cur.fetchall()]
        finally:
            self._close(cur)

    def _pkg_row(self, r) -> Dict[str, Any]:
        return {"package_id": r[0], "name": r[1], "coins": int(r[2]),
                "price_minor": int(r[3]), "currency": r[4],
                "bonus_percent": int(r[5]), "bonus_coins": int(r[6]),
                "is_active": bool(r[7]), "sort_order": int(r[8]),
                "tags": r[9] if isinstance(r[9], list) else json.loads(r[9] or "[]"),
                "created_at": r[10], "updated_at": r[11]}

    def get_package(self, package_id: str) -> Optional[Dict[str, Any]]:
        cur = self._cursor_factory()
        try:
            cur.execute("SELECT " + ", ".join(self._PKG_COLS) +
                        " FROM coin_package WHERE package_id = %s", (package_id,))
            r = cur.fetchone()
            return self._pkg_row(r) if r else None
        finally:
            self._close(cur)

    def create_package(self, **kw) -> Dict[str, Any]:
        cur = self._cursor_factory()
        try:
            cur.execute(
                "INSERT INTO coin_package (package_id, name, coins, price_minor, "
                "currency, bonus_percent, bonus_coins, is_active, sort_order, tags) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING " +
                ", ".join(self._PKG_COLS),
                (kw["package_id"], kw["name"], int(kw["coins"]),
                 int(kw["price_minor"]), kw.get("currency", "USD"),
                 int(kw.get("bonus_percent", 0)), int(kw.get("bonus_coins", 0)),
                 bool(kw.get("is_active", True)), int(kw.get("sort_order", 0)),
                 json.dumps(list(kw.get("tags") or []))))
            r = cur.fetchone()
            self._commit(cur)
            return self._pkg_row(r)
        except Exception:
            self._rollback(cur)
            raise
        finally:
            self._close(cur)

    def update_package(self, package_id: str, **kw) -> Dict[str, Any]:
        """Partial update: only supplied keys are written, so omitting a field
        can never blank it."""
        allowed = {"name": str, "coins": int, "price_minor": int,
                   "currency": str, "bonus_percent": int, "bonus_coins": int,
                   "is_active": bool, "sort_order": int, "tags": list}
        sets, params = [], []
        for key, caster in allowed.items():
            if key in kw and kw[key] is not None:
                sets.append(f"{key} = %s")
                params.append(json.dumps(caster(value := kw[key]))
                              if caster is list else caster(value))
        if not sets:
            return self.get_package(package_id) or {}
        params.append(package_id)
        cur = self._cursor_factory()
        try:
            cur.execute("UPDATE coin_package SET " + ", ".join(sets) +
                        ", updated_at = NOW() WHERE package_id = %s RETURNING " +
                        ", ".join(self._PKG_COLS), tuple(params))
            r = cur.fetchone()
            if not r:
                raise KeyError(package_id)
            self._commit(cur)
            return self._pkg_row(r)
        except Exception:
            self._rollback(cur)
            raise
        finally:
            self._close(cur)

    def archive_package(self, package_id: str) -> bool:
        cur = self._cursor_factory()
        try:
            cur.execute("UPDATE coin_package SET is_active = FALSE, "
                        "updated_at = NOW() WHERE package_id = %s "
                        "AND is_active", (package_id,))
            changed = cur.rowcount
            self._commit(cur)
            return bool(changed)
        except Exception:
            self._rollback(cur)
            raise
        finally:
            self._close(cur)

    # -- platform settings (platform_config) --

    def get_settings(self) -> Dict[str, Any]:
        """All key/value rows as a flat mapping."""
        cur = self._cursor_factory()
        try:
            cur.execute("SELECT key, value_json FROM platform_config")
            out = {}
            for k, v in cur.fetchall():
                out[k] = v if isinstance(v, (dict, list, int, float, bool, type(None))) \
                    else json.loads(v)
            return out
        finally:
            self._close(cur)

    def put_settings(self, values: Dict[str, Any],
                     actor: str = "") -> Dict[str, Any]:
        """Upsert each key in one transaction, so a failure cannot leave the
        platform half-configured."""
        if not values:
            return self.get_settings()
        cur = self._cursor_factory()
        try:
            for key, value in values.items():
                cur.execute(
                    "INSERT INTO platform_config (key, value_json) VALUES (%s, %s) "
                    "ON CONFLICT (key) DO UPDATE SET value_json = EXCLUDED.value_json, "
                    "updated_at = NOW()", (str(key), json.dumps(value)))
            self._commit(cur)
        except Exception:
            self._rollback(cur)
            raise
        finally:
            self._close(cur)
        return self.get_settings()

    # -- audit (audit_log) --
    #
    # Append-only. Nothing in this store issues UPDATE or DELETE against
    # audit_log; entries are corrected by writing a new one.

    def list_audit(self, actor: Optional[str] = None, action: Optional[str] = None,
                   entity: Optional[str] = None, since_ms: Optional[int] = None,
                   limit: int = 100) -> List[Dict[str, Any]]:
        where, params = [], []
        if actor:
            where.append("actor = %s"); params.append(actor)
        if action:
            where.append("action = %s"); params.append(action)
        if entity:
            where.append("entity = %s"); params.append(entity)
        if since_ms:
            where.append("created_at >= to_timestamp(%s)")
            params.append(float(since_ms) / 1000.0)
        sql = ("SELECT audit_id, actor, action, entity, entity_id, before_data, "
               "after_data, created_at FROM audit_log ")
        if where:
            sql += "WHERE " + " AND ".join(where) + " "
        sql += "ORDER BY created_at DESC LIMIT %s"
        params.append(int(limit))
        cur = self._cursor_factory()
        try:
            cur.execute(sql, tuple(params))
            out = []
            for r in cur.fetchall():
                out.append({
                    "audit_id": r[0], "actor": r[1], "action": r[2],
                    "entity": r[3], "entity_id": r[4],
                    "before": r[5] if isinstance(r[5], dict) else json.loads(r[5] or "null"),
                    "after": r[6] if isinstance(r[6], dict) else json.loads(r[6] or "null"),
                    "created_at": r[7]})
            return out
        finally:
            self._close(cur)

    # -- game enable / disable (game) --

    def list_games_admin(self) -> List[Dict[str, Any]]:
        cur = self._cursor_factory()
        try:
            cur.execute("SELECT game_id, name, status, enabled FROM game ORDER BY game_id")
            return [{"game_id": r[0], "name": r[1], "status": r[2],
                     "enabled": bool(r[3])} for r in cur.fetchall()]
        finally:
            self._close(cur)

    def set_game_enabled(self, game_id: str, enabled: bool,
                         status: Optional[str] = None,
                         message: str = "") -> Dict[str, Any]:
        """Flip the lobby toggle, and optionally set the lifecycle status.

        A round in flight is never interrupted: round state lives in the game
        engine, not in this row, so an in-progress round finishes normally and
        only new rounds stop being offered.
        """
        cur = self._cursor_factory()
        try:
            if status is None:
                status = "live" if enabled else "disabled"
            if message:
                cur.execute(
                    "INSERT INTO platform_config (key, value_json) VALUES (%s, %s) "
                    "ON CONFLICT (key) DO UPDATE SET value_json = EXCLUDED.value_json, "
                    "updated_at = NOW()",
                    (f"game_message:{game_id}", json.dumps({"message": message})))
            cur.execute("UPDATE game SET enabled = %s, status = %s WHERE game_id = %s "
                        "RETURNING game_id, name, status, enabled",
                        (bool(enabled), str(status), game_id))
            r = cur.fetchone()
            if not r:
                raise KeyError(game_id)
            self._commit(cur)
            return {"game_id": r[0], "name": r[1], "status": r[2],
                    "enabled": bool(r[3]), "message": message}
        except Exception:
            self._rollback(cur)
            raise
        finally:
            self._close(cur)

    # -- dashboard --

    def dashboard_kpis(self) -> Dict[str, Any]:
        """Real aggregates for the dashboard. No estimate, no zero-fill.

        The previous implementation returned ``today_pnl: 0`` and
        ``total_revenue: 0`` as literals, which is indistinguishable from a
        genuinely empty day. Every field here is either a COUNT/SUM the
        database computed, or a ``has_data`` flag the UI can use to render
        "No data yet" honestly.

        Day boundaries are **UTC**, computed in Python rather than SQL. Two
        reasons: ``bets`` stores ``decision_time_ms`` (epoch millis) and has no
        timestamp column at all, so the comparison has to be numeric; and
        leaving the boundary to the database would silently follow the server's
        session timezone, making "today" shift with host configuration.
        """
        now_ms = int(_now_ms())
        day_ms = 86_400_000
        today_start = now_ms - (now_ms % day_ms)
        yesterday_start = today_start - day_ms

        cur = self._cursor_factory()
        try:
            # 1. games: live vs total. `status` is the source of truth;
            #    `enabled` is the lobby toggle and can disagree with it during
            #    maintenance, so both are reported.
            cur.execute("SELECT count(*) FILTER (WHERE status = 'live'), "
                        "count(*), count(*) FILTER (WHERE NOT enabled) FROM game")
            g = cur.fetchone() or (0, 0, 0)
            games_live, games_total, games_disabled = int(g[0]), int(g[1]), int(g[2])

            # 2. rounds in flight, plus how many started in the last hour for
            #    the "+N vs last hour" delta.
            cur.execute("SELECT count(*) FROM rounds "
                        "WHERE status IN ('BETTING_OPEN', 'DEALING')")
            live_rounds = int((cur.fetchone() or (0,))[0])
            cur.execute("SELECT count(*) FROM rounds WHERE created_at_ms >= %s",
                        (now_ms - 3_600_000,))
            rounds_last_hour = int((cur.fetchone() or (0,))[0])

            # 3. bet volume for today and yesterday (epoch-millis column).
            cur.execute("SELECT COALESCE(sum(amount), 0) FROM bets "
                        "WHERE decision_time_ms >= %s AND decision_time_ms < %s",
                        (today_start, now_ms + 1))
            bets_today = int((cur.fetchone() or (0,))[0])
            cur.execute("SELECT COALESCE(sum(amount), 0) FROM bets "
                        "WHERE decision_time_ms >= %s AND decision_time_ms < %s",
                        (yesterday_start, today_start))
            bets_yesterday = int((cur.fetchone() or (0,))[0])
            cur.execute("SELECT count(*) FROM bets "
                        "WHERE decision_time_ms >= %s AND decision_time_ms < %s",
                        (today_start, now_ms + 1))
            bet_count_today = int((cur.fetchone() or (0,))[0])

            # 4. realised payouts today. settlements.created_at is a real
            #    TIMESTAMPTZ, so compare against a UTC epoch.
            cur.execute("SELECT COALESCE(sum(payout), 0) FROM settlements "
                        "WHERE created_at >= to_timestamp(%s)",
                        (today_start / 1000.0,))
            payouts_today = int((cur.fetchone() or (0,))[0])
            cur.execute("SELECT count(*) FROM settlements "
                        "WHERE created_at >= to_timestamp(%s) "
                        "AND credited IS NOT TRUE", (today_start / 1000.0,))
            settlements_owed = int((cur.fetchone() or (0,))[0])

            # 5. pending withdrawals.
            cur.execute("SELECT count(*) FROM withdrawal_request "
                        "WHERE status = 'PENDING'")
            pending_withdrawals = int((cur.fetchone() or (0,))[0])
        finally:
            self._close(cur)

        # Net profit is the *realised* figure: staked minus paid out. The
        # configured house edge is an input to pricing, not an outcome; the two
        # are reported separately because conflating them would overstate
        # profit on any day where the realised hold differed from target.
        net_today = bets_today - payouts_today

        return {
            "generated_at_ms": now_ms,
            "day_start_ms": today_start,
            "day_boundary": "UTC",
            "games": {"live": games_live, "total": games_total,
                      "disabled": games_disabled, "has_data": games_total > 0},
            "rounds": {"live": live_rounds, "last_hour": rounds_last_hour,
                       "has_data": live_rounds > 0 or rounds_last_hour > 0},
            "bets": {"today": bets_today, "yesterday": bets_yesterday,
                     "count_today": bet_count_today, "has_data": bet_count_today > 0,
                     "change_pct": _pct_change(bets_today, bets_yesterday)},
            "payouts": {"today": payouts_today, "has_data": payouts_today > 0},
            "profit": {"net_today": net_today, "net_yesterday": bets_yesterday,
                       "has_data": bets_today > 0 or payouts_today > 0,
                       "change_pct": _pct_change(net_today, bets_yesterday)},
            "settlements_owed": settlements_owed,
            "pending_withdrawals": pending_withdrawals,
        }

    # -- profit & risk --

    def get_active_profit_risk(self) -> Optional[Dict[str, Any]]:
        cur = self._cursor_factory()
        try:
            cur.execute(_PR_SELECT + " WHERE config_id = %s AND active "
                        "ORDER BY version DESC LIMIT 1", (PROFIT_RISK_CONFIG_ID,))
            row = cur.fetchone()
            return _row_to_pr(row) if row else None
        finally:
            self._close(cur)

    def list_profit_risk_versions(self, limit: int = 10) -> List[Dict[str, Any]]:
        cur = self._cursor_factory()
        try:
            cur.execute(_PR_SELECT + " WHERE config_id = %s "
                        "ORDER BY version DESC LIMIT %s",
                        (PROFIT_RISK_CONFIG_ID, int(limit)))
            return [_row_to_pr(r) for r in cur.fetchall()]
        finally:
            self._close(cur)

    def save_profit_risk(self, values: Dict[str, Any], actor: str) -> Dict[str, Any]:
        """Insert the next version and make it the active one, atomically.

        Deactivating first and inserting second inside one transaction means a
        concurrent reader never observes zero active rows, and a failed insert
        rolls the deactivation back with it.
        """
        cur = self._cursor_factory()
        try:
            cur.execute("SELECT COALESCE(MAX(version), 0) FROM profit_risk_config "
                        "WHERE config_id = %s", (PROFIT_RISK_CONFIG_ID,))
            row = cur.fetchone()
            next_version = int(row[0]) + 1 if row else 1
            cur.execute("UPDATE profit_risk_config SET active = FALSE "
                        "WHERE config_id = %s AND active",
                        (PROFIT_RISK_CONFIG_ID,))
            cur.execute(
                "INSERT INTO profit_risk_config (config_id, version, "
                "base_house_edge_pct, vip_profit_adj_pct, max_payout_per_round, "
                "rng_weight, max_daily_loss_per_player, active, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, %s) RETURNING "
                + ", ".join(_PR_FIELDS),
                (PROFIT_RISK_CONFIG_ID, next_version,
                 float(values.get("base_house_edge_pct", 8.0)),
                 float(values.get("vip_profit_adj_pct", 1.5)),
                 int(values.get("max_payout_per_round", 10000)),
                 float(values.get("rng_weight", 1.0)),
                 int(values.get("max_daily_loss_per_player", 500)),
                 str(actor or "")))
            created = _row_to_pr(cur.fetchone())
            self._commit(cur)
            return created
        except Exception:
            self._rollback(cur)
            raise
        finally:
            self._close(cur)

    # -- player overrides --

    def create_player_override(self, **kw) -> Dict[str, Any]:
        cols = ("override_id", "player_id", "house_edge_pct", "token_delta",
                "custom_loss_limit", "expires_at", "reason", "actor")
        vals = (kw["override_id"], kw["player_id"], kw.get("house_edge_pct"),
                int(kw.get("token_delta", 0)), kw.get("custom_loss_limit"),
                kw.get("expires_at"), kw["reason"], kw["actor"])
        cur = self._cursor_factory()
        try:
            cur.execute(
                "INSERT INTO player_override (" + ", ".join(cols) + ") "
                "VALUES (" + ", ".join(["%s"] * len(cols)) + ") RETURNING "
                + ", ".join(cols), vals)
            row = cur.fetchone()
            self._commit(cur)
            return dict(zip(cols, row))
        except Exception:
            self._rollback(cur)
            raise
        finally:
            self._close(cur)

    def get_live_player_override(self, player_id: str) -> Optional[Dict[str, Any]]:
        cur = self._cursor_factory()
        try:
            cur.execute(
                "SELECT override_id, player_id, house_edge_pct, token_delta, "
                "custom_loss_limit, expires_at, reason, actor, created_at "
                "FROM player_override WHERE player_id = %s AND revoked_at IS NULL "
                "AND (expires_at IS NULL OR expires_at > NOW()) "
                "ORDER BY created_at DESC LIMIT 1", (player_id,))
            row = cur.fetchone()
            if not row:
                return None
            return {"override_id": row[0], "player_id": row[1],
                    "house_edge_pct": _num(row[2]), "token_delta": int(row[3] or 0),
                    "custom_loss_limit": row[4], "expires_at": row[5],
                    "reason": row[6], "actor": row[7], "created_at": row[8]}
        finally:
            self._close(cur)

    def revoke_player_override(self, override_id: str, actor: str) -> bool:
        cur = self._cursor_factory()
        try:
            cur.execute("UPDATE player_override SET revoked_at = NOW(), "
                        "revoked_by = %s WHERE override_id = %s "
                        "AND revoked_at IS NULL", (str(actor or ""), override_id))
            changed = cur.rowcount
            self._commit(cur)
            return bool(changed)
        except Exception:
            self._rollback(cur)
            raise
        finally:
            self._close(cur)

    def list_player_overrides(self, player_id: Optional[str] = None,
                              limit: int = 50) -> List[Dict[str, Any]]:
        cur = self._cursor_factory()
        try:
            base = ("SELECT override_id, player_id, house_edge_pct, token_delta, "
                    "custom_loss_limit, expires_at, reason, actor, created_at, "
                    "revoked_at FROM player_override ")
            if player_id:
                cur.execute(base + "WHERE player_id = %s "
                            "ORDER BY created_at DESC LIMIT %s",
                            (player_id, int(limit)))
            else:
                cur.execute(base + "ORDER BY created_at DESC LIMIT %s",
                            (int(limit),))
            out = []
            for r in cur.fetchall():
                out.append({"override_id": r[0], "player_id": r[1],
                            "house_edge_pct": _num(r[2]),
                            "token_delta": int(r[3] or 0),
                            "custom_loss_limit": r[4], "expires_at": r[5],
                            "reason": r[6], "actor": r[7], "created_at": r[8],
                            "revoked_at": r[9],
                            "live": r[9] is None})
            return out
        finally:
            self._close(cur)

    # -- vip tiers --

    def list_vip_tiers(self) -> List[Dict[str, Any]]:
        cur = self._cursor_factory()
        try:
            cur.execute("SELECT tier_id, name, rank, house_edge_adj_pct, perks "
                        "FROM vip_tier ORDER BY rank")
            return [{"tier_id": r[0], "name": r[1], "rank": int(r[2]),
                     "house_edge_adj_pct": _num(r[3]),
                     "perks": r[4] if isinstance(r[4], (dict, list)) else json.loads(r[4] or "{}")}
                    for r in cur.fetchall()]
        finally:
            self._close(cur)

    def upsert_vip_tier(self, tier_id: str, name: str, rank: int,
                        house_edge_adj_pct: float = 0.0,
                        perks: Optional[dict] = None) -> Dict[str, Any]:
        cur = self._cursor_factory()
        try:
            cur.execute(
                "INSERT INTO vip_tier (tier_id, name, rank, house_edge_adj_pct, perks) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (tier_id) DO UPDATE SET "
                "name = EXCLUDED.name, rank = EXCLUDED.rank, "
                "house_edge_adj_pct = EXCLUDED.house_edge_adj_pct, "
                "perks = EXCLUDED.perks, updated_at = NOW() "
                "RETURNING tier_id, name, rank, house_edge_adj_pct, perks",
                (tier_id, name, int(rank), float(house_edge_adj_pct),
                 json.dumps(perks or {})))
            r = cur.fetchone()
            self._commit(cur)
            return {"tier_id": r[0], "name": r[1], "rank": int(r[2]),
                    "house_edge_adj_pct": _num(r[3]),
                    "perks": r[4] if isinstance(r[4], (dict, list)) else json.loads(r[4] or "{}")}
        except Exception:
            self._rollback(cur)
            raise
        finally:
            self._close(cur)

    # -- withdrawals --

    def create_withdrawal_request(self, request_id: str, player_id: str,
                                  amount: int, currency: str = "COIN",
                                  reason: str = "", actor: str = "") -> Dict[str, Any]:
        cols = ("request_id", "player_id", "amount", "currency", "status",
                "reason", "actor")
        cur = self._cursor_factory()
        try:
            cur.execute("INSERT INTO withdrawal_request (" + ", ".join(cols) +
                        ") VALUES (%s, %s, %s, %s, 'PENDING', %s, %s) RETURNING " +
                        ", ".join(cols),
                        (request_id, player_id, int(amount), currency, reason, actor))
            r = cur.fetchone()
            self._commit(cur)
            return dict(zip(cols, r))
        except Exception:
            self._rollback(cur)
            raise
        finally:
            self._close(cur)

    def list_withdrawal_requests(self, status: Optional[str] = None,
                                 limit: int = 50) -> List[Dict[str, Any]]:
        cur = self._cursor_factory()
        try:
            cols = ("request_id", "player_id", "amount", "currency", "status",
                    "reason", "actor", "created_at")
            if status:
                cur.execute("SELECT " + ", ".join(cols) +
                            " FROM withdrawal_request WHERE status = %s "
                            "ORDER BY created_at LIMIT %s", (status, int(limit)))
            else:
                cur.execute("SELECT " + ", ".join(cols) +
                            " FROM withdrawal_request ORDER BY created_at LIMIT %s",
                            (int(limit),))
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            self._close(cur)

    def set_withdrawal_status(self, request_id: str, status: str, actor: str,
                              reason: str = "") -> Dict[str, Any]:
        cols = ("request_id", "player_id", "amount", "currency", "status",
                "reason", "actor", "created_at", "decided_at")
        cur = self._cursor_factory()
        try:
            cur.execute("UPDATE withdrawal_request SET status = %s, actor = %s, "
                        "reason = COALESCE(NULLIF(%s, ''), reason), "
                        "updated_at = NOW(), decided_at = NOW() "
                        "WHERE request_id = %s RETURNING " + ", ".join(cols),
                        (status, str(actor or ""), reason, request_id))
            r = cur.fetchone()
            if not r:
                raise KeyError(request_id)
            self._commit(cur)
            return dict(zip(cols, r))
        except Exception:
            self._rollback(cur)
            raise
        finally:
            self._close(cur)

    # -- game config versioning --

    def list_game_config_versions(self, game_id: str,
                                  limit: int = 10) -> List[Dict[str, Any]]:
        cur = self._cursor_factory()
        try:
            cur.execute("SELECT version, confirmed, created_at FROM "
                        "game_configuration WHERE game_id = %s "
                        "ORDER BY created_at DESC LIMIT %s", (game_id, int(limit)))
            return [{"version": r[0], "confirmed": bool(r[1]), "created_at": r[2]}
                    for r in cur.fetchall()]
        finally:
            self._close(cur)

    def get_game_config_version(self, game_id: str,
                                version: str) -> Optional[Dict[str, Any]]:
        cur = self._cursor_factory()
        try:
            cur.execute("SELECT game_id, version, confirmed, tbc, payload, "
                        "created_at FROM game_configuration "
                        "WHERE game_id = %s AND version = %s", (game_id, version))
            r = cur.fetchone()
            if not r:
                return None
            return {"game_id": r[0], "version": r[1], "confirmed": bool(r[2]),
                    "tbc": r[3] if isinstance(r[3], list) else json.loads(r[3] or "[]"),
                    "payload": r[4] if isinstance(r[4], dict) else json.loads(r[4] or "{}"),
                    "created_at": r[5]}
        finally:
            self._close(cur)

    def save_game_config_version(self, game_id: str, payload: Dict[str, Any],
                                 confirmed: bool = False,
                                 tbc: Optional[list] = None) -> Dict[str, Any]:
        cur = self._cursor_factory()
        try:
            version = _next_version(game_id, payload.get("version"))
            cur.execute(
                "INSERT INTO game_configuration (game_id, version, confirmed, tbc, payload) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (game_id, version) DO UPDATE "
                "SET confirmed = EXCLUDED.confirmed, tbc = EXCLUDED.tbc, "
                "payload = EXCLUDED.payload RETURNING game_id, version, confirmed, "
                "tbc, payload, created_at",
                (game_id, version, bool(confirmed), json.dumps(tbc or []),
                 json.dumps(payload)))
            r = cur.fetchone()
            self._commit(cur)
            return {"game_id": r[0], "version": r[1], "confirmed": bool(r[2]),
                    "tbc": r[3] if isinstance(r[3], list) else json.loads(r[3] or "[]"),
                    "payload": r[4] if isinstance(r[4], dict) else json.loads(r[4] or "{}"),
                    "created_at": r[5]}
        except Exception:
            self._rollback(cur)
            raise
        finally:
            self._close(cur)

    def rollback_game_config(self, game_id: str, version: str) -> Dict[str, Any]:
        """Copy an old version forward as a new version.

        Rollback deliberately does not delete or mutate history: the old
        payload is re-inserted under a fresh version, so the audit trail shows
        both the change and the undo.
        """
        cur = self._cursor_factory()
        try:
            cur.execute("SELECT confirmed, tbc, payload FROM game_configuration "
                        "WHERE game_id = %s AND version = %s", (game_id, version))
            r = cur.fetchone()
            if not r:
                raise KeyError(f"{game_id}@{version}")
            payload = r[2] if isinstance(r[2], dict) else json.loads(r[2] or "{}")
            new_version = _next_version(game_id, None)
            cur.execute(
                "INSERT INTO game_configuration (game_id, version, confirmed, tbc, payload) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING game_id, version, confirmed, "
                "tbc, payload, created_at",
                (game_id, new_version, bool(r[0]),
                 json.dumps(r[1] if isinstance(r[1], list) else json.loads(r[1] or "[]")),
                 json.dumps(payload)))
            row = cur.fetchone()
            self._commit(cur)
            return {"game_id": row[0], "version": row[1],
                    "confirmed": bool(row[2]),
                    "tbc": row[3] if isinstance(row[3], list) else json.loads(row[3] or "[]"),
                    "payload": row[4] if isinstance(row[4], dict) else json.loads(row[4] or "{}"),
                    "created_at": row[5], "rolled_back_from": version}
        except Exception:
            self._rollback(cur)
            raise
        finally:
            self._close(cur)

    @staticmethod
    def _close(cur):
        closer = getattr(cur, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass


def _next_version(game_id: str, proposed) -> str:
    """Version labels are opaque strings; default to a UTC timestamp.

    A timestamp sorts chronologically and is unique without a round trip, which
    matters because the column is TEXT and callers may supply their own labels.
    """
    if proposed:
        return str(proposed)
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S%f")
