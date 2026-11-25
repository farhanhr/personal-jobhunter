#!/usr/bin/env bash
# Start Personal JobHunter: install dependencies on first run, then run the
# FastAPI backend and the Vite frontend together. Press Ctrl+C to stop both.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  trap - INT TERM
  for pid in "$FRONTEND_PID" "$BACKEND_PID"; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  wait "$FRONTEND_PID" 2>/dev/null || true
  wait "$BACKEND_PID" 2>/dev/null || true
}

trap cleanup INT TERM

if ! command -v uv >/dev/null 2>&1; then
  echo "Personal JobHunter needs uv (Python package manager)."
  echo "Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "Then run ./run.sh again."
  exit 1
fi

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  echo "Personal JobHunter needs Node.js 20.19+ (or 22.12+) and npm."
  echo "Download it from: https://nodejs.org/en/download"
  echo "Then run ./run.sh again."
  exit 1
fi

echo "Preparing backend dependencies…"
(cd "$ROOT_DIR/backend" && uv sync)

echo "Preparing frontend dependencies…"
(cd "$ROOT_DIR/frontend" && npm install --no-audit --no-fund)

echo "Starting Personal JobHunter…"
(cd "$ROOT_DIR/backend" && exec uv run job-hunter start) &
BACKEND_PID=$!

(cd "$ROOT_DIR/frontend" && exec npm run dev) &
FRONTEND_PID=$!

cat <<'BANNER'

===================================================
Personal JobHunter is running!
URL: http://localhost:5173
Backend API: http://127.0.0.1:8000
Press Ctrl+C to stop both servers.
===================================================

BANNER

STATUS=0
wait "$BACKEND_PID" "$FRONTEND_PID" || STATUS=$?
cleanup
exit "$STATUS"
