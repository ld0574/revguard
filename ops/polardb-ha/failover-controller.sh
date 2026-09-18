#!/usr/bin/env sh
# Promote only after this controller has observed a writable primary and then
# lost it.  This fencing rule avoids promotion during initial startup.
set -eu

PRIMARY_HOST="${PRIMARY_HOST:-polardb-primary}"
STANDBY_HOST="${STANDBY_HOST:-polardb-standby}"
PORT="${POLARDB_PORT:-5432}"
seen_primary=false
promoted=false

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s %s\n' "$(stamp)" "$*"; }
primary_writable() {
  result=$(psql -w -h "$PRIMARY_HOST" -p "$PORT" -U postgres -d postgres -tAc 'SELECT NOT pg_is_in_recovery()' 2>/dev/null || true)
  [ "$result" = "t" ]
}
standby_recovering() {
  result=$(psql -w -h "$STANDBY_HOST" -p "$PORT" -U postgres -d postgres -tAc 'SELECT pg_is_in_recovery()' 2>/dev/null || true)
  [ "$result" = "t" ]
}
standby_promoted() {
  result=$(psql -w -h "$STANDBY_HOST" -p "$PORT" -U postgres -d postgres -tAc 'SELECT NOT pg_is_in_recovery()' 2>/dev/null || true)
  [ "$result" = "t" ]
}

while [ "$promoted" = false ]; do
  if primary_writable; then
    seen_primary=true
    sleep 1
    continue
  fi
  if [ "$seen_primary" = false ]; then
    sleep 1
    continue
  fi
  if standby_recovering; then
    log "PRIMARY_UNAVAILABLE promoting standby"
    result=$(psql -w -h "$STANDBY_HOST" -p "$PORT" -U postgres -d postgres \
      -tAc 'SELECT pg_promote(false, 60)' 2>/dev/null || true)
    log "PROMOTE_RESULT ${result:-connection_lost}"
    for _ in $(seq 1 45); do
      if standby_promoted; then
        log "STANDBY_PROMOTED"
        promoted=true
        break
      fi
      sleep 1
    done
    continue
  fi
  sleep 1
done
tail -f /dev/null
