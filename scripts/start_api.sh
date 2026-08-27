#!/bin/sh
set -eu

db_path="${REVGUARD_DB_PATH:-/app/runtime/revguard.db}"
gateway_state="${REVGUARD_GATEWAY_STATE_PATH:-/app/runtime/revguard.gateway.json}"

if [ -z "${REVGUARD_DATABASE_URL:-}" ]; then
  if [ "${REVGUARD_RESET_ON_START:-false}" = "true" ]; then
    python scripts/seed_demo.py --db "$db_path" --reset --gateway-state "$gateway_state"
  else
    python scripts/seed_demo.py --db "$db_path"
  fi
elif [ "${REVGUARD_RESET_ON_START:-false}" = "true" ]; then
  echo "REVGUARD_RESET_ON_START is forbidden with the PolarDB production store" >&2
  exit 2
fi

exec uvicorn revguard.api:app --host 0.0.0.0 --port 9000
