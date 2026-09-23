#!/usr/bin/env bash
# Reproducible browser-QA entry: ./tools/browser-qa/run.sh [BASE_URL]
# No arg  -> boots the local QA stack (serve_qa.py, funded players) and runs all flows.
# BASE_URL -> runs flows against a deployed staging URL (e.g. https://....vercel.app).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
EVDIR="${QA_EVIDENCE:-$ROOT/tools/browser-qa/evidence}"
mkdir -p "$EVDIR"
JEV="$HOME/tools/jev-ultrafast"
export PATH="$HOME/.local/bin:$PATH"

echo "== 1 environment =="
python3 --version
node --version
google-chrome --version | head -n 1
test -x "$JEV/.venv/bin/python" && echo "jev venv ok" || { echo "MISSING jev venv"; exit 1; }
echo "== 1b chrome (CDP :9222, chromium profile for harness discovery) =="
if ! curl -s --max-time 5 http://127.0.0.1:9222/json/version > /dev/null 2>&1; then
  mkdir -p ~/.config/chromium
  nohup google-chrome --headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage \
    --remote-debugging-port=9222 --user-data-dir=$HOME/.config/chromium about:blank \
    >"$EVDIR/chrome.log" 2>&1 &
  sleep 6
fi
curl -s --max-time 8 http://127.0.0.1:9222/json/version > /dev/null && echo "chrome CDP ok" || { echo "chrome failed"; exit 1; }
echo "== 1c redis (staging state backend) =="
if ! redis-cli ping > /dev/null 2>&1; then
  if command -v redis-server > /dev/null 2>&1; then
    redis-server --port 6379 --daemonize yes --save '' --appendonly no
    sleep 1
  fi
fi
redis-cli ping > /dev/null 2>&1 && echo "redis ok" || { echo "redis unavailable - staging tests need it"; exit 1; }

if [ $# -eq 0 ]; then
  echo "== 2 boot local QA stack =="
  export QA_HOST="${QA_HOST:-127.0.0.1}" QA_API_PORT="${QA_API_PORT:-8901}" QA_WS_PORT="${QA_WS_PORT:-8902}"
  ( python3 "$ROOT/tools/browser-qa/serve_qa.py" &>"$EVDIR/qa-server.log" & echo $! > "$EVDIR/qa-server.pid" )
  for _ in $(seq 1 30); do
    curl -s -o /dev/null "http://$QA_HOST:$QA_API_PORT/teen-patti-pro/" && break || sleep 1
  done
  curl -s -o /dev/null "http://$QA_HOST:$QA_API_PORT/teen-patti-pro/" && echo "qa api ok" || { echo "QA server failed"; exit 1; }
else
  echo "== 2 remote base: $1 =="
  export QA_BASE="$1"
  curl -s -o /dev/null "$1/teen-patti-pro/" && echo "remote web ok" || { echo "remote unreachable"; exit 1; }
fi

echo "== 3 harness doctor (daemon starts on first use; cloud auth optional) =="
( cd "$JEV" && uv run browser-harness --doctor ) | head -n 10 || true

echo "== 4 browser flows =="
export QA_EVIDENCE="$EVDIR"
( cd "$JEV" && uv run browser-harness < "$ROOT/tools/browser-qa/flow_teen_patti.py" ) | tee "$EVDIR/flow-teen.log" | tail -n 3
( cd "$JEV" && uv run browser-harness < "$ROOT/tools/browser-qa/flow_wheels.py" ) | tee "$EVDIR/flow-wheels.log" | tail -n 3

echo "== 5 API verification (suite) =="
( cd "$ROOT" && python3 -m unittest discover -s tests 2>&1 | tail -n 3 )

echo "== 6 evidence =="
ls -la "$EVDIR" | head -n 15
if [ -f "$EVDIR/qa-server.pid" ]; then kill "$(cat "$EVDIR/qa-server.pid")" 2>/dev/null || true; fi
echo DONE
