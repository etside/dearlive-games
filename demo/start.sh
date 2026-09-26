#!/usr/bin/env bash
# Teen Patti Pro — one-command local demo.
#
# Starts the API on 5002 (+ WebSocket on 5003) with zero dependencies:
# no Redis, no database, no .env, no npm install. Everything is in-memory.
#
#   ./demo/start.sh              # start, bootstrap a session, print next steps
#   PORT=5100 ./demo/start.sh    # use a different HTTP port
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-5002}"
WS_PORT="${WS_PORT:-5003}"
HOST="${HOST:-127.0.0.1}"
ROOM="${ROOM:-c-room}"
PLAYER="${PLAYER:-stranger}"
PY="${PYTHON:-python3}"

command -v "$PY" >/dev/null 2>&1 || { echo "error: '$PY' not found. Need Python 3.12+."; exit 1; }

echo "==> starting Teen Patti Pro on http://$HOST:$PORT (WebSocket $WS_PORT)"
# --confirmed is the TBC ruleset sign-off required to place bets. Demo only.
"$PY" -m games.teen_patti_pro.api \
  --host "$HOST" --port "$PORT" --ws-port "$WS_PORT" --confirmed &
API_PID=$!

STOPPED=0
cleanup() {
  [ "$STOPPED" -eq 1 ] && return
  STOPPED=1
  echo; echo "==> stopping (pid $API_PID)"
  kill "$API_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# wait for readiness
for _ in $(seq 1 40); do
  if curl -fsS "http://$HOST:$PORT/health" >/dev/null 2>&1; then break; fi
  if ! kill -0 "$API_PID" 2>/dev/null; then
    echo "error: server exited during startup" >&2; exit 1
  fi
  sleep 0.25
done

if ! curl -fsS "http://$HOST:$PORT/health" >/dev/null 2>&1; then
  echo "error: server did not become healthy on port $PORT" >&2; exit 1
fi

echo "==> healthy. bootstrapping a funded demo session..."
SESSION_JSON="$(curl -fsS "http://$HOST:$PORT/demo/session?room=$ROOM&player=$PLAYER")"

read -r SESSION_ID BALANCE ROUND_ID <<EOF
$(printf '%s' "$SESSION_JSON" | "$PY" -c '
import json,sys
d=json.load(sys.stdin).get("data",{})
st=d.get("state") or {}
print(d.get("session_id",""), d.get("balance",""), st.get("round_id",""))')
EOF

cat <<TXT

  session_id   ${SESSION_ID}
  balance      ${BALANCE}
  round        ${ROUND_ID}

  Play a round:

    # bet 100 on seat A
    curl -s -X POST http://$HOST:$PORT/api/v1/games/teen-patti-pro/rooms/$ROOM/bets \\
      -H "Authorization: Bearer $SESSION_ID" \\
      -H "Content-Type: application/json" \\
      -H "Idempotency-Key: demo-bet-1" \\
      -d '{"position":"A","amount":100}'

    # read the table
    curl -s "http://$HOST:$PORT/api/v1/games/teen-patti-pro/rounds/current?room=$ROOM" \\
      -H "Authorization: Bearer $SESSION_ID"

  Browser:
    client        http://$HOST:$PORT/teen-patti-pro/
    API docs      http://$HOST:$PORT/docs
    OpenAPI spec  http://$HOST:$PORT/openapi.json

  Press Ctrl-C to stop.
TXT

wait "$API_PID"
