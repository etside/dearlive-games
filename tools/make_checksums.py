#!/usr/bin/env python3
"""Record expected SHA256 checksums for every registry asset.

Run after adding/changing a production asset:
    python3 tools/make_checksums.py
Commits to assets/dearlive-master/checksums.json. CI fails when a file is
added/changed without re-recording (see tests/test_asset_contract.py).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.asset_registry import build_registry

out = {"version": "1.0.0",
       "records": {r.id: {"filePath": r.filePath, "sha256": r.checksum,
                          "assetType": r.assetType, "gameId": r.gameId}
                   for r in build_registry() if r.enabled}}
dest = Path(__file__).resolve().parents[1] / "assets" / "dearlive-master" / "checksums.json"
dest.write_text(json.dumps(out, indent=1) + "\n")
print(f"recorded {len(out['records'])} checksums -> {dest}")
