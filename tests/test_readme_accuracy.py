"""The README must be true.

A README that documents endpoints which do not exist sends an integrator
debugging a 404 against software that is working correctly. These tests check
the README against the code:

* every endpoint in the README's API reference is a real route in api.py
* every command it tells the reader to run is real and works
* every file it links to exists
* it contains no secret-shaped strings
* the repo it describes holds exactly one game

The endpoint check is static (route patterns are compared against the source)
rather than live, because a live 404 cannot distinguish "this route does not
exist" from "this resource does not exist" -- and the latter is a correct
response the README is not wrong about.
"""
import json
import re
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()
API_SRC = (ROOT / "games" / "teen_patti_pro" / "api.py").read_text()


def _as_regex(path: str) -> str:
    """Turn a documented path into a pattern that matches a real route.

    The README writes {id}; the source writes a regex fragment in its place.
    Rather than normalising both into some third form and comparing strings --
    which quietly loses optional groups and prefix routes -- compile the
    documented path as a pattern and let the source pattern match it.
    """
    return re.sub(r"\{[^}]+\}", r"\\S+", path.split("?")[0].rstrip("/"))


def _source_routes():
    """Route patterns the API actually implements, per verb."""
    routes = {}
    for verb, anchor in (("GET", "def do_GET"), ("POST", "def do_POST"),
                         ("PUT", "def do_PUT"), ("DELETE", "def do_DELETE")):
        i = API_SRC.index(anchor)
        later = [API_SRC.index(a) for a in
                 ("def do_GET", "def do_POST", "def do_PUT", "def do_DELETE")
                 if API_SRC.index(a) > i]
        body = API_SRC[i:min(later) if later else len(API_SRC)]
        # The admin deep-control routes are dispatched from the verb into a
        # helper (_admin_post/_admin_put/_admin_delete) behind a
        # path.startswith("/api/v1/admin/") guard. Scanning only the verb body
        # would miss all of them and report them as undocumented-but-absent.
        for helper in ("_admin_post", "_admin_put", "_admin_delete",
                       "_admin_read"):
            if f"def {helper}" in API_SRC:
                body += _method_body(f"def {helper}")
        found = set()
        for m in re.finditer(
                r'path == "([^"]+)"|path\.startswith\("([^"]+)"\)'
                r'|re\.fullmatch\(r"([^"]+)"', body):
            found.add([g for g in m.groups() if g][0])
        routes[verb] = found
    return routes


def _method_body(anchor: str) -> str:
    i = API_SRC.index(anchor)
    j = API_SRC.find("\n    def ", i + 1)
    return API_SRC[i:j if j != -1 else len(API_SRC)]


def _readme_endpoints():
    """(verb, path) pairs from the README's API reference tables."""
    out = []
    section = re.split(r"^### Game$", README, maxsplit=1, flags=re.M)
    if len(section) < 2:
        return out
    # Up to the next H2. Splitting on "---" would stop at the table's own
    # separator row and yield nothing.
    tables = re.split(r"^## ", section[1], maxsplit=1, flags=re.M)[0]
    for line in tables.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        for verb in re.findall(r"GET|POST|PUT|DELETE", cells[0]):
            for path in re.findall(r"`(/[^`]*)`", cells[1]):
                out.append((verb, path))
    return out


class ReadmeEndpointAccuracyTest(unittest.TestCase):
    def setUp(self):
        self.routes = _source_routes()
        self.endpoints = _readme_endpoints()

    def test_the_reference_table_was_actually_parsed(self):
        # Guards against a regex change silently matching nothing, which would
        # make every other test in this class vacuously pass.
        self.assertGreater(len(self.endpoints), 25,
                           "README API reference table failed to parse")

    def test_every_documented_endpoint_exists(self):
        missing = []
        for verb, path in self.endpoints:
            want = _as_regex(path)
            if any(self._matches(c, want) for c in self.routes[verb]):
                continue
            missing.append(f"{verb} {path}")
        self.assertEqual(missing, [],
                         "README documents endpoints that are not implemented")

    @staticmethod
    def _matches(route_pattern: str, want: str) -> bool:
        """True when a source route pattern accepts the documented path."""
        # Source routes are either a literal (path == "/api/v1/games") or a
        # regex fragment (re.fullmatch(r"...", path)).
        for candidate in (route_pattern, re.escape(route_pattern)):
            try:
                if re.fullmatch(candidate, want):
                    return True
            except re.error:
                continue
        return False

    def test_no_documented_path_names_a_removed_feature(self):
        for _, path in self.endpoints:
            for gone in ("wheel", "lucky", "greedy", "baby-king", "superadmin"):
                self.assertNotIn(gone, path.lower(), path)


class ReadmeCommandAccuracyTest(unittest.TestCase):
    def test_the_demo_flag_exists(self):
        # The README's headline command.
        self.assertIn('"--demo"', API_SRC)

    def test_demo_defaults_to_port_8000(self):
        self.assertRegex(API_SRC, r'GAME_DEMO_PORT", "8000"')

    def test_demo_mode_forces_in_memory_stores(self):
        # Otherwise a REDIS_HOST in the shell makes "zero-dependency" a lie.
        self.assertIn("DEMO_MODE",
                      (ROOT / "integrations" / "__init__.py").read_text())

    def test_documented_scripts_exist_and_are_executable(self):
        for name in ("serve-demo.sh", "apply-migration.sh", "setup-dev.sh"):
            path = ROOT / "scripts" / name
            self.assertTrue(path.is_file(), f"scripts/{name} is missing")
            self.assertTrue(os_access(path), f"scripts/{name} is not executable")


def os_access(path: Path) -> bool:
    import os
    return os.access(path, os.X_OK)


class ReadmeLinkTest(unittest.TestCase):
    def test_every_relative_link_resolves(self):
        broken = []
        for m in re.finditer(r"\]\((?!https?:)([^)#]+)(#[^)]*)?\)", README):
            target = (ROOT / m.group(1)).resolve()
            if not target.exists():
                broken.append(m.group(1))
        self.assertEqual(broken, [], f"README links to missing files: {broken}")

    def test_the_live_demo_works_end_to_end(self):
        """Boot --demo exactly as the README says, and prove it is playable."""
        port = 8765
        proc = subprocess.Popen(
            [sys.executable, "-m", "games.teen_patti_pro.api", "--demo",
             "--host", "127.0.0.1", "--port", str(port), "--ws-port", str(port + 1)],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            base = f"http://127.0.0.1:{port}"
            self._await_health(base)
            # The pages the README tells the reader to open.
            for path in ("/teen-patti-pro", "/teen-patti-pro/lobby.html",
                         "/teen-patti-pro/how-to-play.html"):
                self.assertEqual(self._status(base + path), 200, path)
            # A demo session mints a real session and a real balance.
            body = self._json(base + "/demo/session")
            self.assertTrue(body["success"])
            self.assertEqual(body["data"]["mode"], "demo")
            self.assertGreater(body["data"]["balance"], 0)
            self.assertIn("session_id", body["data"])
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _await_health(self, base, attempts=40):
        import time
        for _ in range(attempts):
            if self._status(base + "/health") == 200:
                return
            time.sleep(0.25)
        self.fail("demo server never became healthy")

    def _status(self, url):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:
            return 0

    def _json(self, url):
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read())


class EnvelopeShapeTest(unittest.TestCase):
    """SRS section 7 fixes the envelope, including the type of serverTime."""

    def test_server_time_is_iso8601(self):
        from common.envelope import err, now_iso, ok
        import datetime
        for env in (ok({"a": 1}), err("nope", "X")):
            self.assertIsInstance(env["serverTime"], str)
            parsed = datetime.datetime.fromisoformat(env["serverTime"])
            self.assertIsNotNone(parsed.tzinfo, "serverTime must carry a timezone")

    def test_epoch_millis_is_available_alongside(self):
        # Removing the numeric form would break every consumer that computes a
        # clock skew, so both are always present.
        from common.envelope import ok
        env = ok()
        self.assertIsInstance(env["serverTimeMs"], int)
        self.assertGreater(env["serverTimeMs"], 1_600_000_000_000)

    def test_request_id_is_present_and_unique(self):
        from common.envelope import ok
        a, b = ok(), ok()
        self.assertTrue(a["requestId"])
        self.assertNotEqual(a["requestId"], b["requestId"])

    def test_all_srs_envelope_keys_are_present(self):
        from common.envelope import ok
        for key in ("success", "code", "message", "data", "serverTime",
                    "requestId"):
            self.assertIn(key, ok(), key)

    def test_the_readme_shows_the_same_shape(self):
        for key in ("success", "code", "message", "data", "serverTime",
                    "requestId"):
            self.assertIn(f'"{key}"', README, f"README envelope omits {key}")


class WireEventNameTest(unittest.TestCase):
    """SRS sections 8 and 14 name the same moments differently."""

    def test_every_srs_websocket_event_is_declared(self):
        from common.wire_events import WS_EVENTS
        for name in ("round.created", "round.opened", "round.updated",
                     "betting.closed", "result.processing", "result.declared",
                     "settlement.started", "settlement.completed",
                     "balance.updated", "round.closed", "error"):
            self.assertIn(name, WS_EVENTS, name)

    def test_every_srs_webhook_event_is_declared(self):
        from common.wire_events import WEBHOOK_EVENTS
        for name in ("game.session.created", "game.round.started",
                     "game.bet.accepted", "game.result.published",
                     "game.settlement.completed", "game.error"):
            self.assertIn(name, WEBHOOK_EVENTS, name)

    def test_internal_kinds_map_to_srs_websocket_names(self):
        from common.wire_events import WS_EVENTS, ws_name
        self.assertEqual(ws_name("round.started"), "round.opened")
        self.assertEqual(ws_name("result.published"), "result.declared")
        self.assertEqual(ws_name("result.processing"), "result.processing")
        for kind in ("round.created", "round.started", "result.processing",
                     "settlement.started", "settlement.completed",
                     "betting.closed", "round.cancelled", "error"):
            self.assertIn(ws_name(kind), WS_EVENTS, kind)

    def test_internal_kinds_map_to_srs_webhook_names(self):
        from common.wire_events import WEBHOOK_EVENTS, webhook_name
        self.assertEqual(webhook_name("round.started"), "game.round.started")
        self.assertEqual(webhook_name("result.published"),
                         "game.result.published")
        for kind in ("round.started", "result.published",
                     "settlement.completed", "bet.accepted", "error"):
            self.assertIn(webhook_name(kind), WEBHOOK_EVENTS, kind)

    def test_an_unknown_kind_does_not_leak_a_raw_name(self):
        # A client cannot handle an event it has never heard of.
        from common.wire_events import webhook_name, ws_name
        self.assertEqual(ws_name("something.new"), "error")
        self.assertEqual(webhook_name("something.new"), "game.error")

    def test_every_internal_kind_the_engine_emits_is_mapped(self):
        # An unmapped kind silently becomes "error" on the wire, which is a
        # much harder bug to find than a missing dict entry.
        import re
        from common.wire_events import WS_NAMES, WEBHOOK_NAMES
        from common.webhooks import EVENTS
        engine = (ROOT / "games/teen_patti_pro/engine.py").read_text()
        service = (ROOT / "games/teen_patti_pro/service.py").read_text()
        fired = set(re.findall(r'_fire\("([^"]+)"', service))
        for kind in sorted(fired):
            self.assertIn(kind, WS_NAMES, f"{kind} has no ws_event mapping")
            self.assertIn(kind, WEBHOOK_NAMES, f"{kind} has no webhook mapping")
            self.assertIn(kind, EVENTS, f"{kind} missing from the EVENTS allow-list")

    def test_hub_only_events_need_a_ws_mapping_but_no_webhook(self):
        # round.tick is pushed straight from the ws hub, so it never becomes a
        # webhook. It still has to have a ws_event name or the client sees the
        # internal name.
        from common.wire_events import WS_EVENTS, WS_NAMES
        hub = (ROOT / "games/teen_patti_pro/ws.py").read_text()
        import re
        # "event"/"snapshot"/"pong" are envelope discriminators in ws.py, not
        # event names -- they are the frame type, not something a client routes
        # on by business meaning.
        pushed = set(re.findall(r'"kind": "([a-z_.]+)"', hub)) - {
            "event", "snapshot", "pong"}
        for kind in pushed:
            self.assertIn(kind, WS_NAMES, f"{kind} has no ws_event mapping")
            self.assertIn(WS_NAMES[kind], WS_EVENTS)

    def test_the_readme_lists_the_spec_event_names(self):
        for name in ("round.opened", "result.declared", "settlement.started",
                     "balance.updated", "round.closed"):
            self.assertIn(name, README, name)


class BettingWindowTest(unittest.TestCase):
    def test_default_betting_window_is_thirty_seconds(self):
        from games.teen_patti_pro.config import DEFAULT_CONFIG
        self.assertEqual(DEFAULT_CONFIG.guess_ms, 30_000,
                         "SRS section 1: 30s default betting")

    def test_the_rules_page_states_the_configured_window(self):
        from games.teen_patti_pro.config import DEFAULT_CONFIG
        page = (ROOT / "games/teen_patti_pro/client/how-to-play.html").read_text()
        self.assertIn(f"{DEFAULT_CONFIG.guess_ms // 1000} seconds", page)


class ReadmeHygieneTest(unittest.TestCase):
    def test_no_secret_shaped_strings(self):
        # A README that ships a working credential is a leaked credential.
        # Obvious placeholders are allowed: a README that shows the *shape* of
        # a connection string is more useful than one that shows nothing, and
        # "user:pass" is not a credential.
        placeholder = {"user", "pass", "password", "host", "db", "dbname",
                       "yourdb", "username", "xxx", "changeme", "example"}
        for m in re.finditer(r"(?i)postgres(ql)?://([^\s:/@]+):([^\s@]+)@", README):
            if m.group(2).lower() in placeholder and m.group(3).lower() in placeholder:
                continue
            self.fail(f"README contains a credential in a URL: {m.group(0)[:40]}...")
        suspicious = [
            (r"(?i)\b(secret|password|token|api[_-]?key)\s*[:=]\s*['\"][A-Za-z0-9/+_-]{16,}",
             "a hardcoded secret"),
            (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "a private key"),
            (r"\bAKIA[0-9A-Z]{16}\b", "an AWS access key"),
        ]
        for pattern, what in suspicious:
            self.assertIsNone(re.search(pattern, README),
                              f"README appears to contain {what}")

    def test_it_does_not_tell_the_reader_to_commit_secrets(self):
        low = README.lower()
        self.assertIn("do not commit", low)

    def test_placeholders_are_obvious(self):
        # Anything that looks like a credential must be visibly a placeholder.
        for m in re.finditer(r"GAME_ADMIN_KEYS='([^']+)'", README):
            self.assertIn("<key>", m.group(1),
                          "GAME_ADMIN_KEYS example must use a placeholder")


class SingleGameTest(unittest.TestCase):
    def test_exactly_one_game_is_shipped(self):
        catalog = ROOT / "games"
        engines = [d.name for d in catalog.iterdir()
                   if d.is_dir() and (d / "engine.py").exists()]
        self.assertEqual(engines, ["teen_patti_pro"],
                         "this package ships exactly one game")

    def test_the_catalog_advertises_one_game(self):
        from games.teen_patti_pro.config import DEFAULT_CONFIG
        self.assertEqual(DEFAULT_CONFIG.seats, ("A", "B", "C"))
        self.assertEqual(DEFAULT_CONFIG.cards_per_hand, 3)

    def test_no_removed_game_appears_in_the_readme(self):
        low = README.lower()
        for gone in ("greedy monkey", "baby king", "lucky wheel"):
            self.assertNotIn(gone, low)


if __name__ == "__main__":
    unittest.main()
