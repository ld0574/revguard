#!/usr/bin/env bash
# Separate-container PolarDB-PG node lifecycle for the drill.  The official
# local-instance entrypoint uses the same PolarDB binaries but starts all nodes
# inside one container.  This wrapper keeps private data directories separate,
# mounts shared storage explicitly, and fails closed if the expected PolarDB
# tools are unavailable.
set -Eeuo pipefail

ROLE="${1:-primary}"
PRIVATE_DIR="${POLAR_PRIVATE_DIR:-/var/polardb/private}"
SHARED_DIR="${POLAR_SHARED_DIR:-/var/polardb/shared}"
ARCHIVE_DIR="${POLAR_ARCHIVE_DIR:-/var/polardb/wal-archive}"
PRIMARY_HOST="${PRIMARY_HOST:-polardb-primary}"
PRIMARY_PORT="${PRIMARY_PORT:-5432}"
PORT="${POLARDB_PORT:-5432}"
BACKUP_LABEL="${POLAR_BACKUP_LABEL:-revguard-p0-pitr}"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
fail() { log "FATAL $*" >&2; exit 1; }

for command in initdb pg_ctl psql pg_isready createdb polar-initdb.sh pg_basebackup; do
  command -v "$command" >/dev/null 2>&1 || fail "official PolarDB binary is missing: $command"
done

stop_node() {
  pg_ctl -D "$PRIVATE_DIR" status >/dev/null 2>&1 && pg_ctl -D "$PRIVATE_DIR" stop -m fast || true
}
trap stop_node EXIT INT TERM

append_common_config() {
  local host_id="$1"
  local archive_command
  archive_command="test ! -f ${ARCHIVE_DIR}/%f && cp %p ${ARCHIVE_DIR}/%f"
  test -f /u01/polardb_pg/share/postgresql/polardb.conf.sample \
    || fail "PolarDB configuration sample was not found"
  cat /u01/polardb_pg/share/postgresql/polardb.conf.sample >>"$PRIVATE_DIR/postgresql.conf"
  cat >>"$PRIVATE_DIR/postgresql.conf" <<EOF
port = ${PORT}
listen_addresses = '*'
huge_pages = off
full_page_writes = on
wal_level = replica
max_wal_senders = 10
max_replication_slots = 10
wal_keep_size = '256MB'
synchronous_commit = on
archive_mode = on
archive_command = '${archive_command}'
archive_timeout = '5s'
polar_hostid = ${host_id}
polar_enable_shared_storage_mode = on
polar_vfs.localfs_mode = on
polar_datadir = 'file-dio://${SHARED_DIR}'
shared_preload_libraries = '\$libdir/polar_vfs,\$libdir/polar_worker'
EOF
  cat >>"$PRIVATE_DIR/pg_hba.conf" <<EOF
host all all 0.0.0.0/0 trust
host replication postgres 0.0.0.0/0 trust
EOF
}

append_recovery_config() {
  local host_id="$1"
  cat >>"$PRIVATE_DIR/postgresql.conf" <<EOF
port = ${PORT}
listen_addresses = '*'
polar_hostid = ${host_id}
polar_enable_shared_storage_mode = on
polar_vfs.localfs_mode = on
polar_datadir = 'file-dio://${SHARED_DIR}'
shared_preload_libraries = '\$libdir/polar_vfs,\$libdir/polar_worker'
EOF
}

wait_primary() {
  for _ in $(seq 1 90); do
    pg_isready -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" -U postgres >/dev/null 2>&1 && return 0
    sleep 1
  done
  fail "primary did not become reachable: ${PRIMARY_HOST}:${PRIMARY_PORT}"
}

prepare_directories() {
  # Docker creates a fresh named volume as root:root.  The upstream image runs
  # as postgres (with passwordless sudo), so take ownership before initdb.
  sudo mkdir -p "$PRIVATE_DIR" "$SHARED_DIR"
  sudo chown -R postgres:postgres "$PRIVATE_DIR" "$SHARED_DIR"
  chmod 700 "$PRIVATE_DIR"
}

prepare_archive_directory() {
  sudo mkdir -p "$ARCHIVE_DIR"
  sudo chown -R postgres:postgres "$ARCHIVE_DIR"
}

run_primary() {
  prepare_directories
  prepare_archive_directory
  if [[ ! -s "$PRIVATE_DIR/PG_VERSION" ]]; then
    log "initializing primary private and shared storage"
    initdb -k -A trust -U postgres -D "$PRIVATE_DIR" --wal-segsize=16
    append_common_config 1
    echo "synchronous_standby_names = 'FIRST 1 (standby1)'" >>"$PRIVATE_DIR/postgresql.conf"
    polar-initdb.sh "$PRIVATE_DIR/" "$SHARED_DIR/" primary localfs
  fi
  pg_ctl -D "$PRIVATE_DIR" start -w
  psql -h 127.0.0.1 -p "$PORT" -U postgres -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='revguard'" | grep -qx 1 \
    || PGOPTIONS='-c synchronous_commit=local' createdb -h 127.0.0.1 -p "$PORT" -U postgres revguard
  PGOPTIONS='-c synchronous_commit=local' psql -h 127.0.0.1 -p "$PORT" -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c "SELECT pg_create_physical_replication_slot('standby1') WHERE NOT EXISTS (SELECT 1 FROM pg_replication_slots WHERE slot_name='standby1')" >/dev/null
  log "PRIMARY_READY port=${PORT}"
  tail -f /dev/null
}

run_standby() {
  prepare_directories
  prepare_archive_directory
  if [[ ! -s "$PRIVATE_DIR/PG_VERSION" ]]; then
    wait_primary
    # `pg_basebackup` serializes a file-dio URI as a local relative path and
    # cannot restore it into a different container.  The upstream local-image
    # workflow creates a replica's private data with polar-initdb instead; the
    # two nodes still share only the explicit shared-storage volume.
    log "creating isolated standby private data with polar-initdb replica"
    polar-initdb.sh "$PRIVATE_DIR/" "$SHARED_DIR/" replica localfs
    append_common_config 3
    cat >>"$PRIVATE_DIR/postgresql.conf" <<EOF
port = ${PORT}
listen_addresses = '*'
polar_hostid = 3
polar_enable_parallel_replay_standby_mode = on
primary_conninfo = 'host=${PRIMARY_HOST} port=${PRIMARY_PORT} user=postgres dbname=postgres application_name=standby1'
primary_slot_name = 'standby1'
EOF
    touch "$PRIVATE_DIR/replica.signal"
  fi
  pg_ctl -D "$PRIVATE_DIR" start -w
  log "STANDBY_READY port=${PORT}"
  tail -f /dev/null
}

run_basebackup() {
  prepare_directories
  [[ ! -e "$PRIVATE_DIR/PG_VERSION" ]] || fail "PITR recovery volume is not empty"
  wait_primary
  # PolarDB-PG 15 splits private node state from shared localfs data.  Copying
  # only /var/polardb/shared and then recreating the private directory leaves a
  # node that starts but cannot resolve any database.  The official backup
  # binary accepts both destinations and keeps those two storage halves under
  # one coordinated backup window.
  log "creating coordinated private/shared PITR base backup label=${BACKUP_LABEL}"
  pg_basebackup -w -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" -U postgres \
    -D "$PRIVATE_DIR" --polardata="$SHARED_DIR" \
    -X stream -c fast -l "$BACKUP_LABEL" --no-sync
  chown -R postgres:postgres "$PRIVATE_DIR" "$SHARED_DIR"
  append_recovery_config 5
  touch "$PRIVATE_DIR/.revguard-basebackup-complete"
  log "PITR_BASEBACKUP_READY label=${BACKUP_LABEL}"
}

run_recovery() {
  [[ -f "$PRIVATE_DIR/.revguard-basebackup-complete" ]] || fail "PITR base backup is missing"
  [[ -n "${PITR_TARGET_TIME:-}" ]] || fail "PITR_TARGET_TIME is required"
  # This PolarDB-PG build accepts SQL timestamp syntax in postgresql.conf,
  # rather than an RFC 3339 `T...Z` literal recorded in the evidence manifest.
  local config_target="${PITR_TARGET_TIME/T/ }"
  config_target="${config_target/Z/+00}"
  rm -f "$PRIVATE_DIR/standby.signal" "$PRIVATE_DIR/replica.signal" "$PRIVATE_DIR/recovery.signal"
  find "$PRIVATE_DIR/pg_replslot" -mindepth 1 -maxdepth 1 -type d -name standby1 -exec rm -rf {} +
  cat >>"$PRIVATE_DIR/postgresql.conf" <<EOF
port = ${PORT}
listen_addresses = '*'
restore_command = 'cp ${ARCHIVE_DIR}/%f %p'
recovery_target_time = '${config_target}'
recovery_target_action = 'promote'
recovery_target_timeline = 'latest'
EOF
  touch "$PRIVATE_DIR/recovery.signal"
  pg_ctl -D "$PRIVATE_DIR" start -w
  log "PITR_RECOVERY_READY target=${PITR_TARGET_TIME}"
  tail -f /dev/null
}

case "$ROLE" in
  primary) run_primary ;;
  standby) run_standby ;;
  basebackup) run_basebackup ;;
  recovery) run_recovery ;;
  *) fail "unknown PolarDB role: $ROLE" ;;
esac
