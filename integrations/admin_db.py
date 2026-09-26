"""Factory for the Postgres-backed admin store.

Kept out of :mod:`common.admin_store` on purpose. That module is
standard-library-only and takes a ``cursor_factory`` so it can be tested without
a driver, matching ``common/settlement_postgres.py``. This module is the one
place that knows how to turn ``DATABASE_URL`` into such a factory, and the one
place allowed to import a driver lazily.

The important behaviour is the fallback. When no database is configured we
return :class:`UnavailableAdminStore` rather than an in-memory stand-in, so an
admin route answers 503 instead of rendering a dashboard of invented zeros.
An admin panel that quietly shows zeros is worse than one that refuses to load,
because the zeros read as "a quiet day" instead of "no data source".
"""
import os
from typing import Optional

from common.admin_store import (PostgresAdminStore, UnavailableAdminStore,
                                AdminStoreUnavailable)

# Reconnect on each cursor rather than holding one long-lived connection: the
# admin surface is low-traffic and a per-request connection avoids a stale
# socket wedging a container that has been idle overnight. Swap this for a
# pool if admin traffic ever becomes hot.
_CONNECT_TIMEOUT_S = 5


def _reason() -> str:
    if not os.environ.get("DATABASE_URL", "").strip():
        return "DATABASE_URL is not configured"
    return "DATABASE_URL is configured but no driver is available"


def build_admin_store(database_url: Optional[str] = None) -> AdminStore:
    """Return a Postgres admin store, or an unavailable one that fails loudly.

    Never raises for a missing database or a missing driver: those are
    deployment states the HTTP layer reports as 503, not crash conditions.
    """
    dsn = (database_url if database_url is not None
           else os.environ.get("DATABASE_URL", "")).strip()
    if not dsn:
        return UnavailableAdminStore("DATABASE_URL is not configured")

    try:
        import psycopg
    except ModuleNotFoundError:
        return UnavailableAdminStore(
            "psycopg driver is not installed (pip install 'psycopg[binary]')")

    class _ConnectionCursorFactory:
        """Yields cursors from a short-lived connection and always closes it.

        Commits are explicit in the store, so closing the connection without
        committing would discard work. Every mutating method in
        PostgresAdminStore commits or rolls back before returning, so reaching
        the close means the transaction is already resolved.
        """

        def __init__(self, dsn: str):
            self._dsn = dsn
            self._connection = None

        def _ensure(self):
            if self._connection is None or self._connection.closed:
                self._connection = psycopg.connect(
                    self._dsn, connect_timeout=_CONNECT_TIMEOUT_S)
            return self._connection

        def __call__(self):
            return self._ensure().cursor()

        def close(self):
            if self._connection is not None:
                try:
                    self._connection.close()
                except Exception:
                    pass
                self._connection = None

    factory = _ConnectionCursorFactory(dsn)
    store = PostgresAdminStore(factory)
    store.close = factory.close          # so callers can release the socket
    return store


__all__ = ["build_admin_store", "AdminStoreUnavailable"]
