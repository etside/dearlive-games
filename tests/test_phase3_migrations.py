import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "db/migrations/002_phase3_economy.up.sql"
DOWN = ROOT / "db/migrations/002_phase3_economy.down.sql"
TABLES = [
    "platform_config",
    "currency",
    "locked_field",
    "superadmin_audit",
    "device_session",
    "coin_config",
    "wallet",
    "wallet_transaction",
    "player",
    "player_note",
    "player_status_history",
]
OPERATOR_TABLES = [
    "device_session",
    "coin_config",
    "wallet",
    "wallet_transaction",
    "player",
    "player_note",
    "player_status_history",
]


class Phase3MigrationTest(unittest.TestCase):
    def test_up_declares_required_tables_and_foreign_keys(self):
        up = UP.read_text()
        for table in TABLES:
            self.assertRegex(up, rf"CREATE TABLE IF NOT EXISTS {table}\b")
        for table in OPERATOR_TABLES:
            block = up.split(f"CREATE TABLE IF NOT EXISTS {table}", 1)[1]
            block = block.split("CREATE TABLE IF NOT EXISTS", 1)[0]
            self.assertIn("operator_id UUID NOT NULL REFERENCES operator(id)", block)
        self.assertIn("idempotency_key TEXT NOT NULL UNIQUE", up)
        self.assertIn("wallet_transaction_immutable", up)
        self.assertIn("CREATE INDEX IF NOT EXISTS device_session_status_seen_idx", up)
        self.assertIn("CREATE INDEX IF NOT EXISTS player_operator_status_idx", up)

    def test_down_drops_dependencies_before_parents(self):
        down = DOWN.read_text()
        positions = [re.search(
            rf"^DROP TABLE IF EXISTS {re.escape(table)} CASCADE;$", down, re.M
        ).start() for table in
                     ["player_status_history", "player_note", "wallet_transaction",
                      "wallet", "player", "coin_config", "device_session",
                      "locked_field", "superadmin_audit", "currency",
                      "platform_config", "operator"]]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("DROP FUNCTION IF EXISTS forbid_wallet_transaction_mutation", down)

    def test_up_down_up_against_test_database(self):
        dsn = os.environ.get("TEST_DATABASE_URL")
        if not dsn or not shutil.which("psql"):
            self.skipTest("TEST_DATABASE_URL is not configured")
        self.psql(dsn, UP.read_text())
        for table in TABLES:
            self.assertTrue(self.table_exists(dsn, table), table)
        self.psql(dsn, DOWN.read_text())
        for table in TABLES + ["operator"]:
            self.assertFalse(self.table_exists(dsn, table), table)
        self.psql(dsn, UP.read_text())
        for table in TABLES:
            self.assertTrue(self.table_exists(dsn, table), table)

    def psql(self, dsn, sql):
        subprocess.run(["psql", dsn, "-v", "ON_ERROR_STOP=1", "-c", sql],
                       check=True, capture_output=True, text=True)

    def table_exists(self, dsn, table):
        result = subprocess.run(
            ["psql", dsn, "-At", "-c",
             f"SELECT to_regclass('public.{table}') IS NOT NULL"],
            check=True, capture_output=True, text=True)
        return result.stdout.strip() == "t"


if __name__ == "__main__":
    unittest.main()
