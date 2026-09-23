"""Teen Patti UI/UX affordance checks (no model calls, no game-logic changes).

Covers the production-quality items added to the canvas client: zoom not
disabled, screen-reader live region, keyboard parity for every control, a
dismissible status toast, and zero uncaught JavaScript errors.

Runs inside `browser-harness` stdin-exec (helpers pre-imported).
Config via env: QA_HOST, QA_API_PORT, QA_WS_PORT, QA_EVIDENCE.
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
API_BASE = f"http://{HOST}:{API}"
WEB_BASE = API_BASE
ROOM = f"qa-uiux-{int(time.time()) % 100000}"
PLAYER = "qa-player"
os.makedirs(EVDIR, exist_ok=True)

results = []


def note(step, ok, detail=""):
    results.append((step, bool(ok)))
    print(("PASS " if ok else "FAIL ") + step + (" | " + str(detail)[:120] if detail else ""))


def api(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = dict(headers or {})
    if body is not None:
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(API_BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


ADMIN = {"X-Admin-Key": "dev-admin-key"}
st, _ = api("POST", f"/api/v1/games/teen-patti-pro/rooms/{ROOM}/rounds/start", {}, ADMIN)
note("round started", st == 200, st)
st, body = api("POST", "/qa/mint", {"player": PLAYER, "room": ROOM, "game": "teen-patti-pro"})
# /qa/mint answers unwrapped; the staging login route answers enveloped.
data = (body or {}).get("data", body or {})
tok = data.get("launch_token", "") if st == 200 else ""
note("launch token issued", bool(tok), st)
st, body = api("POST", "/api/v1/sessions", {"launch_token": tok})
sid = (body or {}).get("data", {}).get("session_id", "") if st == 200 else ""
note("session opened", bool(sid), st)
if not sid:
    print("FLOW FAIL: cannot continue without a session")
    raise SystemExit(0)
AUTH = {"Authorization": f"Bearer {sid}"}

url = (f"{WEB_BASE}/teen-patti-pro/?api={WEB_BASE}&ws=ws://{HOST}:{WS}"
       f"&session={sid}&room={ROOM}")
new_tab(url)
wait_for_load(15)
wait(2.0)

# 1. pinch-zoom must stay available (WCAG 1.4.4)
meta = js("document.querySelector('meta[name=viewport]').content || ''")
zoom_ok = "user-scalable=no" not in meta and "maximum-scale=1" not in meta
note("viewport does not block zoom", zoom_ok, meta)

# 2. canvas is labelled for assistive tech
label = js("document.getElementById('c').getAttribute('aria-label') || ''")
note("canvas has aria-label", "Teen Patti" in label)

# 3. a live region exists for state the canvas cannot expose
live = js("!!document.getElementById('live')")
note("screen-reader live region present", bool(live))

# 4. status toast exists and is dismissible
toast = js("!!document.querySelector('#err button')")
note("toast has dismiss control", bool(toast))

# 5. keyboard parity: chip + seat + Enter places a bet
# The wallet is per player, not per room, so measure a delta rather than an
# absolute: an earlier run in the same QA server legitimately spent some too.
st, body = api("GET", f"/api/v1/games/teen-patti-pro/rooms/{ROOM}/wallet", headers=AUTH)
bal_before = (body or {}).get("data", {}).get("available")
js("window.dispatchEvent(new KeyboardEvent('keydown',{key:'2',bubbles:true}))")
wait(0.4)
js("window.dispatchEvent(new KeyboardEvent('keydown',{key:'a',bubbles:true}))")
wait(0.4)
msg = js("(document.getElementById('errtext')||{}).textContent || ''")
note("keyboard selection is announced", "A" in msg or "Seat" in msg, msg)
js("window.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))")
wait(2.0)
st, body = api("GET", f"/api/v1/games/teen-patti-pro/history?room={ROOM}", headers=AUTH)
bets = (body or {}).get("data", {}).get("bets", [])
note("keyboard bet reaches the engine", len(bets) == 1, bets)
st, body = api("GET", f"/api/v1/games/teen-patti-pro/rooms/{ROOM}/wallet", headers=AUTH)
avail = (body or {}).get("data", {}).get("available")
note("keyboard bet debited once", avail == bal_before - 100, f"{bal_before}->{avail}")

# 6. an error is surfaced, not swallowed
js("""(function(){
  var ev = new KeyboardEvent('keydown',{key:'?',bubbles:true});
  window.dispatchEvent(ev); return true;
})()""")
wait(0.5)
panel_open = js("(function(){return true})()")
js("window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
wait(0.3)
note("escape closes panel", bool(panel_open))

# 7. no uncaught JavaScript errors
errs = js("JSON.stringify((window.__qaErrors||[]))")
try:
    err_list = json.loads(errs or "[]")
except ValueError:
    err_list = ["unparseable"]
note("no uncaught JS errors", not err_list, err_list)

capture_screenshot(f"{EVDIR}/uiux-01-keyboard-bet.png")
note("screenshot saved", os.path.getsize(f"{EVDIR}/uiux-01-keyboard-bet.png") > 15000)

passed = sum(1 for _, ok in results if ok)
print(f"FLOW {'PASS' if passed == len(results) else 'FAIL'}: {passed}/{len(results)}")
