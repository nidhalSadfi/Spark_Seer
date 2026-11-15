#!/usr/bin/env bash
# Run the whole project locally in one command: the API on :8000 (which also starts the fire incident
# agents on :8765 for the Social Alerts page) and the dashboard on :5173.
#
#   ./run-world.sh db       full app from PostgreSQL (.env): starts the Postgres container, applies the
#                           migrations, then serves the approved dataset (the world since the 2026-11-15
#                           cut-over), live map, reports, Processing Center and the agents.
#   ./run-world.sh          no database: the world history from backend/data/world/harmonized.parquet if
#                           that file exists (it is built from multi-GB FIRMS downloads, see
#                           backend/README.md "World coverage"), otherwise the Tunisia and Algeria
#                           history that is committed (backend/data/harmonized.parquet). The live map,
#                           reports and Processing Center show "unavailable" without the database.
#
# It stops whatever already listens on 8000, 8001, 5173 and 5174. Ctrl+C stops the API (and so the
# agents) and the dashboard. Logs: /tmp/emberguard-api.log, /tmp/emberguard-web.log
# The fire worker (polls NASA FIRMS for the live map) is a separate process: see the README.
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
  [ -f "$ROOT/.env" ] || { echo "missing .env: run  cp .env.example .env  in $ROOT and fill in the keys"; exit 1; }
  # Postgres: start the container and wait until it accepts connections (a no-op when it already runs).
  if command -v docker > /dev/null 2>&1; then
    echo "starting Postgres (docker compose up -d db)..."
    (cd "$ROOT" && { docker compose up -d --wait db || docker compose up -d db; }) > /dev/null 2>&1 \
      || echo "  could not start Postgres with docker: the database endpoints answer 503 until it is up"
  else
    echo "docker not found: start Postgres yourself (DATABASE_URL in .env)"
  fi
  # Python reads the environment, not .env. tr drops Windows line endings, which bash would otherwise
  # keep as a hidden \r at the end of every value (and print ": command not found" for blank lines).
  set -a; . <(tr -d '\r' < "$ROOT/.env"); set +a      # DATABASE_URL etc.
  if alembic upgrade head > /tmp/emberguard-migrate.log 2>&1; then
    echo "database schema is up to date"
  else
    echo "alembic upgrade head failed (see /tmp/emberguard-migrate.log): continuing"
  fi
  echo "API mode: database (approved dataset + live features)"
  uvicorn api.main:app --port "$API_PORT" > /tmp/emberguard-api.log 2>&1 &
else
  if [ -f data/world/harmonized.parquet ]; then
    DATA=data/world/harmonized.parquet
    WARM_BBOX="148,-38,154,-28"                          # south-eastern Australia
    echo "API mode: world history (parquet, no database)"
  else
    # The world file needs multi-GB FIRMS downloads; without it the committed data still gives a working app.
    DATA=data/harmonized.parquet
    WARM_BBOX="8.3,36,9.8,37.2"                          # Bizerte / NW Tunisia
    echo "API mode: Tunisia and northern Algeria history from the committed file (no world file, no database)"
  fi
  env -u DATABASE_URL EMBER_DATA="$DATA" \
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
echo -n "warming up the data (the first load of the history file takes a few seconds)... "
curl -s -m 120 "http://localhost:$API_PORT/calendar?bbox=${WARM_BBOX:-8.3,36,9.8,37.2}&start=2019&end=2019" > /dev/null || true
curl -s "http://localhost:$API_PORT/health"; echo

# 3. Dashboard, pointed at this API (Vite reads VITE_API_URL at start-up only).
cd "$ROOT/frontend"
[ -d node_modules ] || npm install --no-audit --no-fund
# Vite's bundler is a native binary per operating system. If node_modules was installed on Windows it holds the
# Windows build and Vite crashes here ("Cannot find native binding"). Reinstalling from WSL into the same folder
# could break the Windows install, so do not touch it: say what to do instead. The API keeps running either way.
WEB_PID=""
if (node --input-type=module -e "import('rolldown').then(() => process.exit(0), () => process.exit(1))") > /dev/null 2>&1; then
  VITE_API_URL="http://localhost:$API_PORT" npx vite --port "$WEB_PORT" --strictPort > /tmp/emberguard-web.log 2>&1 &
  WEB_PID=$!
  sleep 3
fi

echo
if [ -n "$WEB_PID" ]; then
  echo "  Dashboard:  http://localhost:$WEB_PORT/dashboard"
else
  echo "  Dashboard:  NOT started here. frontend/node_modules was installed on Windows, so Vite cannot run inside WSL."
  echo "              Start it on Windows instead (PowerShell), it will use this API at http://localhost:$API_PORT :"
  echo "                  cd frontend ; npm run dev        then open  http://localhost:$WEB_PORT/dashboard"
  echo "              (or, to run it here: remove frontend/node_modules and run npm install in WSL; that stops it working on Windows.)"
fi
echo "  API docs:   http://localhost:$API_PORT/docs"
echo "  Agents:     started by the API for the Social Alerts page (status: http://localhost:$API_PORT/incidents/status)"
if [ "$MODE" != "db" ]; then
  if [ "${DATA:-}" = "data/world/harmonized.parquet" ]; then
    echo "  Try the calendar with a drawn box, e.g. Australia, the Amazon or Central Africa."
  else
    echo "  This data covers Tunisia and northern Algeria. For the live map, reports and the whole world use:  ./run-world.sh db"
  fi
fi
echo "  Ctrl+C to stop the API and the agents${WEB_PID:+ and the dashboard}."
wait
