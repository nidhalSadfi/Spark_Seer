#!/usr/bin/env bash
# Run EmberGuard locally: API on :8000 + dashboard on :5173.
#
#   ./run-world.sh db       full app from PostgreSQL (.env): the approved dataset (the world since
#                           the 2026-11-15 cut-over), live map, reports, Processing Center.
#   ./run-world.sh          world history from backend/data/world/harmonized.parquet only, no
#                           database (live map, reports and Processing Center show "unavailable").
#
# Ctrl+C stops both servers. Logs: /tmp/emberguard-api.log, /tmp/emberguard-web.log
set -euo pipefail

MODE="${1:-world}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_PORT=8000
WEB_PORT=5173
export PATH="$HOME/.local/node-v22.23.5-linux-x64/bin:$PATH"   # Vite 8 needs Node >= 20.19

# 1. Free the ports (old API / Vite servers, including the stray :8001 / :5174 ones).
for port in 8000 8001 5173 5174; do
  pids=$(ss -ltnpH "sport = :$port" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u || true)
  if [ -n "$pids" ]; then echo "stopping old server on :$port (pid $pids)"; kill $pids 2>/dev/null || true; fi
done
sleep 1

# 2. API.
cd "$ROOT/backend"
# shellcheck disable=SC1091
source .venv/bin/activate
if [ "$MODE" = "db" ]; then
  set -a; . "$ROOT/.env"; set +a                       # DATABASE_URL etc.
  echo "API mode: database (approved dataset + live features)"
  uvicorn api.main:app --port "$API_PORT" > /tmp/emberguard-api.log 2>&1 &
else
  [ -f data/world/harmonized.parquet ] || { echo "missing backend/data/world/harmonized.parquet (run scripts/build_world.py first)"; exit 1; }
  echo "API mode: world history (parquet, no database)"
  env -u DATABASE_URL EMBER_DATA=data/world/harmonized.parquet \
    uvicorn api.main:app --port "$API_PORT" > /tmp/emberguard-api.log 2>&1 &
fi
API_PID=$!
trap 'echo; echo "stopping..."; kill $API_PID ${WEB_PID:-} 2>/dev/null || true; exit 0' INT TERM

echo -n "waiting for the API"
for _ in $(seq 1 60); do
  if curl -s -m 2 "http://localhost:$API_PORT/health" > /dev/null; then break; fi
  kill -0 $API_PID 2>/dev/null || { echo; echo "API failed to start:"; tail -20 /tmp/emberguard-api.log; exit 1; }
  echo -n "."; sleep 1
done
echo
echo -n "warming up the data (first load of the world file takes a few seconds)... "
curl -s -m 120 "http://localhost:$API_PORT/calendar?bbox=148,-38,154,-28&start=2019&end=2019" > /dev/null || true
curl -s "http://localhost:$API_PORT/health"; echo

# 3. Dashboard, pointed at this API (Vite reads VITE_API_URL at start-up only).
cd "$ROOT/frontend"
[ -d node_modules ] || npm install --no-audit --no-fund
VITE_API_URL="http://localhost:$API_PORT" npx vite --port "$WEB_PORT" --strictPort > /tmp/emberguard-web.log 2>&1 &
WEB_PID=$!
sleep 3

echo
echo "  Dashboard:  http://localhost:$WEB_PORT/dashboard"
echo "  API docs:   http://localhost:$API_PORT/docs"
[ "$MODE" = "db" ] || echo "  Try the calendar with a drawn box, e.g. Australia, the Amazon or Central Africa."
echo "  Ctrl+C to stop both."
wait
