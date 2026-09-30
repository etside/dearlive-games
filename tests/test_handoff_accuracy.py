"""docs/HANDOFF.md must not lie to a client.

The handoff is the first document a DearLive integrator reads, and a wrong
value in it costs them an afternoon. Every checkable claim in it is asserted
here against the code, so a rename or a removal breaks a test rather than
silently misleading someone.

Claims that cannot be checked (prose, ordering) are not tested. Claims that
can be are.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDOFF = (ROOT / "docs" / "HANDOFF.md").read_text(encoding="utf-8")
CONFIG_SRC = (ROOT / "common" / "config.py").read_text(encoding="utf-8")
API_SRC = (ROOT / "games" / "teen_patti_pro" / "api.py").read_text(encoding="utf-8")
ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")

# Every variable the handoff tells the client to set, as claimed.
CLAIMED = [
    "DATABASE_URL", "REDIS_HOST", "REDIS_PORT", "APP_ENV", "CURRENCY",
    "TOKEN_KEY_PREFIX", "SETTLEMENT_SIGNING_SECRET", "GAME_ADMIN_KEYS",
    "PROVIDER_API_KEYS", "WALLET_API_KEY", "WALLET_CLIENT_SECRET",
    "DEARLIVE_API_KEY", "DEARLIVE_CLIENT_SECRET", "WEBHOOK_SECRET",
    "OPERATOR_TOKEN_SECRET", "OPERATOR_PIN_HASH", "GAMES_BASE_URL",
    "DEARLIVE_API_BASE_URL", "WALLET_BASE_URL", "PROVIDER_PUBLIC_BASE_URL",
    "SETTLEMENT_WEBHOOK_URL",
]

# Variables that were proposed but do not exist. Documenting these would tell a
# client to set six no-ops and believe they had configured security.
REJECTED = [
    "SUPERADMIN_TOKEN_SECRET", "SUPERADMIN_PIN_HASH", "JWT_SECRET",
    "JWT_REFRESH_SECRET", "SESSION_TOKEN_SECRET", "ENABLE_REAL_MONEY",
]


class HandoffEnvClaimsTest(unittest.TestCase):
    def test_every_claimed_variable_is_mentioned(self):
        for var in CLAIMED:
            self.assertIn(f"`{var}`", HANDOFF, f"{var} not in the handoff table")

    def test_every_claimed_variable_exists_in_the_code_or_env_example(self):
        # A variable the code never reads is a no-op. The handoff must not
        # instruct anyone to set one.
        unread = [v for v in CLAIMED
                  if v not in CONFIG_SRC and f"{v}=" not in ENV_EXAMPLE]
        self.assertEqual(unread, [],
                         f"handoff tells the client to set these, but nothing "
                         f"reads them: {unread}")

    def test_non_existent_variables_are_explicitly_ruled_out(self):
        # Not merely absent: the handoff must warn, because these names look
        # plausible and a reader who has seen them in older material will try.
        for var in REJECTED:
            self.assertIn(var, HANDOFF, f"{var} should be called out as not real")
        block = HANDOFF.split("do **not** exist")[1][:500]
        for var in ("JWT_SECRET", "JWT_REFRESH_SECRET", "SESSION_TOKEN_SECRET"):
            self.assertIn(var, block, f"{var} not ruled out alongside superadmin")

    def test_the_production_gate_matches_the_code(self):
        # The handoff says there is no ENABLE_REAL_MONEY flag and names the
        # real gate. That must stay true.
        self.assertIn("no `ENABLE_REAL_MONEY` flag", HANDOFF)
        self.assertIn("APP_ENV=production", HANDOFF)
        self.assertIn("TBC_RULE_UNCONFIRMED", HANDOFF)
        self.assertNotIn("ENABLE_REAL_MONEY=true", HANDOFF)


class HandoffCommandClaimsTest(unittest.TestCase):
    def test_the_health_path_is_the_one_that_exists(self):
        # /api/v1/health returns 404; only /health is routed.
        self.assertIn("The health endpoint is `/health`", HANDOFF)
        self.assertIn('"/health"', API_SRC)
        self.assertNotIn("/api/v1/health", HANDOFF.split("## Integration Steps")[0])

    def test_it_says_which_statuses_have_no_supertier(self):
        self.assertIn("no `superadmin` role", HANDOFF)
        self.assertIn("admin > operator > auditor", HANDOFF)

    def test_the_role_ladder_is_the_real_one(self):
        self.assertIn('ROLE_LEVEL = {"auditor": 1, "operator": 2, "admin": 3}',
                      API_SRC)

    def test_scripts_it_names_exist(self):
        for name in ("setup-dev.sh", "apply-migration.sh", "serve-demo.sh"):
            self.assertTrue((ROOT / "scripts" / name).is_file(), name)
            self.assertIn(name, HANDOFF, f"{name} not referenced")

    def test_the_demo_command_is_the_real_entrypoint(self):
        self.assertIn("python -m games.teen_patti_pro.api --demo", HANDOFF)
        self.assertIn('"--demo"', API_SRC)

    def test_it_mentions_the_redis_requirement_for_tests(self):
        # Corrected after an outage proved the old "no Redis" claim false.
        self.assertIn("redis-server", HANDOFF)
        self.assertIn("need this", HANDOFF + "  ")

    def test_every_doc_it_links_to_exists(self):
        for m in re.finditer(r"\]\((?!https?:)([^)#]+\.md)", HANDOFF):
            self.assertTrue((ROOT / "docs" / m.group(1)).exists(),
                            f"handoff links to a missing doc: {m.group(1)}")

    def test_migration_numbering_is_described_accurately(self):
        up = sorted((ROOT / "db/migrations").glob("*.up.sql"))
        # 001 is a single file; 002-008 have .up/.down pairs. Seven up-files,
        # numbered to 008. Every .up.sql must have a matching .down.sql, or
        # the handoff cannot honestly say so.
        self.assertEqual(len(up), 7, "expected 001 plus 002-008")
        self.assertTrue(any(p.name.startswith("008") for p in up))
        for p in up:
            if p.name.startswith("001"):
                continue
            down = p.with_name(p.name.replace(".up.sql", ".down.sql"))
            self.assertTrue(down.is_file(),
                            "missing down migration for %s" % p.name)
        self.assertIn("001-008", HANDOFF)
        self.assertIn(".up.sql", HANDOFF,
                      "the up/down file layout should be stated")

    def test_cors_is_not_offered_as_an_app_variable(self):
        # The app sets no Access-Control-* headers, so listing CORS_ORIGINS in
        # the "what you provide" table would send the client to configure a
        # variable nothing reads. It may appear only in the note explaining
        # that it is a reverse-proxy concern.
        # Only table rows count: the explanatory note legitimately names it.
        rows = [ln for ln in HANDOFF.splitlines() if ln.strip().startswith("|")]
        self.assertFalse([r for r in rows if "CORS_ORIGINS" in r],
                         "CORS_ORIGINS must not be a table row: nothing reads it")
        self.assertIn("reverse proxy", HANDOFF)
        self.assertIn("not read here", HANDOFF)


class HandoffNumbersTest(unittest.TestCase):
    def test_test_count_is_current(self):
        claimed = re.search(r"(\d+) passing", HANDOFF)
        self.assertIsNotNone(claimed)
        # Cheap guard: the suite collects at least this many.
        import subprocess
        out = subprocess.run(
            ["/tmp/dlvenv/bin/python", "-m", "pytest", "-q", "--collect-only"],
            cwd=ROOT, capture_output=True, text=True).stdout
        m = re.search(r"(\d+) tests collected", out)
        self.assertIsNotNone(m, out[-300:])
        self.assertGreaterEqual(int(m.group(1)), int(claimed.group(1)))

    def test_asset_count_is_current(self):
        # The card faces and the back are crops of a raster reference sheet and
        # ship as PNGs; everything else is SVG. extracted/ is unpacked source,
        # never drawn, so it is excluded. The runtime total is unchanged at 93
        # because 52 SVGs became 52 PNGs.
        base = ROOT / "assets/games/teen-patti-pro"
        pack = [p for p in base.rglob("*")
                if p.is_file() and p.suffix in (".svg", ".png")
                and "extracted" not in p.relative_to(base).parts]
        self.assertEqual(len(pack), 93)
        self.assertIn("93 / 93", HANDOFF)

    def test_known_limitations_are_stated_honestly(self):
        for phrase in ("Visual appearance", "reference-image comparison",
                       "No production URL", "Auto Bet"):
            self.assertIn(phrase, HANDOFF, f"{phrase} not in known limitations")

    def test_it_does_not_claim_a_visual_verification(self):
        low = HANDOFF.lower()
        for overclaim in ("visually verified", "matches the reference",
                          "pixel-perfect", "reference match confirmed"):
            self.assertNotIn(overclaim, low, f"overclaims: {overclaim}")


class DeliveryInstructionAccuracyTest(unittest.TestCase):
    """No client-facing file may point at something that does not exist.

    Two of these were wrong before this test existed: `setup-dev.sh` and
    `DEPLOYMENT.md` both told the client to curl /api/v1/health, which 404s.
    A wrong path in a setup script costs an integrator an hour.
    """

    def _client_facing(self):
        files = [ROOT / "README.md", ROOT / "docs/HANDOFF.md",
                 ROOT / "docs/DEPLOYMENT.md", ROOT / "docs/TROUBLESHOOTING.md",
                 ROOT / "scripts/setup-dev.sh", ROOT / "scripts/apply-migration.sh",
                 ROOT / ".github/workflows/deploy.yml"]
        return {p: p.read_text(encoding="utf-8") for p in files if p.is_file()}

    def test_health_path_is_the_one_that_is_routed(self):
        # The only legitimate mention of the wrong path is one that says it
        # does not exist.
        for path, text in self._client_facing().items():
            for line in text.splitlines():
                if "/api/v1/health" not in line:
                    continue
                self.assertRegex(line, r"404|does not exist|no such",
                                 f"{path.name} cites a path that 404s, "
                                 f"without saying so: {line.strip()[:70]}")

    def test_health_response_shape_is_not_invented(self):
        # The main API's /health reports game/config/confirmed. services.* is
        # the staging adapter's, and must not be attributed to the main API.
        api_health_keys = {"game", "config", "confirmed"}
        main = self._client_facing()[ROOT / "docs/HANDOFF.md"]
        for line in main.splitlines():
            if "data.services" in line:
                self.fail("handoff attributes services.* to the main API: " + line)

    def test_scripts_exist_and_are_executable(self):
        for p in (ROOT / "scripts/setup-dev.sh", ROOT / "scripts/apply-migration.sh",
                  ROOT / "scripts/serve-demo.sh"):
            self.assertTrue(p.is_file(), p)
            import os
            self.assertTrue(os.access(p, os.X_OK), f"{p} not executable")

    def test_the_demo_flag_is_what_the_docs_prescribe(self):
        # --confirmed alone is not the documented zero-dependency path; --demo
        # forces in-memory stores and defaults to port 8000.
        for path, text in self._client_facing().items():
            for line in text.splitlines():
                if "teen_patti_pro.api --confirmed" in line:
                    self.fail(f"{path.name} prescribes --confirmed as the "
                              f"zero-dep demo; use --demo: {line.strip()[:60]}")

    def test_setup_script_runs_clean(self):
        """Runs in a scratch copy, never the working tree.

        setup-dev.sh seeds .env from the template when none exists. It leaves
        an existing .env alone, but a test that writes into the repository is
        still the wrong shape, so the script is exercised against a copy.
        """
        import shutil
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp) / "repo"
            scratch.mkdir()
            shutil.copytree(ROOT / "scripts", scratch / "scripts")
            shutil.copy(ROOT / ".env.example", scratch / ".env.example")
            out = subprocess.run(["bash", str(scratch / "scripts/setup-dev.sh")],
                                 cwd=scratch, capture_output=True, text=True,
                                 timeout=300)
            self.assertEqual(out.returncode, 0,
                             out.stdout[-400:] + out.stderr[-400:])
            self.assertIn("Setup complete", out.stdout)

    def test_setup_script_never_clobbers_an_existing_env(self):
        # The behaviour that makes the test above safe, pinned directly.
        src = (ROOT / "scripts/setup-dev.sh").read_text(encoding="utf-8")
        guard = src.index("if [ -f .env ]")
        write = src.index("cp .env.example .env")
        self.assertLess(guard, write,
                        "the -f .env guard must come before the copy")


if __name__ == "__main__":
    unittest.main()
