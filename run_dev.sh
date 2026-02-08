#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/himanshu/Desktop/VidX"
WEB_DIR="$ROOT/webapp"
BACKEND_PORT=8010
WEB_PORT=3003

export VIDX_ALLOWED_ORIGINS="http://localhost:${WEB_PORT}"

if [ -f "$ROOT/backend/.venv/bin/activate" ]; then
  source "$ROOT/backend/.venv/bin/activate"
elif [ -f "$ROOT/.venv311/bin/activate" ]; then
  source "$ROOT/.venv311/bin/activate"
elif [ -f "$ROOT/.venv/bin/activate" ]; then
  source "$ROOT/.venv/bin/activate"
else
  echo "Backend venv not found. Expected one of:" >&2
  echo "  $ROOT/backend/.venv" >&2
  echo "  $ROOT/.venv311" >&2
  echo "  $ROOT/.venv" >&2
  exit 1
fi

python3 -m pip install -r "$ROOT/backend/requirements.txt"

uvicorn backend.app:app --reload --host 0.0.0.0 --port "$BACKEND_PORT" &
BACKEND_PID=$!

cd "$WEB_DIR"
printf "NEXT_PUBLIC_API_BASE=http://localhost:${BACKEND_PORT}\n" > .env.local
npm run dev -- --port "$WEB_PORT" &
WEB_PID=$!

trap 'kill "$BACKEND_PID" "$WEB_PID"' INT TERM

wait "$BACKEND_PID" "$WEB_PID"
