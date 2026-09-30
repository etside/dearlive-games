#!/usr/bin/env python3
"""Print the round snapshot exactly as the client receives it.

The client is a black box that renders from this payload, so when the table
comes up empty the first question is which keys the server actually sends and
which of them are null. This prints the raw response and then checks the
specific fields the renderer reads, so a mismatch is a line of output rather
than something to infer from a blank screen.

Usage:
    scripts/diag_snapshot.py [token] [--base URL] [--room ROOM]
    scripts/diag_snapshot.py --mint            # mint a demo session first

With --mint it walks the same launch path a browser does, so it needs no
arguments and no operator key.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "https://api.ura-dhura.com"
DEFAULT_ROOM = "teen-patti-low"

# What the renderer reads, and what it must therefore be. Kept as data so a
# new field in the draw path has to be added here to be checked.
REQUIRED = {
    "round_id": "identifies the round; the client gates its veil on this",
    "status": "BETTING_OPEN drives the timer and the bet button",
    "balance": "drawn in the bottom-left pill",
    "betting_end_at": "the countdown is computed from this",
    "seatOccupancy": "seat -> player id; the chairs read this",
    "members": "the per-seat occupant label reads this",
    "mySeat": "marks which chair is yours",
    "hands": "three cards per position",
    "pots": "per-position pot, drawn on each betting panel",
    "denoms": "the chips the bar may offer",
    "availableSeats": "free seats",
    "isSpectator": "true when the table is full",
}


def get(url: str, token: str = "", timeout: int = 20):
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        **({"Authorization": "Bearer " + token} if token else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except Exception:
            return e.code, None
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def mint(base: str, room: str) -> str:
    """Walk the demo launch the way a browser does, and return the token.

    urllib follows the 302, so the body is the game page's HTML and carries no
    session. The token is in the final URL. The soak harness hit exactly this
    and could never join anybody.
    """
    try:
        with urllib.request.urlopen(
                base + f"/teen-patti-pro?operator=demo&room={room}",
                timeout=20) as r:
            url = r.geturl()
    except urllib.error.HTTPError as e:
        url = getattr(e, "url", "") or ""
    except Exception:
        return ""
    if "session=" in url:
        return url.split("session=")[1].split("&")[0]
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("token", nargs="?", default="")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--room", default=DEFAULT_ROOM)
    ap.add_argument("--mint", action="store_true",
                    help="mint a fresh demo session first")
    args = ap.parse_args()

    token = args.token
    if args.mint or not token:
        sys.stderr.write("minting a demo session...\n")
        token = mint(args.base, args.room)
        if not token:
            print("could not mint a session; pass one as an argument")
            return 2
        sys.stderr.write("minted\n")

    url = (f"{args.base}/api/v1/games/teen-patti-pro/rounds/current"
           f"?room={args.room}")
    status, body = get(url, token)
    print("=" * 70)
    print(f"GET {url}")
    print(f"HTTP {status}")
    print("=" * 70)
    if not isinstance(body, dict):
        print("no JSON body")
        return 1
    print(json.dumps(body, indent=2)[:4000])

    data = body.get("data") if isinstance(body.get("data"), dict) else None
    if data is None:
        print("\nno data envelope; the client cannot render from this")
        return 1

    print("\n" + "=" * 70)
    print("fields the renderer reads")
    print("=" * 70)
    missing = []
    for field, why in REQUIRED.items():
        present = field in data
        value = data.get(field)
        empty = value is None or value == {} or value == [] or value == ""
        mark = "ok     " if present and not empty else "EMPTY  "
        if not present:
            missing.append(field)
        elif empty:
            missing.append(field + " (null)")
        print(f"  {mark} {field:18s} {why}")
        if present and not empty:
            text = json.dumps(value)
            print(f"           {text[:100]}")

    print()
    if missing:
        print("NOT USABLE BY THE RENDERER:")
        for m in missing:
            print(f"  - {m}")
        return 1
    print("every field the renderer reads is present and non-empty")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
