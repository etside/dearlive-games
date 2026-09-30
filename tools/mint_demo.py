#!/usr/bin/env python3
"""Mint demo launch URLs directly via admin API.

Reads ADMIN_KEY from .env, mints 3 demo sessions, prints play URLs.
"""

import json
import os
import sys
import urllib.request
import urllib.error

def load_env(path=".env"):
    """Load key=value pairs from .env into os.environ (idempotent)."""
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

def main():
    load_env(".env")
    admin_key = os.environ.get("ADMIN_KEY") or os.environ.get("GAME_ADMIN_KEYS", "").split(":")[0]
    if not admin_key:
        print("ERROR: ADMIN_KEY not found in .env", file=sys.stderr)
        return 1

    base = "http://127.0.0.1:5002"
    room = "teen-patti-low"
    players = ["demo_001", "demo_002", "demo_003"]

    for player_id in players:
        body = json.dumps({
            "player_id": player_id,
            "room": "teen-patti-low",
            "currency": "COIN",
            "language": "en",
            "ttl_hours": 24,
        }).encode()

        req = urllib.request.Request(
            "http://127.0.0.1:5002/api/v1/admin/sessions/mint",
            data=json.dumps({"player_id": player_id, "room": "teen-patti-low", "ttl_hours": 24}).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Admin-Key": os.environ.get("ADMIN_KEY", "89893f6c0094e1b56eae94572aa6045391718cdb1c954a66518beb44463bfc50"),
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())["data"]
                print(f"{player_id} | {data['play_url']}")
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            print(f"{player_id} | ERROR {e.code}: {body}", file=sys.stderr)
        except Exception as e:
            print(f"{player_id} | ERROR: {e}", file=sys.stderr)

    return 0

if __name__ == "__main__":
    sys.exit(main())