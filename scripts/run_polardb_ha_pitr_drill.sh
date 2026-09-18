#!/usr/bin/env bash
# Execute the isolated P0 PolarDB HA + PITR drill on 10.10.10.202.
#
# The command owns only project revguard-polardb-ha and its explicitly named
# volumes.  It never touches revguard-polardb, revguard-dev, ports 19000/19088,
# or their histories.  On every exit it saves logs and removes just this drill's
# containers, network, and volumes; it never uses docker system prune.
set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PROJECT="revguard-polardb-ha"
COMPOSE=(docker compose --project-directory "$ROOT_DIR" -p "$PROJECT" -f "$ROOT_DIR/docker-compose.polardb-ha.yml")
OUTPUT_DIR="${1:-$ROOT_DIR/docs/evidence/polardb-ha-pitr-$(date -u +%Y%m%dT%H%M%SZ)}"
KEEP_RESOURCES=false

usage() {
  cat <<'EOF'
Usage: bash scripts/run_polardb_ha_pitr_drill.sh [evidence-output-directory] [--keep-resources]

Runs only on 10.10.10.202.  The result directory receives manifest.json,
ha-result.json, pitr-result.json, raw probes, sanitized logs, and SHA256SUMS.txt.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then usage; exit 0; fi
if [[ "${2:-}" == "--keep-resources" || "${1:-}" == "--keep-resources" ]]; then
  KEEP_RESOURCES=true
  [[ "${1:-}" == "--keep-resources" ]] && OUTPUT_DIR="$ROOT_DIR/docs/evidence/polardb-ha-pitr-$(date -u +%Y%m%dT%H%M%SZ)"
fi

log() { printf '\n==> %s\n' "$*"; }
fail() { echo "PolarDB HA/PITR drill failed: $*" >&2; exit 1; }

collect_logs() {
  set +e
  for service in polardb-primary polardb-standby ha-router failover-controller pitr-basebackup pitr-recovery; do
    "${COMPOSE[@]}" logs --no-color "$service" >"$OUTPUT_DIR/${service}.log" 2>&1 || true
  done
}

cleanup() {
  status=$?
  set +e
  collect_logs
  if [[ "$KEEP_RESOURCES" != true ]]; then
    "${COMPOSE[@]}" down --volumes --remove-orphans >>"$OUTPUT_DIR/cleanup.log" 2>&1 || true
  fi
  exit "$status"
}

mkdir -p "$OUTPUT_DIR"
# revguard-tools deliberately runs as an unprivileged container user.  The
# evidence contains no credentials, so grant that one container a writeable
# bind mount while leaving all unrelated runtime directories untouched.
chmod 777 "$OUTPUT_DIR"
trap cleanup EXIT INT TERM

ip -4 addr show | grep -Eq 'inet 10[.]10[.]10[.]202/' || fail "must run on 10.10.10.202"
"${COMPOSE[@]}" config --quiet

log "build the official PolarDB-PG wrapper and start two independent nodes"
"${COMPOSE[@]}" build polardb-primary revguard-tools 2>&1 | tee "$OUTPUT_DIR/build.log"
docker image inspect revguard-polardb-ha:local >"$OUTPUT_DIR/image-inspect.json"
"${COMPOSE[@]}" up -d polardb-primary polardb-standby ha-router failover-controller
for _ in $(seq 1 120); do
  "${COMPOSE[@]}" exec -T polardb-primary pg_isready -h 127.0.0.1 -p 5432 -U postgres -d postgres >/dev/null 2>&1 \
    && "${COMPOSE[@]}" exec -T polardb-standby sh -c "psql -h 127.0.0.1 -p 5432 -U postgres -d postgres -tAc 'SELECT pg_is_in_recovery()' | grep -qx t" >/dev/null 2>&1 \
    && break
  sleep 2
done
"${COMPOSE[@]}" exec -T polardb-primary pg_isready -h 127.0.0.1 -p 5432 -U postgres -d postgres >/dev/null \
  || fail "primary did not become ready"
"${COMPOSE[@]}" exec -T polardb-standby sh -c "psql -h 127.0.0.1 -p 5432 -U postgres -d postgres -tAc 'SELECT pg_is_in_recovery()' | grep -qx t" >/dev/null \
  || fail "standby did not enter recovery"
"${COMPOSE[@]}" exec -T polardb-primary psql -h 127.0.0.1 -p 5432 -U postgres -d postgres -Atc \
  "SHOW config_file; SHOW synchronous_standby_names; SHOW archive_mode; SHOW archive_command;" \
  >"$OUTPUT_DIR/config-summary.txt"

log "apply schema and seed a disposable RevGuard dataset"
"${COMPOSE[@]}" run --rm --no-deps revguard-tools scripts/migrate_polardb.py | tee "$OUTPUT_DIR/migration.log"
"${COMPOSE[@]}" run --rm --no-deps revguard-tools -c '
import os
from revguard.store import create_store
from scripts.seed_demo import seed_store
store = create_store("/tmp/unused.db", database_url=os.environ["REVGUARD_DATABASE_URL"])
try:
    seeded = seed_store(store, quiet=True)
    assert len(seeded) >= 8
finally:
    store.close()
print("isolated synthetic RevGuard dataset seeded")
' | tee "$OUTPUT_DIR/seed.log"

log "prove synchronous state and make a recovery-only localfs physical snapshot"
"${COMPOSE[@]}" run --rm --no-deps -v "$OUTPUT_DIR:/evidence" revguard-tools \
  scripts/probe_polardb_drill.py --kind sync --output /evidence/sync-before-fault.json
"${COMPOSE[@]}" run --rm --no-deps pitr-basebackup | tee "$OUTPUT_DIR/pitr-snapshot.log"

log "create A, record the recovery target, then create B"
"${COMPOSE[@]}" exec -T polardb-primary psql -h 127.0.0.1 -p 5432 -U postgres -d revguard -v ON_ERROR_STOP=1 -c \
  "CREATE TABLE IF NOT EXISTS revguard_pitr_markers (marker text PRIMARY KEY, value text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp()); INSERT INTO revguard_pitr_markers(marker, value) VALUES ('A', 'before-target') ON CONFLICT (marker) DO NOTHING; CHECKPOINT; SELECT pg_switch_wal();" \
  >"$OUTPUT_DIR/pitr-marker-a.sql.out"
sleep 2
PITR_TARGET_TIME=$("${COMPOSE[@]}" exec -T polardb-primary psql -h 127.0.0.1 -p 5432 -U postgres -d revguard -Atc \
  "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')")
sleep 1
"${COMPOSE[@]}" exec -T polardb-primary psql -h 127.0.0.1 -p 5432 -U postgres -d revguard -v ON_ERROR_STOP=1 -c \
  "INSERT INTO revguard_pitr_markers(marker, value) VALUES ('B', 'after-target') ON CONFLICT (marker) DO NOTHING; CHECKPOINT; SELECT pg_switch_wal();" \
  >"$OUTPUT_DIR/pitr-marker-b.sql.out"
sleep 5
"${COMPOSE[@]}" run --rm --no-deps -v "$OUTPUT_DIR:/evidence" revguard-tools \
  scripts/capture_recovery_evidence.py --output /evidence/pitr-expected.json

log "recover into separate volumes at the recorded target time"
PITR_TARGET_TIME="$PITR_TARGET_TIME" "${COMPOSE[@]}" up -d pitr-recovery
for _ in $(seq 1 120); do
  "${COMPOSE[@]}" exec -T pitr-recovery sh -c "psql -h 127.0.0.1 -p 5432 -U postgres -d revguard -tAc 'SELECT NOT pg_is_in_recovery()' | grep -qx t" >/dev/null 2>&1 && break
  sleep 2
done
"${COMPOSE[@]}" exec -T pitr-recovery sh -c "psql -h 127.0.0.1 -p 5432 -U postgres -d revguard -tAc 'SELECT NOT pg_is_in_recovery()' | grep -qx t" >/dev/null \
  || fail "PITR recovery did not promote a readable recovery node"
"${COMPOSE[@]}" run --rm --no-deps -v "$OUTPUT_DIR:/evidence" \
  -e REVGUARD_RECOVERY_DATABASE_URL=postgresql://postgres@pitr-recovery:5432/revguard \
  revguard-tools scripts/capture_recovery_evidence.py --expected /evidence/pitr-expected.json --output /evidence/pitr-actual.json
"${COMPOSE[@]}" run --rm --no-deps -v "$OUTPUT_DIR:/evidence" \
  -e REVGUARD_RECOVERY_DATABASE_URL=postgresql://postgres@pitr-recovery:5432/revguard \
  revguard-tools scripts/probe_polardb_drill.py --kind pitr --output /evidence/pitr-marker-check.json

log "inject a primary-container stop and wait for controller promotion"
"${COMPOSE[@]}" exec -T polardb-primary psql -h 127.0.0.1 -p 5432 -U postgres -d revguard -v ON_ERROR_STOP=1 -c \
  "CREATE TABLE IF NOT EXISTS revguard_ha_markers (marker text PRIMARY KEY, value text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp()); INSERT INTO revguard_ha_markers(marker, value) VALUES ('committed_before_failover', 'sync-commit') ON CONFLICT (marker) DO NOTHING;" \
  >"$OUTPUT_DIR/ha-before-fault.sql.out"
FAILURE_STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
"${COMPOSE[@]}" stop polardb-primary | tee "$OUTPUT_DIR/primary-stop.log"
WRITE_RESTORED_AT=""
for _ in $(seq 1 60); do
  WRITE_RESTORED_AT=$("${COMPOSE[@]}" exec -T failover-controller sh -c \
    "psql -h ha-router -p 5432 -U postgres -d revguard -tAc \"INSERT INTO revguard_ha_markers(marker, value) VALUES ('written_after_failover', 'stable-endpoint') ON CONFLICT (marker) DO NOTHING RETURNING to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD\\\"T\\\"HH24:MI:SS.US\\\"Z\\\"');\"" 2>/dev/null || true)
  [[ "$WRITE_RESTORED_AT" == *T*Z* ]] && break
  sleep 1
done
[[ "$WRITE_RESTORED_AT" == *T*Z* ]] || fail "stable endpoint was not writable within 60 seconds"
"${COMPOSE[@]}" run --rm --no-deps -v "$OUTPUT_DIR:/evidence" revguard-tools \
  scripts/probe_polardb_drill.py --kind ha --output /evidence/ha-after-failover.json

log "assemble evidence, verify package hashes, and record the single-host boundary"
"${COMPOSE[@]}" run --rm --no-deps -v "$OUTPUT_DIR:/evidence" revguard-tools \
  scripts/build_polardb_drill_evidence.py --evidence-dir /evidence \
  --pitr-target-time "$PITR_TARGET_TIME" --failure-started-at "$FAILURE_STARTED_AT" \
  --write-restored-at "$WRITE_RESTORED_AT" | tee "$OUTPUT_DIR/verdict.log"
"${COMPOSE[@]}" run --rm --no-deps -v "$OUTPUT_DIR:/evidence" revguard-tools -c '
import hashlib
from pathlib import Path
root = Path("/evidence")
for line in (root / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
    digest, name = line.split("  ", 1)
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
print("SHA256SUMS verified")
'
echo "PolarDB HA/PITR evidence: $OUTPUT_DIR"
