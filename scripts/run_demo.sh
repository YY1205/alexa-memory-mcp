#!/usr/bin/env bash
# Start the memory MCP server (:8000) and the Simulated Alexa+ web app (:8001)
# on a temporary copy of the fictional demo data. Ctrl+C stops both.
#
#   scripts/run_demo.sh                    # brain from SIM_BRAIN (default: bedrock)
#   SIM_BRAIN=scripted scripts/run_demo.sh # offline, deterministic demo brain
#   MEMORY_BACKEND=drive DRIVE_FOLDER_ID=... GOOGLE_APPLICATION_CREDENTIALS=... scripts/run_demo.sh
#                                          # memories.md in your Google Drive demo folder
#
# Local (default): a temporary copy of the demo data, seeded for today.
# Drive: the existing memories.md in the folder is used as is; nothing is overwritten
# unless you click "Reset demo". Hand edits in Drive show up within MEMORY_REFRESH_SECONDS
# (5 s here).
#
# Bedrock needs BEDROCK_MODEL_ID, AWS_REGION and AWS credentials in the default
# credential chain (e.g. AWS_PROFILE). This script never reads them.
#
# Account linking: open the simulator, click "Link account", and sign in on the
# memory server's page with the owner passphrase printed below (set OWNER_PASSPHRASE
# to choose your own, e.g. for a recording). The server only gets its scrypt hash.
set -euo pipefail

cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"
SERVER_PORT="${PORT:-8000}"
SIM_PORT="${SIM_PORT:-8001}"

BACKEND="${MEMORY_BACKEND:-local}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/alexa-memory-demo.XXXXXX")"
if [ "$BACKEND" = "drive" ]; then
  : "${DRIVE_FOLDER_ID:?set DRIVE_FOLDER_ID to the demo folder id}"
  : "${GOOGLE_APPLICATION_CREDENTIALS:?set GOOGLE_APPLICATION_CREDENTIALS to the service account key path}"
  DATA_DESC="Google Drive folder ...${DRIVE_FOLDER_ID: -4}"
else
  "$PY" demo_data/seed.py "$WORK/memories.md" >/dev/null  # dates relative to today
  DATA_DESC="$WORK/memories.md"
fi

# OAuth for this run: the simulator is the one registered client (like Alexa).
RANDOM_SECRET() { "$PY" -c 'import secrets; print(secrets.token_urlsafe(32))'; }
PASSPHRASE="${OWNER_PASSPHRASE:-$("$PY" -c 'import secrets; print("-".join(secrets.token_hex(2) for _ in range(4)))')}"
export OAUTH_CLIENT_ID="simulated-alexa"
export OAUTH_CLIENT_SECRET="$(RANDOM_SECRET)"
export OAUTH_REDIRECT_URIS="http://127.0.0.1:$SIM_PORT/oauth/callback"
OWNER_HASH="$(PASSPHRASE="$PASSPHRASE" "$PY" -c 'import os; from auth.passphrase import hash_passphrase; print(hash_passphrase(os.environ["PASSPHRASE"]))')"
unset OWNER_PASSPHRASE MCP_BEARER_TOKEN

PIDS=()
cleanup() {
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

MEMORY_BACKEND="$BACKEND" LOCAL_MEMORY_PATH="$WORK/memories.md" MEMORY_REFRESH_SECONDS="${MEMORY_REFRESH_SECONDS:-5}" \
  DEMO_ADMIN=1 HOST=127.0.0.1 PORT="$SERVER_PORT" \
  OWNER_PASSPHRASE_HASH="$OWNER_HASH" OAUTH_TOKEN_STORE="$WORK/tokens.json" \
  "$PY" server.py &
PIDS+=($!)

for _ in $(seq 100); do  # Drive startup downloads the file first
  curl -fs "http://127.0.0.1:$SERVER_PORT/healthz" >/dev/null 2>&1 && break
  kill -0 "${PIDS[0]}" 2>/dev/null || { echo "The memory server did not start (see the message above)."; exit 1; }
  sleep 0.1
done

MCP_SERVER_URL="http://127.0.0.1:$SERVER_PORT/mcp" SIM_HOST=127.0.0.1 SIM_PORT="$SIM_PORT" \
  SIM_PUBLIC_URL="http://127.0.0.1:$SIM_PORT" "$PY" -m simulator.app &
PIDS+=($!)

echo
echo "  Memory server:      http://127.0.0.1:$SERVER_PORT/mcp   (data: $DATA_DESC)"
echo "  Simulated Alexa+:   http://127.0.0.1:$SIM_PORT          (brain: ${SIM_BRAIN:-bedrock})"
echo "  Owner passphrase:   $PASSPHRASE   (click Link account, then sign in with it)"
echo "  Ctrl+C to stop."
echo
wait
