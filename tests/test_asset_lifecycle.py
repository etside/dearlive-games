"""Asset/UI lifecycle enforcement (QA auditor rule 1+2, Python form).

Every test executes real HTTP against a live stdlib game server (no mocks,
no stubs) and counts its assertions explicitly: tearDown fails the test when
the count is zero -- the unittest equivalent of expect.hasAssertions().

What is verified, for real:
  1. Every file path declared in client/assets.json is served (200) with the
     claimed content type, and every SVG parses as well-formed XML.
  2. client/theme.json is served and carries the live canvas.* skin contract
     that game.js applies at boot.
  3. The offline demo bundle (demo.html + 5-frame demo_round.json) is served
     intact -- the exact artifact the offline APK embeds.
"""
import json
import threading
import unittest
import urllib.request
import xml.dom.minidom
from http.server import ThreadingHTTPServer

CLIENT_DIR = __import__("pathlib").Path(__file__).parent.parent / "games" / "teen_patti_pro" / "client"


class CountedCase(unittest.TestCase):
    def setUp(self):
        self._n_assert = 0

    def counted(self, cond, msg=""):
        self._n_assert += 1
        self.assertTrue(cond, msg)

    def tearDown(self):
        self.assertGreater(self._n_assert, 0, f"{self._testMethodName} executed ZERO assertions")


def _load_app():
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    from games.teen_patti_pro import api as api_mod
    from games.teen_patti_pro.config import DEFAULT_CONFIG
    from games.teen_patti_pro.service import TeenPattiService
    from integrations import build_stores
    wallet, tokens, sessions, idem, _ = build_stores()
    api_mod.Handler.svc = TeenPattiService(config=DEFAULT_CONFIG, wallet=wallet, tokens=tokens,
                                           sessions=sessions, idempotency=idem)
    return api_mod.Handler


class AssetLifecycleTest(CountedCase):
    @classmethod
    def setUpClass(cls):
        handler = _load_app()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.manifest = json.loads((CLIENT_DIR / "assets.json").read_text())
        cls.theme = json.loads((CLIENT_DIR / "theme.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()

    def test_every_manifest_asset_is_served_and_well_formed(self):
        files = dict(self.manifest["files"])
        chips = files.pop("chips")
        seats = files.pop("seats")
        paths = {**files, **{f"chip-{k}": v for k, v in chips.items()},
                 **{f"seat-{k}": v for k, v in seats.items()}}
        self.counted(len(paths) == 11, f"manifest lists 11 assets, got {len(paths)}")
        for name, rel in paths.items():
            status, ctype, body = self.get(f"/teen-patti-pro/{rel}")
            self.counted(status == 200, f"{rel} served, got {status}")
            self.counted("svg" in ctype, f"{rel} content-type {ctype}")
            try:
                xml.dom.minidom.parseString(body)
                well_formed = True
            except Exception:
                well_formed = False
            self.counted(well_formed, f"{rel} is well-formed XML")

    def test_theme_contract_game_js_consumes(self):
        status, ctype, body = self.get("/teen-patti-pro/theme.json")
        self.counted(status == 200, "theme.json served")
        doc = json.loads(body)
        for key in ("feltA", "feltB", "gold"):
            self.counted(key in doc.get("canvas", {}), f"canvas.{key} present for game.js")
        src = (CLIENT_DIR / "game.js").read_text()
        for token in ("theme.json", "THEME.feltA", "THEME.feltB"):
            self.counted(token in src, f"game.js references {token}")
        for key in ("seats", "accent"):
            self.counted(key in doc.get("theme", {}), f"theme.{key} present")

    def test_demo_bundle_intact(self):
        status, _, body = self.get("/teen-patti-pro/demo.html")
        self.counted(status == 200, "demo.html served")
        self.counted(b"demo_round.json" in body, "demo.html wires demo_round.json")
        status, _, body = self.get("/teen-patti-pro/demo_round.json")
        self.counted(status == 200, "demo_round.json served")
        doc = json.loads(body)
        self.counted(len(doc["frames"]) == 5, "demo bundle has 5 frames")
        labels = [f["label"] for f in doc["frames"]]
        self.counted("result" in labels and "settled" in labels, f"bundle covers result+settled: {labels}")


if __name__ == "__main__":
    unittest.main()
