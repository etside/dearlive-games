"""The installer must produce code that compiles, not code that looks like code.

The scaffold is emitted as bash heredocs, so there is no way for a test to
import it. But two real defects shipped in it and were only caught by running
tsc against the generated files:

  - BadRequestException was thrown in ledger.schema.ts with no import
  - LedgerStore is an interface, and an interface emits no runtime value, so
    using it as a Nest injection token is a compile error

Both would have broken an integrator's build on first run, which is the worst
possible moment to discover it. These tests generate the scaffold for real and
check the properties that would have caught them, so the next person editing a
heredoc finds out here instead.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "scripts" / "install.sh"

# Everything the scaffold emits, keyed by the marker that ends each heredoc.
MARKERS = ("DARTEOF", "DTOEOF", "SCHEMAEOF", "SVCEOF", "MODEOF", "TSEOF",
           "NEXTEOF", "ENVEOF")


def run_installer(cwd: Path, project_files: dict) -> subprocess.CompletedProcess:
    """Run the installer non-interactively with fixed answers."""
    for name, content in project_files.items():
        (cwd / name).write_text(content, encoding="utf-8")
    # key, secret, callback, mongo, game code, proceed
    answers = "KEY123\nSECRET456\nhttps://cb.example.com\n\n\ny\n"
    return subprocess.run(["bash", str(INSTALL)], cwd=str(cwd), input=answers,
                          capture_output=True, text=True, timeout=120)


def generated_sources(cwd: Path) -> list:
    out = []
    for pattern in ("**/*.ts", "**/*.dart"):
        out.extend(p for p in cwd.glob(pattern) if "node_modules" not in p.parts)
    return sorted(out)


class InstallerScriptTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(INSTALL.is_file())
        self.assertTrue(os.access(INSTALL, os.X_OK),
                        "install.sh must be executable")

    def test_it_is_syntactically_valid_bash(self):
        r = subprocess.run(["bash", "-n", str(INSTALL)], capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_heredoc_marker_leaks_into_a_generated_file(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = Path(d)
            run_installer(cwd, {"package.json":
                                '{"name":"x","dependencies":{"@nestjs/core":"^10"}}'})
            for f in generated_sources(cwd):
                text = f.read_text(encoding="utf-8")
                for marker in MARKERS:
                    self.assertNotIn(
                        marker, text,
                        "%s contains the heredoc marker %s -- the terminator "
                        "is indented or the body is unbalanced" % (f.name, marker))

    def test_nestjs_scaffold_has_the_parts_an_integrator_needs(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = Path(d)
            run_installer(cwd, {"package.json":
                                '{"name":"x","dependencies":{"@nestjs/core":"^10"}}'})
            files = {f.name for f in generated_sources(cwd)}
            for required in ("wallet.dto.ts", "ledger.schema.ts",
                             "wallet-callback.service.ts",
                             "wallet-callback.controller.ts",
                             "teen-patti.module.ts", "teen-patti.config.ts"):
                self.assertIn(required, files,
                              "the NestJS scaffold is missing %s" % required)

    def test_flutter_scaffold_has_a_service_a_bloc_and_a_webview(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = Path(d)
            run_installer(cwd, {"pubspec.yaml": "name: demo\n"})
            files = {f.name for f in generated_sources(cwd)}
            self.assertIn("teen_patti_session_service.dart", files)
            self.assertIn("teen_patti_round_bloc.dart", files)
            self.assertIn("teen_patti_webview.dart", files)
            self.assertIn("teen_patti_config.dart", files)

    def test_everything_the_scaffold_uses_is_imported(self):
        """A symbol used but not imported is a compile error.

        This is how BadRequestException shipped: thrown in ledger.schema.ts
        with no import of it anywhere in the file.
        """
        with tempfile.TemporaryDirectory() as d:
            cwd = Path(d)
            run_installer(cwd, {"package.json":
                                '{"name":"x","dependencies":{"@nestjs/core":"^10"}}'})
            for f in generated_sources(cwd):
                if f.suffix != ".ts":
                    continue
                text = f.read_text(encoding="utf-8")
                # Strip the comment banner and import block before scanning, so
                # a symbol named in prose is not counted as a use.
                body = re.sub(r"^import .*?;$", "", text, flags=re.M)
                body = re.sub(r"^//.*$", "", body, flags=re.M)
                for symbol in ("BadRequestException", "Inject",
                               "UnauthorizedException", "Injectable"):
                    if not re.search(r"\b%s\b" % symbol, body):
                        continue
                    if re.search(r"import[^;]*\b%s\b" % symbol, text):
                        continue
                    self.fail("%s uses %s but never imports it"
                              % (f.name, symbol))

    def test_an_interface_is_never_used_as_an_injection_token(self):
        """Interfaces emit no runtime value, so Nest cannot inject them.

        The token has to be a const or a class. This checks the shape rather
        than compiling, so it holds even where tsc is unavailable.
        """
        with tempfile.TemporaryDirectory() as d:
            cwd = Path(d)
            run_installer(cwd, {"package.json":
                                '{"name":"x","dependencies":{"@nestjs/core":"^10"}}'})
            schema = (cwd / "src" / "teen-patti" / "ledger.schema.ts")
            if not schema.is_file():
                self.skipTest("nestjs scaffold not generated")
            text = schema.read_text(encoding="utf-8")
            interfaces = set(re.findall(r"export interface (\w+)", text))
            for name in interfaces:
                self.assertNotRegex(
                    text, r"\{\s*provide:\s*%s\b" % re.escape(name),
                    "%s is an interface and cannot be an injection token" % name)
            module = (cwd / "src" / "teen-patti" / "teen-patti.module.ts")
            if module.is_file():
                mtext = module.read_text(encoding="utf-8")
                for name in interfaces:
                    self.assertNotRegex(
                        mtext, r"provide:\s*%s\b" % re.escape(name),
                        "the module provides %s directly" % name)

    def test_the_env_file_is_owner_only_and_gitignored(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = Path(d)
            run_installer(cwd, {"package.json":
                                '{"name":"x","dependencies":{"@nestjs/core":"^10"}}'})
            env = cwd / ".env"
            self.assertTrue(env.is_file(), "no .env was written")
            mode = env.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600,
                             "a .env holding the operator secret must be 0600, "
                             "got %o" % mode)
            self.assertIn(".env", (cwd / ".gitignore").read_text())
            self.assertNotIn("SECRET456", (cwd / ".gitignore").read_text())

    def test_an_existing_env_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = Path(d)
            (cwd / ".env").write_text("OPERATOR_API_KEY=keep-me\n", encoding="utf-8")
            run_installer(cwd, {"package.json":
                                '{"name":"x","dependencies":{"@nestjs/core":"^10"}}'})
            self.assertIn("keep-me", (cwd / ".env").read_text(),
                          "the installer overwrote an existing .env")

    def test_the_flutter_client_never_receives_the_hmac_secret(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = Path(d)
            run_installer(cwd, {"pubspec.yaml": "name: demo\n"})
            for f in generated_sources(cwd):
                text = f.read_text(encoding="utf-8")
                # A guide that shows a Flutter client calling POST /api/v1/sessions
                # teaches people to ship the operator's secret in an app bundle.
                self.assertNotIn("OPERATOR_HMAC_SECRET", text)
                self.assertNotIn("/api/v1/sessions", text)

    def test_no_project_leaves_only_the_env_and_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = Path(d)
            result = run_installer(cwd, {})
            self.assertIn("No project detected", result.stdout)
            self.assertTrue((cwd / ".env").is_file())
            self.assertEqual(generated_sources(cwd), [])


if __name__ == "__main__":
    unittest.main()
