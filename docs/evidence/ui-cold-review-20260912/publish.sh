#!/bin/sh
set -eu
cd /root/revguard
backup=/root/revguard-backups/ui-cold-review-20260912
test -s "$backup/source-and-settings-before.tgz"
docker run --rm --user 0 -v /root/revguard:/workspace --entrypoint python revguard-ui-audit:0.5.8 -c 'from pathlib import Path; p=Path("/workspace/.env"); rows=p.read_text().splitlines(); rows=["REVGUARD_RELEASE_VERSION=0.5.8" if r.startswith("REVGUARD_RELEASE_VERSION=") else r for r in rows]; assert "REVGUARD_RELEASE_VERSION=0.5.8" in rows; p.write_text("\n".join(rows)+"\n")'
docker exec -i revguard-api sh -c 'cat > /tmp/revguard_ui_rollout_guard.py' < /tmp/revguard_ui_rollout_guard.py
docker exec -d -e PYTHONPATH=/app revguard-api python /tmp/revguard_ui_rollout_guard.py
ready=false
for n in 1 2 3 4 5; do
  if docker exec revguard-api test -f /tmp/revguard-ui-0.5.8.ready; then ready=true; break; fi
  sleep 1
done
[ "$ready" = true ] || { echo 'No exclusive quiescent lease: deployment stopped'; exit 1; }
docker tag revguard-ui-audit:0.5.8 revguard-revguard-api:latest
docker compose -p revguard -f docker-compose.yml -f docker-compose.polardb.yml -f docker-compose.agentteams.yml -f docker-compose.observability.yml up -d --no-build --no-deps revguard-api
docker inspect --format '{{.Image}}' revguard-api
