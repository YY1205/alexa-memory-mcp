#!/bin/sh
# Container entry point. The public demo is ephemeral by design: the server seeds the
# fictional Sam data into /data at every start (see docs/DEPLOY-PLAN.md).
set -eu
case "${APP:-server}" in
  server)
    python demo_data/seed.py "${LOCAL_MEMORY_PATH:-/data/memories.md}" >/dev/null
    exec python server.py
    ;;
  simulator)
    exec python -m simulator.app
    ;;
  *)
    echo "APP must be 'server' or 'simulator'" >&2
    exit 64
    ;;
esac
