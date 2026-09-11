#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker run --rm --user root -v "$PWD:/workspace" -w /workspace \
  --entrypoint python python:3.11-slim scripts/prepare_observability.py
compose=(docker compose -f docker-compose.yml)
if [[ "${1:-}" == "--full" ]]; then
  compose+=(-f docker-compose.polardb.yml -f docker-compose.agentteams.yml)
fi
compose+=(-f docker-compose.observability.yml)
"${compose[@]}" config --quiet
"${compose[@]}" run --rm --no-deps --entrypoint promtool prometheus check config /etc/prometheus/prometheus.yaml
"${compose[@]}" up -d --build
"${compose[@]}" run --rm -T --no-deps --user root \
  -v "$PWD/.runtime/observability:/run/observability" \
  --entrypoint python revguard-api scripts/prepare_grafana_embed.py
