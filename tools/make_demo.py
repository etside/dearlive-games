#!/usr/bin/env python3
"""Capture a REAL engine round as an offline replay for GitHub Pages demo.

Runs the authoritative engine (fixed seed) through the full lifecycle with
three funded players, and dumps per-phase snapshots + events to
games/teen_patti_pro/client/demo_round.json. The demo page replays these
frames with a clear OFFLINE REPLAY banner — no fake data, no invented hands.
Regenerate: python3 tools/make_demo.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.wallet import MemoryWallet
from games.teen_patti_pro.config import TeenPattiConfig
from games.teen_patti_pro.service import TeenPattiService

SEED = "de" * 16
T0 = 1_750_000_000_000


def main():
    cfg = TeenPattiConfig(confirmed=True)
    w = MemoryWallet()
    for p in ("Ravi", "Priya", "Aman"):
        w.fund(p, 10000)
    svc = TeenPattiService(config=cfg, wallet=w)
    # deterministic clock
    now = [T0]
    svc._now = lambda: now[0]

    frames = []

    def snap(label):
        s = svc.state("demo-room", "Ravi")
        frames.append({"label": label, "serverTime": now[0], "snapshot": s,
                       "balances": {p: w.get_balance(p).available
                                    for p in ("Ravi", "Priya", "Aman")}})

    for p in ("Ravi", "Priya", "Aman"):
        svc.open_session(svc.tokens.mint(p, "demo-room", "teen-patti-pro").token)
    svc.rooms["demo-room"].start_round(now[0], seed_hex=SEED)
    snap("betting-open-empty")
    now[0] += 3000
    svc.place_bet("demo-room", "Ravi", "A", 500, "demo-k1")
    svc.place_bet("demo-room", "Priya", "B", 1000, "demo-k2")
    svc.place_bet("demo-room", "Aman", "A", 100, "demo-k3")
    snap("betting-open-bets")
    now[0] = T0 + cfg.guess_ms + 1
    svc.close_betting("demo-room")
    snap("betting-closed")
    res = svc.publish_result("demo-room")
    snap("result")
    stl = svc.settle("demo-room")
    snap("settled")

    room = svc.rooms["demo-room"]
    out = {
        "meta": {"note": "OFFLINE REPLAY of real authoritative engine output (seed "
                         + SEED + ", config " + cfg.version + "). Not live.",
                 "config_version": cfg.version, "seed": SEED,
                 "deck_commit": room.round.deck_commit,
                 "winners": res["winners"]},
        "frames": frames,
        "events": [{"seq": e["seq"], "kind": e["kind"]} for e in room.round.events],
        "settlements": stl["settlements"],
    }
    dest = Path(__file__).resolve().parents[1] / "games/teen_patti_pro/client/demo_round.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {dest} ({len(frames)} frames, winners={res['winners']})")


if __name__ == "__main__":
    main()
