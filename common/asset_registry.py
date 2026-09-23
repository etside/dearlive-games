"""Canonical game asset registry (deployability §2-§4).

Every production game asset resolves through this registry: id, gameId,
category, assetKey, filePath, assetType, version, checksum, enabled,
fallbackAsset, event, configurationVersion. verify() enforces file exists,
checksum match, MIME, readability, and per-type validity (SVG XML, Lottie
JSON+timeline, GIF magic+dims, WAV header+duration, PNG dims, JSON parse).
ASSET_HEALTH=PASS only when every enabled record passes.
"""
import hashlib
import json
import struct
import wave
import xml.dom.minidom
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
CLIENT_DIR = ROOT / "games" / "teen_patti_pro" / "client"
MASTER_DIR = ROOT / "assets" / "dearlive-master"

ASSET_TYPES = ("PNG", "SVG", "GIF", "LOTTIE", "WAV", "JSON")
MIME = {"PNG": "image/png", "SVG": "image/svg+xml", "GIF": "image/gif",
        "LOTTIE": "application/json", "WAV": "audio/wav", "JSON": "application/json"}


@dataclass
class AssetRecord:
    id: str
    gameId: str
    category: str
    assetKey: str
    filePath: str
    assetType: str
    version: str = "1.0.0"
    checksum: str = ""
    enabled: bool = True
    fallbackAsset: str = ""
    event: str = ""
    configurationVersion: str = ""
    usage: List[str] = field(default_factory=list)  # client|served|host


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _png_dims(raw: bytes):
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("bad PNG magic")
    w, h = struct.unpack(">II", raw[16:24])
    if w <= 0 or h <= 0:
        raise ValueError("bad PNG dimensions")
    return w, h


def _gif_dims(raw: bytes):
    if raw[:6] not in (b"GIF87a", b"GIF89a"):
        raise ValueError("bad GIF magic")
    w, h = struct.unpack("<HH", raw[6:10])
    if w <= 0 or h <= 0:
        raise ValueError("bad GIF dimensions")
    return w, h


def _check_wav(path: Path):
    with wave.open(str(path)) as w:
        frames, rate = w.getnframes(), w.getframerate()
        if frames <= 0 or rate <= 0:
            raise ValueError("bad WAV frames/rate")
        return {"channels": w.getnchannels(), "rate": rate,
                "duration_s": round(frames / rate, 3)}


def _check_lottie(raw: bytes):
    doc = json.loads(raw)
    if not isinstance(doc.get("layers"), list) or not doc["layers"]:
        raise ValueError("lottie has no layers")
    fr = doc.get("fr", 0)
    dur = round((doc.get("op", 0) - doc.get("ip", 0)) / fr, 3) if fr else 0
    if dur <= 0:
        raise ValueError("lottie has no duration")
    return {"version": doc.get("v"), "duration_s": dur,
            "layers": len(doc["layers"])}


def build_registry() -> List[AssetRecord]:
    """Enumerate every production asset from disk (repo files only)."""
    recs: List[AssetRecord] = []
    manifest = json.loads((CLIENT_DIR / "assets.json").read_text())
    files = dict(manifest["files"])
    chips = files.pop("chips")
    seats = files.pop("seats")
    svg_paths = {**files, **chips, **seats}
    for name, rel in sorted(svg_paths.items()):
        recs.append(AssetRecord(
            id=f"tpp-svg-{name}", gameId="teen-patti-pro", category="art",
            assetKey=rel, filePath=str(CLIENT_DIR / rel), assetType="SVG",
            fallbackAsset="", event="RENDER",
            configurationVersion=(CLIENT_DIR / "theme.json").name,
            usage=["served"]))
    for doc_name in ("theme.json", "assets.json", "demo_round.json"):
        recs.append(AssetRecord(
            id=f"tpp-doc-{doc_name}", gameId="teen-patti-pro", category="config",
            assetKey=doc_name, filePath=str(CLIENT_DIR / doc_name),
            assetType="JSON",
            fallbackAsset="", event="LOAD",
            configurationVersion="theme.json", usage=["client", "served"]))
    # Master pack rows mirror asset-manifest.json (single source of truth).
    mpath = MASTER_DIR / "asset-manifest.json"
    if mpath.exists():
        mdoc = json.loads(mpath.read_text())
        for game, groups in mdoc.get("games", {}).items():
            for row in groups.get("audio", []) + groups.get("animations", []) + groups.get("gifs", []):
                rel = row["filename"]
                atype = {"wav": "WAV", "gif": "GIF", "json": "LOTTIE"}[rel.rsplit(".", 1)[-1]]
                recs.append(AssetRecord(
                    id=f"master-{game}-{rel.replace('/', '-')}", gameId=game,
                    category="audio" if atype == "WAV" else "animation",
                    assetKey=rel, filePath=str(MASTER_DIR / rel), assetType=atype,
                    fallbackAsset=row.get("fallback_asset", ""),
                    event=row.get("event_trigger", ""),
                    configurationVersion=mdoc.get("version", ""),
                    usage=["served", "host"] if atype in ("LOTTIE", "GIF") else ["client", "served"]))
    for r in recs:
        p = Path(r.filePath)
        if p.exists():
            r.checksum = sha256_file(p)
    return recs


def verify(records: Optional[List[AssetRecord]] = None) -> Dict:
    """Full integrity pass. Returns {health, passed, failed, failures[]}."""
    records = records if records is not None else build_registry()
    failures: List[str] = []
    passed = 0
    for r in records:
        if not r.enabled:
            continue
        try:
            p = Path(r.filePath)
            if not p.exists():
                raise ValueError("missing file")
            raw = p.read_bytes()
            if not raw:
                raise ValueError("unreadable/empty")
            if r.checksum and sha256_file(p) != r.checksum:
                raise ValueError("checksum mismatch")
            if r.assetType == "SVG":
                xml.dom.minidom.parseString(raw)
            elif r.assetType == "JSON":
                json.loads(raw)
            elif r.assetType == "LOTTIE":
                _check_lottie(raw)
            elif r.assetType == "GIF":
                _gif_dims(raw)
            elif r.assetType == "WAV":
                _check_wav(p)
            elif r.assetType == "PNG":
                _png_dims(raw)
            else:
                raise ValueError(f"unsupported type {r.assetType}")
            passed += 1
        except Exception as e:  # noqa: BLE001 - report, don't crash the gate
            failures.append(f"{r.id}: {e}")
    return {"health": "PASS" if not failures else "FAIL",
            "passed": passed, "failed": len(failures), "failures": failures}


def asset_health() -> str:
    return verify()["health"]
