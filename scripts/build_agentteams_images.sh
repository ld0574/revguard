#!/usr/bin/env bash
# Run on 10.10.10.202. Builds only; does not restart AgentTeams.
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
WORKER_BASE="${AGENTTEAMS_WORKER_BASE_IMAGE:-higress-registry.cn-hangzhou.cr.aliyuncs.com/agentteams/agentteams-copaw-worker:v1.2.0}"
MANAGER_BASE="${AGENTTEAMS_MANAGER_BASE_IMAGE:-higress-registry.cn-hangzhou.cr.aliyuncs.com/agentteams/agentteams-manager-copaw:latest}"
docker build --pull=false --build-arg "BASE_IMAGE=$WORKER_BASE" \
  -t "revguard-agentteams-worker:${AGENTTEAMS_IMAGE_TAG:-luna-20260912}" "$ROOT_DIR/agentteams/copaw-runtime"
docker build --pull=false --build-arg "BASE_IMAGE=$MANAGER_BASE" \
  -t "revguard-agentteams-manager:${AGENTTEAMS_IMAGE_TAG:-luna-20260912}" "$ROOT_DIR/agentteams/copaw-runtime"
