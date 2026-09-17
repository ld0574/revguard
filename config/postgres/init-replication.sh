#!/bin/sh
set -eu

: "${REVGUARD_REPLICATION_PASSWORD:?set REVGUARD_REPLICATION_PASSWORD}"

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=replication_password="$REVGUARD_REPLICATION_PASSWORD" <<'SQL'
SELECT format(
  'CREATE ROLE revguard_replicator WITH REPLICATION LOGIN PASSWORD %L',
  :'replication_password'
)
WHERE NOT EXISTS (
  SELECT 1 FROM pg_roles WHERE rolname = 'revguard_replicator'
) \gexec
SQL

printf '%s\n' \
  'host replication revguard_replicator all scram-sha-256' \
  >> "$PGDATA/pg_hba.conf"
