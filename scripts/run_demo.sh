#!/usr/bin/env bash
# Start the memory MCP server (:8000) and the Simulated Alexa+ web app (:8001)
# on a temporary copy of the fictional demo data. Ctrl+C stops both.
#
#   scripts/run_demo.sh                    # brain from SIM_BRAIN (default: bedrock)
#   SIM_BRAIN=scripted scripts/run_demo.sh # offline, deterministic demo brain
#
# Bedrock needs BEDROCK_MODEL_ID, AWS_REGION and AWS credentials in the default
# credential chain (e.g. AWS_PROFILE). This script never reads them.
set -euo pipefail

cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"
SERVER_PORT="${PORT:-8000}"
SIM_PORT="${SIM_PORT:-8001}"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/alexa-memory-demo.XXXXXX")"
cp demo_data/memories.md "$WORK/memories.md"

# A throwaway token for this run only; both processes get it via the environment.
export MCP_BEARER_TOKEN="${MCP_BEARER_TOKEN:-$("$PY" -c 'import secrets; print(secrets.token_urlsafe(32))')}"

PIDS=()
cleanup() {
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

LOCAL_MEMORY_PATH="$WORK/memories.md" DEMO_ADMIN=1 HOST=127.0.0.1 PORT="$SERVER_PORT" \
  "$PY" server.py &
PIDS+=($!)

for _ in $(seq 50); do
  curl -fs "http://127.0.0.1:$SERVER_PORT/healthz" >/dev/null 2>&1 && break
  sleep 0.1
done

MCP_SERVER_URL="http://127.0.0.1:$SERVER_PORT/mcp" SIM_HOST=127.0.0.1 SIM_PORT="$SIM_PORT" \
  "$PY" -m simulator.app &
PIDS+=($!)

echo
echo "  Memory server:      http://127.0.0.1:$SERVER_PORT/mcp   (data: $WORK/memories.md)"
echo "  Simulated Alexa+:   http://127.0.0.1:$SIM_PORT          (brain: ${SIM_BRAIN:-bedrock})"
echo "  Ctrl+C to stop."
echo
wait
