#!/usr/bin/env bash
# Boots the FastAPI backend on :8000 and the Next.js dashboard on :3000.
# Ctrl-C stops both (and their grandchildren — next dev spawns workers).

set -euo pipefail
set -m  # job control → each backgrounded child gets its own pgid, so
        # kill -- -PID targets the whole subtree, not just the shell parent.
cd "$(dirname "$0")"

if [[ ! -f .venv/bin/activate ]]; then
  echo "error: .venv not found. run: python -m venv .venv && source .venv/bin/activate && pip install -e ."
  exit 1
fi

if [[ ! -d web/node_modules ]]; then
  echo "error: web/node_modules not found. run: cd web && npm install"
  exit 1
fi

mkdir -p .logs
# shellcheck disable=SC1091
source .venv/bin/activate

echo "starting backend on :8000 (log: .logs/backend.log)"
uvicorn arb.api.server:app --port 8000 --host 127.0.0.1 --reload --reload-dir arb >.logs/backend.log 2>&1 &
BACKEND_PID=$!

echo "starting frontend on :3000 (log: .logs/frontend.log)"
(cd web && NEXT_PUBLIC_API_BASE=http://localhost:8000 npm run dev >../.logs/frontend.log 2>&1) &
FRONTEND_PID=$!

cleanup() {
  trap - EXIT INT TERM
  printf "\nstopping...\n"
  # Target by name — covers uvicorn reloader + worker and next-dev workers
  # even when they're not direct children of $BACKEND_PID/$FRONTEND_PID.
  pkill -TERM -f "uvicorn arb.api" 2>/dev/null || true
  pkill -TERM -f "next dev" 2>/dev/null || true
  pkill -TERM -f "next-server" 2>/dev/null || true
  kill -TERM "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  sleep 1
  # Escalate anything still alive.
  pkill -KILL -f "uvicorn arb.api" 2>/dev/null || true
  pkill -KILL -f "next dev" 2>/dev/null || true
  pkill -KILL -f "next-server" 2>/dev/null || true
  kill -KILL "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  # Belt and braces: anything still bound to the ports.
  lsof -ti:8000 2>/dev/null | xargs kill -9 2>/dev/null || true
  lsof -ti:3000 2>/dev/null | xargs kill -9 2>/dev/null || true
  echo "stopped."
  exit 0
}
trap cleanup INT TERM
trap cleanup EXIT

sleep 2
echo ""
echo "  backend:   http://localhost:8000/health  (also: /snapshot, /plan/current)"
echo "  dashboard: http://localhost:3000"
echo "  replan:    http://localhost:3000/replan"
echo ""
echo "tail -f .logs/backend.log .logs/frontend.log  # for logs"
echo "Ctrl-C to stop"
echo ""

wait
