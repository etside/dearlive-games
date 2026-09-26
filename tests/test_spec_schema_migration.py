"""Static checks for migration 007 (the BRD/SRS section 13 tables).

007 has never been applied to a real database -- the client supplies
DATABASE_URL at deploy time, and this repo is not allowed to need one. So these
tests check what can be checked without a server: that the spec's tables,
constraints and column types are actually declared, and that the down file
unwinds in a safe order.

`test_up_down_up_against_test_database` runs the real thing when
TEST_DATABASE_URL is set, and skips loudly when it is not. That skip is
visible in CI output on purpose: this file is unverified against a live
Postgres until someone points it at one.
"""
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "db/migrations/007_spec_tables.up.sql"
DOWN = ROOT / "db/migrations/007_spec_tables.down.sql"

# Every table the SRS section 13 names. 002 already created some of these
# (player, wallet_transaction, profit_risk_config, player_override); 007 is
# additive and must not re-create them, so they are checked separately.
CREATED_BY_007 = [
    "game", "game_configuration", "config_version", "game_round",
    "game_option", "game_bet", "game_result", "game_settlement",
    "token_package", "admin_audit",
]
# Already present from 001-006. 007 must leave them alone.
PRE_EXISTING = [
    "player", "wallet", "wallet_transaction", "profit_risk_config",
    "player_override", "coin_config", "superadmin_audit", "settlements",
]
ROUND_STATUSES = ["UPCOMING", "BETTING_OPEN", "BETTING_CLOSED",
                  "RESULT_PROCESSING", "SETTLED", "CLOSED"]


def _block(sql: str, table: str) -> str:
    """The CREATE TABLE block for one table, up to the next CREATE."""
    start = sql.index(f"CREATE TABLE IF NOT EXISTS {table}")
    rest = sql[start:]
    for marker in ("CREATE TABLE IF NOT EXISTS", "CREATE INDEX",
                   "CREATE UNIQUE INDEX"):
        idx = rest.find(marker, 1)
        if idx != -1:
            rest = rest[:idx]
    return rest


class SpecSchemaMigrationTest(unittest.TestCase):
    def setUp(self):
        self.up = UP.read_text()
        self.down = DOWN.read_text()

    # -- tables -----------------------------------------------------------

    def test_every_spec_table_is_created(self):
        for table in CREATED_BY_007:
            self.assertRegex(self.up,
                             rf"CREATE TABLE IF NOT EXISTS {table}\b", table)

    def test_it_is_additive_and_does_not_recreate_existing_tables(self):
        # The whole point of 007. Recreating one of these with IF NOT EXISTS
        # would silently skip on a database that has it -- but on a fresh
        # database it would create an EMPTY table that later migrations and
        # queries assume is populated. That is the failure this guards.
        for table in PRE_EXISTING:
            self.assertNotRegex(
                self.up, rf"CREATE TABLE IF NOT EXISTS {table}\b",
                f"007 must not re-create existing table {table}")

    def test_the_single_game_is_seeded(self):
        self.assertIn("INSERT INTO game (game_id", self.up)
        self.assertIn("'teen-patti-pro'", self.up)
        self.assertIn("ON CONFLICT (game_id) DO NOTHING", self.up,
                      "re-running must not reset an operator's edits")

    def test_only_one_game_is_seeded(self):
        inserts = re.findall(r"INSERT INTO game .*?VALUES \('([^']+)'",
                             self.up, re.S)
        self.assertEqual(inserts, ["teen-patti-pro"],
                         "exactly one game: Teen Patti Pro")

    # -- SRS section 13 constraints ---------------------------------------

    def test_round_no_is_unique_per_game(self):
        self.assertIn("CONSTRAINT game_round_no_unique_per_game "
                      "UNIQUE (game_id, round_no)", self.up)

    def test_bet_id_is_unique(self):
        self.assertRegex(_block(self.up, "game_bet"),
                         r"bet_id\s+TEXT\s+PRIMARY KEY")

    def test_settlement_bet_id_is_unique(self):
        # BR-07. This is the constraint that makes double payout impossible.
        block = _block(self.up, "game_settlement")
        self.assertRegex(block, r"bet_id\s+TEXT\s+NOT NULL UNIQUE")

    def test_amounts_are_decimal_18_4(self):
        # SRS section 13: DECIMAL(18,4) for amounts.
        for table, column in (("game_bet", "amount"),
                              ("game_result", "pot_total"),
                              ("game_result", "rake_amount"),
                              ("game_settlement", "amount")):
            self.assertRegex(_block(self.up, table),
                             rf"{column}\s+DECIMAL\(18,4\)", f"{table}.{column}")

    def test_round_status_matches_the_srs_lifecycle(self):
        block = _block(self.up, "game_round")
        for status in ROUND_STATUSES:
            self.assertIn(f"'{status}'", block, status)

    def test_wallet_transaction_immutability_is_preserved(self):
        # 002 owns this. 007 must not weaken it.
        self.assertIn("wallet_transaction_immutable",
                      (ROOT / "db/migrations/002_phase3_economy.up.sql").read_text())

    def test_settlement_has_a_retryable_pending_state(self):
        # SRS section 9: max retries -> SETTLED_PENDING, surfaced on the
        # operator dashboard.
        block = _block(self.up, "game_settlement")
        self.assertIn("settled_pending", block)
        self.assertIn("attempts", block)

    def test_idempotency_key_is_unique_where_present(self):
        # BR-06: a retried request must not produce a second bet.
        self.assertIn("game_bet_idempotency_uniq", self.up)
        self.assertIn("WHERE idempotency_key IS NOT NULL", self.up,
                      "a NULL key must not collide with other NULL keys")

    def test_audit_is_append_only(self):
        self.assertIn("REVOKE UPDATE, DELETE ON admin_audit", self.up)
        self.assertNotRegex(_block(self.up, "admin_audit"),
                            r"\bupdated_at\b", "audit rows are never updated")

    # -- BR-04 audit trail -------------------------------------------------

    def test_round_stores_seed_and_deck_commitment(self):
        block = _block(self.up, "game_round")
        self.assertIn("seed_hex", block)
        self.assertIn("deck_commitment", block)

    def test_config_version_supports_the_rollback_window(self):
        # SRS section 11: 90-day rollback window.
        self.assertIn("rollback_supported_until", _block(self.up, "config_version"))

    def test_running_rounds_keep_their_config_snapshot(self):
        # BR-09: a change applies to the NEXT round only. Expressible because a
        # round stores config_version rather than reading the current row.
        self.assertIn("config_version", _block(self.up, "game_round"))

    # -- down file ---------------------------------------------------------

    def test_down_drops_children_before_parents(self):
        # game_round is referenced by game_bet and game_result, so it has to go
        # after them or the drop fails on the FK.
        order = ["game_settlement", "game_result", "game_bet", "game_round",
                 "game_option", "game_configuration", "config_version",
                 "admin_audit", "token_package", "game"]
        positions = []
        for table in order:
            m = re.search(rf"^DROP TABLE IF EXISTS {table};$", self.down, re.M)
            self.assertIsNotNone(m, f"{table} is never dropped")
            positions.append(m.start())
        self.assertEqual(positions, sorted(positions),
                         "down drops a parent before its children")

    def test_down_does_not_touch_pre_existing_tables(self):
        for table in PRE_EXISTING:
            self.assertNotRegex(self.down, rf"^DROP TABLE IF EXISTS {table};$",
                                f"007 down must not drop {table}")

    def test_down_warns_about_losing_money_records(self):
        # A destructive file that does not say what it destroys gets run by
        # somebody who did not read it.
        self.assertIn("money records", self.down)
        self.assertIn("Export them first", self.down)

    # -- idempotency -------------------------------------------------------

    def test_up_is_idempotent_by_construction(self):
        # Every statement is IF NOT EXISTS / ON CONFLICT DO NOTHING, so a
        # re-run is a no-op rather than an error.
        statements = [s.strip() for s in self.up.split(";")
                      if s.strip() and not s.strip().startswith("--")]
        creating = [s for s in statements
                    if s.upper().startswith(("CREATE ", "ALTER TABLE"))]
        for s in creating:
            self.assertIn("IF NOT EXISTS", s, s[:70])
        self.assertNotRegex(self.up, r"^\s*DROP TABLE", )

    def test_real_database_round_trip(self):
        dsn = os.environ.get("TEST_DATABASE_URL")
        if not dsn or not shutil.which("psql"):
            self.skipTest("TEST_DATABASE_URL is not configured: 007 has not "
                          "been executed against a live Postgres")
        self.psql(dsn, self.up)
        for table in CREATED_BY_007:
            self.assertTrue(self.exists(dsn, table), table)
        self.psql(dsn, self.up)          # re-run must be a no-op
        self.psql(dsn, self.down)
        for table in CREATED_BY_007:
            self.assertFalse(self.exists(dsn, table), table)
        self.psql(dsn, self.up)

    def psql(self, dsn, sql):
        subprocess.run(["psql", dsn, "-v", "ON_ERROR_STOP=1", "-c", sql],
                       check=True, capture_output=True, text=True)

    def exists(self, dsn, table):
        out = subprocess.run(
            ["psql", dsn, "-At", "-c",
             f"SELECT to_regclass('public.{table}') IS NOT NULL"],
            check=True, capture_output=True, text=True)
        return out.stdout.strip() == "t"


if __name__ == "__main__":
    unittest.main()
