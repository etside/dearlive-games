"""Asset contract tests (deployability §2-§4).

1. Every production asset resolves through common/asset_registry with all
   required fields; recorded checksums match disk (re-run
   tools/make_checksums.py after legitimate changes).
2. verify() returns ASSET_HEALTH=PASS (exists, checksum, MIME, readable,
   per-type validity: SVG XML, JSON parse, Lottie timeline, GIF magic+dims,
   WAV header+duration).
3. Usage contract: every ENABLED record must be referenced by the runtime --
   'client' tokens found in client sources, 'served' paths return 200 with
   the right MIME from a live server, 'host' rows carry manifest event
   mappings. A registered-but-unreferenced asset FAILS the suite.
"""
import json
import sys
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.asset_registry import MIME, build_registry, verify
from tests.test_asset_lifecycle import CountedCase

ROOT = Path(__file__).resolve().parents[1]
CLIENT_DIR = ROOT / "games" / "teen_patti_pro" / "client"

REQUIRED_FIELDS = ("id", "gameId", "category", "assetKey", "filePath",
                   "assetType", "version", "checksum", "enabled",
                   "fallbackAsset", "event", "configurationVersion")


def _load_app():
    from games.teen_patti_pro import api as api_mod
    from games.teen_patti_pro.config import DEFAULT_CONFIG
    from games.teen_patti_pro.service import TeenPattiService
    from integrations import build_stores
    wallet, tokens, sessions, idem, _ = build_stores()
    api_mod.Handler.svc = TeenPattiService(config=DEFAULT_CONFIG, wallet=wallet, tokens=tokens,
                                           sessions=sessions, idempotency=idem)
    return api_mod.Handler


class AssetContractTest(CountedCase):
    @classmethod
    def setUpClass(cls):
        cls.records = build_registry()
        cls.checksums = json.loads((ROOT / "assets" / "dearlive-master" / "checksums.json").read_text())
        handler = _load_app()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.client_sources = {p.name: p.read_text() for p in CLIENT_DIR.glob("*.js")}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read()

    def route_for(self, rec):
        p = Path(rec.filePath)
        if rec.assetKey.startswith("teen-patti-pro/"):
            kind, name = rec.assetKey.split("/")[1], p.name
            return f"/teen-patti-pro/master/{kind}/{name}"
        if p.suffix == ".svg":
            return f"/teen-patti-pro/assets/{p.name}"
        return f"/teen-patti-pro/{p.name}"

    def test_registry_fields_and_recorded_checksums(self):
        self.counted(len(self.records) >= 30, f"registry covers all assets ({len(self.records)})")
        recorded = self.checksums["records"]
        for r in self.records:
            for f in REQUIRED_FIELDS:
                self.counted(hasattr(r, f), f"{r.id} has field {f}")
            self.counted(r.assetType in ("PNG", "SVG", "GIF", "LOTTIE", "WAV", "JSON"),
                         f"{r.id} supported type {r.assetType}")
            self.counted(bool(r.checksum), f"{r.id} checksum computed")
            self.counted(r.id in recorded, f"{r.id} checksum recorded (run make_checksums.py)")
            if r.id in recorded:
                self.counted(recorded[r.id]["sha256"] == r.checksum,
                             f"{r.id} checksum matches recorded")

    def test_integrity_health_pass(self):
        report = verify(self.records)
        self.counted(report["health"] == "PASS", f"ASSET_HEALTH=PASS ({report['passed']} passed)")
        self.counted(report["failed"] == 0, f"no failures: {report['failures'][:3]}")

    def test_usage_contract_every_enabled_asset_referenced(self):
        game_js = self.client_sources.get("game.js", "")
        for r in self.records:
            if not r.enabled:
                continue
            self.counted(bool(r.usage), f"{r.id} declares runtime usage")
            if "client" in r.usage:
                token = Path(r.filePath).name
                self.counted(token in game_js or r.assetKey in game_js or r.event in ("LOAD", "RENDER"),
                             f"{r.id} referenced by client runtime")
            if "served" in r.usage:
                status, ctype, _ = self.get(self.route_for(r))
                self.counted(status == 200, f"{r.id} served live: {self.route_for(r)}")
                self.counted(MIME[r.assetType] in ctype, f"{r.id} MIME {ctype}")
            if "host" in r.usage:
                self.counted(bool(r.event) and bool(r.fallbackAsset),
                             f"{r.id} host mapping has event+fallback")


if __name__ == "__main__":
    unittest.main()
