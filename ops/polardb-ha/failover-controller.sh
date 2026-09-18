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
  result=$(psql -h "$PRIMARY_HOST" -p "$PORT" -U postgres -d postgres -tAc 'SELECT NOT pg_is_in_recovery()' 2>/dev/null || true)
  [ "$result" = "t" ]
}
standby_recovering() {
  result=$(psql -h "$STANDBY_HOST" -p "$PORT" -U postgres -d postgres -tAc 'SELECT pg_is_in_recovery()' 2>/dev/null || true)
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
    psql -h "$STANDBY_HOST" -p "$PORT" -U postgres -d postgres -v ON_ERROR_STOP=1 \
      -tAc 'SELECT pg_promote(true, 30)' | sed 's/^/PROMOTE_RESULT /'
    log "STANDBY_PROMOTED"
    promoted=true
    continue
  fi
  sleep 1
done
tail -f /dev/null
