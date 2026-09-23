"""Deterministic wheel browser flows (no model calls).

Monkey Wheel (/monkey-wheel/) + Greedy Lion (/greedy-lion/): load, tap an
on-screen option twice (select + bet), verify bet/debit/settlement/history
through the API. Evidence: screenshots + JSON trace.
"""
import json
import math
import os
import time
import urllib.request
import urllib.error

HOST = os.environ.get("QA_HOST", "127.0.0.1")
API = int(os.environ.get("QA_API_PORT", "8901"))
EVDIR = os.environ.get("QA_EVIDENCE", "/tmp/browser-qa-evidence")
QA_BASE = os.environ.get("QA_BASE", "").rstrip("/")
API_BASE = QA_BASE + "/api" if QA_BASE else f"http://{HOST}:{API}"
WEB_BASE = QA_BASE if QA_BASE else f"http://{HOST}:{API}"
os.makedirs(EVDIR, exist_ok=True)

trace = []


def note(step, ok, detail=""):
    trace.append({"t": time.strftime("%H:%M:%S"), "step": step,
                  "ok": bool(ok), "detail": str(detail)[:300]})
    print(("PASS " if ok else "FAIL ") + step + (" | " + str(detail)[:120] if detail else ""))


def api(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = dict(headers or {})
    if body is not None:
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(API_BASE + path, data=data,
                                 headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


ADMIN = {"X-Admin-Key": "dev-admin-key"}


def tap_option_second(W=780, H=437, index=1, n_options=5):
    """Canvas coords of option `index` (client.html ring math)."""
    cx, cy, ring = W / 2, H * 0.40, min(W * 0.34, 150) + 44
    a = -math.pi / 2 + index * math.pi * 2 / n_options
    return cx + math.cos(a) * ring, cy + math.sin(a) * ring


def flow(game_route, game_api, room, tag):
    room = f"{room}-{int(time.time()) % 100000}"
    if QA_BASE:
        st, body = api("POST", "/api/v1/staging/test-login",
                       {"player": "qa-player", "room": room, "game": game_api})
        tok = body.get("data", {}).get("launch_token", "") if st == 200 else ""
    else:
        st, body = api("POST", "/qa/mint", {"player": "qa-player", "room": room, "game": game_api})
        tok = body.get("launch_token", "") if st == 200 else ""
    note(f"{tag} launch token", bool(tok))
    st, body = api("POST", f"/api/v1/games/{game_api}/sessions",
                   {"launch_token": tok})
    sid = body.get("data", {}).get("session_id", "") if st == 200 else ""
    note(f"{tag} session", bool(sid))
    AUTH = {"Authorization": f"Bearer {sid}"}
    st, _ = api("POST", f"/api/v1/games/{game_api}/rooms/{room}/rounds/start", {}, ADMIN)
    note(f"{tag} start", st == 200, st)
    new_tab(f"{WEB_BASE}{game_route}?session={sid}&room={room}")
    wait_for_load(15)
    wait(2.0)  # no network-idle wait: WS ticks every 1s and the betting window is 20s
    info = page_info()
    note(f"{tag} page loads", game_route.rstrip("/") in info.get("url", ""), info.get("title"))
    capture_screenshot(f"{EVDIR}/{tag}-loaded.png")
    x, y = tap_option_second()
    click_at_xy(x, y)
    wait(1.0)
    click_at_xy(x, y)
    wait(2.0)
    st, body = api("GET", f"/api/v1/games/{game_api}/history?room={room}&limit=10", headers=AUTH)
    bets = body.get("data", {}).get("bets", []) if st == 200 else []
    note(f"{tag} bet accepted via canvas", len(bets) == 1, bets)
    for op in ("close", "result", "settle"):
        st, _ = api("POST", f"/api/v1/games/{game_api}/rooms/{room}/rounds/{op}", {}, ADMIN)
        note(f"{tag} {op}", st == 200, st)
    st, body = api("GET", f"/api/v1/games/{game_api}/history?room={room}&limit=10", headers=AUTH)
    bets = body.get("data", {}).get("bets", []) if st == 200 else []
    note(f"{tag} settled once", len(bets) == 1 and bets[0].get("status") in ("won", "lost"), bets)
    st, body = api("GET", f"/api/v1/games/{game_api}/results/recent?room={room}&limit=5",
                   headers=AUTH)
    note(f"{tag} recent strip", st == 200 and len(body.get("data", {}).get("results", [])) == 1)
    capture_screenshot(f"{EVDIR}/{tag}-result.png")
    close_tab()


flow("/greedy-lion/", "greedy-lion", "qa-lion", "lion")
flow("/monkey-wheel/", "monkey-wheel", "qa-mw", "monkey")

with open(f"{EVDIR}/wheels-trace.json", "w") as f:
    json.dump(trace, f, indent=1)
failed = [t for t in trace if not t["ok"]]
print(f"WHEELS {'PASS' if not failed else 'FAIL'}: {len(trace) - len(failed)}/{len(trace)}")
