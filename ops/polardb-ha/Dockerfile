# The official local-instance image contains the PolarDB-PG binaries, including
# polar-initdb.sh.  This thin wrapper only replaces its
# single-container demo entrypoint with roles that can live in separate Docker
# containers for an isolated HA/PITR drill.
FROM polardb/polardb_pg_local_instance:15

USER root
COPY --chown=postgres:postgres ops/polardb-ha/node-entrypoint.sh /usr/local/bin/revguard-polar-node
RUN chmod 0755 /usr/local/bin/revguard-polar-node
USER postgres

ENTRYPOINT ["/usr/local/bin/revguard-polar-node"]
CMD ["primary"]
