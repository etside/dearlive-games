#!/usr/bin/env bash
# First-run setup for an integrator (the DearLive app developer).
#
# Checks the toolchain, authenticates the gh CLI for git operations, installs
# Python dependencies, and seeds .env from the template. It never writes a
# secret and never contacts the database.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "=== DearLive Games — Dev Setup ==="
echo ""

# --- gh CLI -----------------------------------------------------------------
if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: the gh CLI is not installed." >&2
  echo "  Install: https://cli.github.com/" >&2
  exit 1
fi
echo "[ok] gh CLI present"

if gh auth status >/dev/null 2>&1; then
  ACCOUNT=$(gh api user --jq .login 2>/dev/null || echo "unknown")
  echo "[ok] gh authenticated as $ACCOUNT"
else
  echo "==> gh is not authenticated; starting the browser flow."
  gh auth login --hostname github.com --git-protocol https --web
  gh auth setup-git
  echo "[ok] gh authenticated"
fi

# Route git through the gh credential helper so pushes need no PAT.
gh auth setup-git >/dev/null 2>&1 || true
echo "[ok] git credential helper configured"

# --- remotes ----------------------------------------------------------------
# The upstream remote is named 'deploy' in this repository. Accept 'origin'
# too so this script works unchanged in a fork.
REMOTE=""
for candidate in deploy origin upstream; do
  if git remote get-url "$candidate" >/dev/null 2>&1; then
    REMOTE="$candidate"
    break
  fi
done
if [ -z "$REMOTE" ]; then
  echo "ERROR: no git remote configured." >&2
  echo "  Add one:  git remote add deploy <repo-url>" >&2
  exit 1
fi
echo "[ok] remote '$REMOTE' -> $(git remote get-url "$REMOTE")"

# --- python -----------------------------------------------------------------
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "ERROR: $PY not found" >&2; exit 1; }
echo "[ok] $($PY --version)"

# --- dependencies -----------------------------------------------------------
# The server itself is standard-library only. requirements.txt pulls bcrypt
# (PIN verification) and psycopg (database-backed admin routes).
if "$PY" -m pip install -q -r requirements.txt 2>/dev/null; then
  echo "[ok] dependencies installed"
else
  echo "[warn] pip install failed; the zero-dependency demo still works."
  echo "       Admin database routes will report the driver as missing."
fi

# --- .env -------------------------------------------------------------------
if [ -f .env ]; then
  echo "[ok] .env already exists (left untouched)"
else
  cp .env.example .env
  chmod 600 .env
  echo "[ok] created .env from .env.example (mode 600)"
  echo "     Fill in the required values — see docs/DEPLOYMENT.md"
fi

# --- sanity -----------------------------------------------------------------
echo ""
echo "=== Setup complete ==="
echo "  1. Edit .env            (required values listed in docs/DEPLOYMENT.md)"
echo "  2. Apply the migration  bash scripts/apply-migration.sh   (needs DATABASE_URL)"
echo "  3. Zero-dep demo        python3 -m games.teen_patti_pro.api --confirmed"
echo "  4. Staging stack        python3 -m staging.wsgi            (needs Redis)"
echo "  5. Health check         curl localhost:8000/api/v1/health"
echo ""
echo "No secret was written by this script and none is ever committed."
