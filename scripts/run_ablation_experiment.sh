#!/usr/bin/env bash
# Run the P0 A/B/C ablation in an isolated Docker project on 10.10.10.202.
# It has a deliberately narrow ownership boundary: only the project named
# revguard-ablation and its named volumes may be removed.  The existing 19000
# and 19088 stacks, their cases, their volumes, and their evidence are never
# reset or restarted.
set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PROJECT="revguard-ablation"
COMPOSE=(docker compose --project-directory "$ROOT_DIR" -p "$PROJECT" -f "$ROOT_DIR/docker-compose.ablation.yml")
OUTPUT_ROOT="${1:-$ROOT_DIR/docs/evidence/ablation-$(date -u +%Y%m%dT%H%M%SZ)}"
SWITCH="$ROOT_DIR/scripts/switch_agentteams_api_target.sh"
ALIAS_LEASED=false
KEEP_STACK=false

usage() {
  cat <<'EOF'
Usage: bash scripts/run_ablation_experiment.sh [evidence-output-directory] [--keep-stack]

Requires the private 202 .env to provide REVGUARD_ABLATION_DB_PASSWORD and the
existing AgentTeams Matrix settings.  The script captures five Direct samples,
five MCP Team samples, and one real AgentTeams/Matrix observation per case.
EOF
}

if [[ "${2:-}" == "--keep-stack" || "${1:-}" == "--keep-stack" ]]; then
  KEEP_STACK=true
  [[ "${1:-}" == "--keep-stack" ]] && OUTPUT_ROOT="$ROOT_DIR/docs/evidence/ablation-$(date -u +%Y%m%dT%H%M%SZ)"
fi
[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { usage; exit 0; }

fail() { echo "ablation failed: $*" >&2; exit 1; }
log() { printf '\n==> %s\n' "$*"; }

cleanup() {
  status=$?
  set +e
  if [[ "$ALIAS_LEASED" == true ]]; then
    "$SWITCH" dev >>"$OUTPUT_ROOT/alias-restore.log" 2>&1 || true
    "$SWITCH" status >>"$OUTPUT_ROOT/alias-after-restore.log" 2>&1 || true
  fi
  if [[ "$KEEP_STACK" != true ]]; then
    "${COMPOSE[@]}" down --volumes --remove-orphans >>"$OUTPUT_ROOT/cleanup.log" 2>&1 || true
  fi
  exit "$status"
}

mkdir -p "$OUTPUT_ROOT"
# The ablation runner uses the image's unprivileged application user; these
# JSON/log artifacts carry no credentials and need to be writable via bind mount.
chmod 777 "$OUTPUT_ROOT"
trap cleanup EXIT INT TERM

[[ -f /etc/os-release ]] || fail "only run this on 10.10.10.202"
ip -4 addr show | grep -Eq 'inet 10[.]10[.]10[.]202/' || fail "must run on 10.10.10.202"
docker network inspect agentteams-net >/dev/null 2>&1 || fail "agentteams-net is unavailable"
[[ -f "$ROOT_DIR/.env" ]] || fail "private .env is missing"
grep -q '^REVGUARD_ABLATION_DB_PASSWORD=.' "$ROOT_DIR/.env" || fail "set REVGUARD_ABLATION_DB_PASSWORD in private .env"
"$SWITCH" status >"$OUTPUT_ROOT/alias-before.log" 2>&1

log "build and start isolated PostgreSQL"
"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" build ablation-runner revguard-api-experiment 2>&1 | tee "$OUTPUT_ROOT/build.log"
"${COMPOSE[@]}" up -d ablation-postgres
for _ in $(seq 1 45); do
  [[ "$(docker inspect -f '{{.State.Health.Status}}' revguard-ablation-ablation-postgres-1 2>/dev/null || true)" == healthy ]] && break
  sleep 2
done
[[ "$(docker inspect -f '{{.State.Health.Status}}' revguard-ablation-ablation-postgres-1 2>/dev/null || true)" == healthy ]] || fail "isolated PostgreSQL did not become healthy"

log "migrate and seed the isolated Golden Case database"
"${COMPOSE[@]}" run --rm --no-deps ablation-runner scripts/migrate_polardb.py | tee "$OUTPUT_ROOT/migration.log"
"${COMPOSE[@]}" run --rm --no-deps ablation-runner -c '
import os
from revguard.store import create_store
from scripts.seed_demo import seed_store
store = create_store("/tmp/unused.db", database_url=os.environ["REVGUARD_DATABASE_URL"])
try:
    seeded = seed_store(store, quiet=True)
    assert len(seeded) >= 8
finally:
    store.close()
print("isolated golden fixtures seeded")
' | tee "$OUTPUT_ROOT/seed.log"

log "run five Direct and MCP samples for each Golden Case"
for mode in direct mcp; do
  for case in 001 003 004 007; do
    for iteration in 1 2 3 4 5; do
      result="$OUTPUT_ROOT/${mode}-${case}-${iteration}.json"
      "${COMPOSE[@]}" run --rm --no-deps -v "$OUTPUT_ROOT:/evidence" ablation-runner \
        scripts/run_ablation.py --mode "$mode" --case "$case" --output "/evidence/$(basename "$result")" \
        --work-dir "/evidence/work-${mode}-${case}-${iteration}" \
        | tee -a "$OUTPUT_ROOT/${mode}.log"
    done
  done
done

log "start the isolated Matrix API and lease revguard-api.internal"
"${COMPOSE[@]}" up -d revguard-api-experiment
for _ in $(seq 1 60); do
  docker exec revguard-api-experiment python -c 'from revguard.api import store; assert store.readiness()["ready"]' >/dev/null 2>&1 && break
  sleep 2
done
docker exec revguard-api-experiment python -c 'from revguard.api import store; assert store.readiness()["ready"]' \
  || fail "isolated Matrix API did not become ready"
ALIAS_LEASED=true
"$SWITCH" experiment | tee "$OUTPUT_ROOT/alias-experiment.log"

log "run one real AgentTeams / Matrix observation per Golden Case"
for case in 001 003 004 007; do
  "${COMPOSE[@]}" run --rm --no-deps -v "$OUTPUT_ROOT:/evidence" ablation-runner \
    scripts/run_ablation.py --mode matrix --matrix-internal --case "$case" \
    --output "/evidence/matrix-${case}.json" --work-dir "/evidence/work-matrix-${case}" \
    | tee -a "$OUTPUT_ROOT/matrix.log"
done

log "summarize results and restore the 19088 AgentTeams alias"
"${COMPOSE[@]}" run --rm --no-deps -v "$OUTPUT_ROOT:/evidence" ablation-runner -c '
import json
from pathlib import Path

root = Path("/evidence")
records = []
for path in sorted(root.glob("*.json")):
    item = json.loads(path.read_text(encoding="utf-8"))
    records.append({
        "file": path.name,
        "mode": item["mode"],
        "case_id": item["case_spec"]["case_id"],
        "passed": item["outcome"]["business_terminal_match"],
        "wall_clock_ms": item["runtime_metrics"]["wall_clock_ms"],
        "tokens": item["runtime_metrics"]["token_usage"],
        "governance": item["governance_metrics"],
    })
assert records and all(row["passed"] for row in records), "at least one ablation sample failed"
summary = {"schema_version": "1.0", "records": records, "all_passed": True}
(root / "manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
' | tee "$OUTPUT_ROOT/summary.log"

ALIAS_LEASED=false
"$SWITCH" dev | tee "$OUTPUT_ROOT/alias-restore.log"
"$SWITCH" status | tee "$OUTPUT_ROOT/alias-after-restore.log"
curl -fsS http://127.0.0.1:19088/api/v1/health >"$OUTPUT_ROOT/dev-health-after.json" \
  || fail "19088 did not recover after alias restoration"
echo "Ablation evidence: $OUTPUT_ROOT"
