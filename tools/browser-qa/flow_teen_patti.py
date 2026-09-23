"""Deterministic Teen Patti browser flow (no model calls).

Runs inside `browser-harness` stdin-exec (helpers pre-imported).
Config via env: QA_HOST, QA_API_PORT, QA_WS_PORT, QA_EVIDENCE.
Every browser action is independently verified through the API.
Evidence: JSON trace + screenshots in QA_EVIDENCE.
"""
import json
import os
import time
import urllib.request
import urllib.error

HOST = os.environ.get("QA_HOST", "127.0.0.1")
API = int(os.environ.get("QA_API_PORT", "8901"))
WS = int(os.environ.get("QA_WS_PORT", "8902"))
EVDIR = os.environ.get("QA_EVIDENCE", "/tmp/browser-qa-evidence")
# Remote (Vercel) mode: QA_BASE=https://<host> (no port, /api prefix,
# staging login instead of /qa/mint, polling transport, no WS).
QA_BASE = os.environ.get("QA_BASE", "").rstrip("/")
API_BASE = QA_BASE + "/api" if QA_BASE else f"http://{HOST}:{API}"
WEB_BASE = QA_BASE if QA_BASE else f"http://{HOST}:{API}"
ROOM = f"qa-room-{int(time.time()) % 100000}"
PLAYER = "qa-player"
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

# 1. round + session setup over HTTP
st, _ = api("POST", f"/api/v1/games/teen-patti-pro/rooms/{ROOM}/rounds/start", {}, ADMIN)
note("admin start round", st == 200, st)
if QA_BASE:
    st, body = api("POST", "/api/v1/staging/test-login",
                   {"player": PLAYER, "room": ROOM, "game": "teen-patti-pro"})
    tok = body.get("data", {}).get("launch_token", "") if st == 200 else ""
else:
    st, body = api("POST", "/qa/mint", {"player": PLAYER, "room": ROOM, "game": "teen-patti-pro"})
    tok = body.get("launch_token", "") if st == 200 else ""
note("launch token issued", bool(tok))
st, body = api("POST", "/api/v1/sessions", {"launch_token": tok})
sid = body.get("data", {}).get("session_id", "") if st == 200 else ""
note("open session", bool(sid))
AUTH = {"Authorization": f"Bearer {sid}"}
st, body = api("GET", f"/api/v1/games/teen-patti-pro/rooms/{ROOM}/wallet", headers=AUTH)
bal_before = body.get("data", {}).get("available") if st == 200 else None
note("balance before", bal_before == 20000, bal_before)

# 2. load game in browser
if QA_BASE:
    url = f"{WEB_BASE}/teen-patti-pro/?session={sid}&room={ROOM}"
else:
    url = (f"{WEB_BASE}/teen-patti-pro/?api={WEB_BASE}"
           f"&ws=ws://{HOST}:{WS}&session={sid}&room={ROOM}")
new_tab(url)
wait_for_load(15)
wait(2.0)  # no network-idle wait: WS ticks every 1s and the betting window is 20s
info = page_info()
note("page loads with game title", "Teen Patti" in info.get("title", ""), info.get("title"))
capture_screenshot(f"{EVDIR}/01-loaded.png")
note("screenshot saved", os.path.getsize(f"{EVDIR}/01-loaded.png") > 20000,
     os.path.getsize(f"{EVDIR}/01-loaded.png"))

# 3. tap seat A twice (select + bet 100) at landscape layout coords
W, H = info.get("w", 780), info.get("h", 437)
cx, top, rx = W / 2, H * 0.30, min(W * 0.36, 260)
ax, ay = cx - rx, top
click_at_xy(ax, ay)
wait(1.0)
click_at_xy(ax, ay)
wait(2.0)
st, body = api("GET", f"/api/v1/games/teen-patti-pro/history?room={ROOM}", headers=AUTH)
bets = body.get("data", {}).get("bets", []) if st == 200 else []
note("bet accepted via canvas taps", len(bets) == 1, bets)
st, body = api("GET", f"/api/v1/games/teen-patti-pro/rooms/{ROOM}/wallet", headers=AUTH)
bal_after = body.get("data", {}).get("available") if st == 200 else None
note("balance debited 100", bal_after == 19900, bal_after)

# 4. close -> result -> settle, verify each
for op in ("close", "result", "settle"):
    st, _ = api("POST", f"/api/v1/games/teen-patti-pro/rooms/{ROOM}/rounds/{op}", {}, ADMIN)
    note(f"admin {op}", st == 200, st)
st, body = api("GET", f"/api/v1/games/teen-patti-pro/history?room={ROOM}", headers=AUTH)
bets = body.get("data", {}).get("bets", []) if st == 200 else []
note("history shows settled bet", any(b.get("status") in ("won", "lost") for b in bets), bets)
st, body = api("GET", f"/api/v1/games/teen-patti-pro/rooms/{ROOM}/wallet", headers=AUTH)
bal_final = body.get("data", {}).get("available") if st == 200 else None
last_status = next((b.get("status") for b in bets), None)
if last_status == "lost":
    note("settlement kept balance (loss)", bal_final == bal_after, f"{bal_after}->{bal_final}")
else:
    note("settlement moved balance (win)", bal_final != bal_after, f"{bal_after}->{bal_final}")
capture_screenshot(f"{EVDIR}/02-result.png")

# 5. reload -> state recovery (server-side truth survives)
new_tab(url)
wait_for_load(15)
st, body = api("GET", f"/api/v1/games/teen-patti-pro/rounds/current?room={ROOM}", headers=AUTH)
note("state survives reload", st == 200 and "round_id" in body.get("data", {}))

# 6. asset network evidence (browser-reachable, correct MIME)
for p, want in [("/teen-patti-pro/master/wav/bet.wav", "audio"),
                ("/teen-patti-pro/master/wav/win.wav", "audio"),
                ("/teen-patti-pro/master/lottie/win_fireworks.json", "json"),
                ("/teen-patti-pro/assets/table-bg.svg", "svg"),
                ("/teen-patti-pro/theme.json", "json")]:
    st, ctype, _ = (lambda r: (r.status, r.headers.get("Content-Type", ""), None))(
        urllib.request.urlopen(f"{WEB_BASE}{p}", timeout=10))
    note(f"asset {p}", st == 200 and want in ctype, ctype)

with open(f"{EVDIR}/teen-patti-trace.json", "w") as f:
    json.dump(trace, f, indent=1)
failed = [t for t in trace if not t["ok"]]
print(f"FLOW {'PASS' if not failed else 'FAIL'}: {len(trace) - len(failed)}/{len(trace)}")
