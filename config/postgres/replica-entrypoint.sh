#!/bin/sh
set -eu

: "${PRIMARY_HOST:?set PRIMARY_HOST}"
: "${REPLICATION_USER:?set REPLICATION_USER}"
: "${REPLICATION_PASSWORD:?set REPLICATION_PASSWORD}"
: "${PGDATA:?set PGDATA}"

mkdir -p "$PGDATA" /var/lib/postgresql
chown -R postgres:postgres "$PGDATA" /var/lib/postgresql
printf '%s:*:*:%s:%s\n' "$PRIMARY_HOST" "$REPLICATION_USER" "$REPLICATION_PASSWORD" \
  > /var/lib/postgresql/.pgpass
chown postgres:postgres /var/lib/postgresql/.pgpass
chmod 600 /var/lib/postgresql/.pgpass

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  rm -rf "$PGDATA"/*
  until pg_isready -h "$PRIMARY_HOST" -p 5432 -U "$REPLICATION_USER"; do
    sleep 2
  done
  gosu postgres pg_basebackup \
    -h "$PRIMARY_HOST" -p 5432 -U "$REPLICATION_USER" \
    -D "$PGDATA" -R -X stream --checkpoint=fast
fi

exec docker-entrypoint.sh postgres -c hot_standby=on
