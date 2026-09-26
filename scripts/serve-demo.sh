#!/usr/bin/env bash
# Zero-dependency local demo. No Postgres, no Redis, no provider keys.
#
# This is the "Try It in 60 Seconds" path from README.md. It is the only script
# here that needs nothing configured: every store is in-memory and the process
# refuses to touch a real database even if DATABASE_URL is set in your shell.
set -euo pipefail

cd "$(dirname "$0")/.."

PORT="${GAME_DEMO_PORT:-8000}"
WS_PORT="${GAME_DEMO_WS_PORT:-8001}"
HOST="${GAME_DEMO_HOST:-127.0.0.1}"

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
  done
fi
if [ -z "$PY" ]; then
  echo "No python3 on PATH. Install Python 3.12 or newer." >&2
  exit 1
fi

# 3.12 is the floor in pyproject.toml. Fail here with a clear message rather
# than three pages of traceback from a syntax error later.
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
  echo "Python 3.12+ required, found $("$PY" -V 2>&1)." >&2
  exit 1
fi

# Dependencies are optional for the demo: the service uses only the standard
# library plus the package itself. Install them only if the import fails, so a
# clean checkout does not need a network round trip.
if ! "$PY" -c 'import games.teen_patti_pro.api' >/dev/null 2>&1; then
  echo "Installing dependencies..."
  "$PY" -m pip install --disable-pip-version-check -q -r requirements.txt
fi

cat <<BANNER
  Teen Patti Pro -- zero-dependency demo

  Game      http://${HOST}:${PORT}/teen-patti-pro
  Lobby     http://${HOST}:${PORT}/teen-patti-pro/lobby.html
  Rules     http://${HOST}:${PORT}/teen-patti-pro/how-to-play.html
  Health    http://${HOST}:${PORT}/health
  WebSocket ws://${HOST}:${WS_PORT}

  In-memory only. Nothing is written anywhere. Ctrl-C to stop.
BANNER

# --demo forces the in-memory stores, defaults the port to 8000, and marks the
# rules confirmed so the demo actually deals cards.
exec "$PY" -m games.teen_patti_pro.api --demo --host "$HOST" --port "$PORT" --ws-port "$WS_PORT"
