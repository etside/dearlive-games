#!/usr/bin/env python3
"""Staging integration example — DearLive developer copy/paste.

Reads GAMES_BASE_URL + ADMIN key + a launch token minted by DearLive's
backend, then runs: session → balance → state → bet → close → result →
settle → wallet → reconnect, printing each step. No hardcoded hosts or
users; everything comes from env/argv.

Usage:
  GAMES_BASE_URL=https://games.<domain> GAME_ADMIN_KEY=<key> \\
    python3 tools/integration_example.py '<launch_token>' [room]
"""
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sdk"))
from python_client import DearLiveGameClient  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    token = sys.argv[1]
    room = sys.argv[2] if len(sys.argv) > 2 else "default"
    base = os.environ.get("GAMES_BASE_URL", "http://127.0.0.1:5002")
    admin = os.environ.get("GAME_ADMIN_KEY", "")
    c = DearLiveGameClient(base, room=room, admin_key=admin)
    print("session:", json.dumps(c.open_session(token)))
    print("balance:", json.dumps(c.wallet()))
    c.round_start()
    print("state:", json.dumps(c.state())[:300])
    print("bet:", json.dumps(c.place_bet("A", 100, uuid.uuid4().hex)))
    c.round_close()
    print("result:", json.dumps(c.round_result())[:300])
    print("settle:", json.dumps(c.round_settle())[:300])
    print("final balance:", json.dumps(c.wallet()))
    print("reconnect:", json.dumps(c.reconnect(0))["snapshot"]["status"] if isinstance(
        c.reconnect(0), dict) else "ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
