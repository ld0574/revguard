#!/usr/bin/env bash
# Register actor-scoped RevGuard REST-to-MCP servers in AgentTeams Higress.
# Run on the AgentTeams host after all revguard-* workers exist.
set -Eeuo pipefail

REVGUARD_HOME="${REVGUARD_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CONTROLLER="${CONTROLLER:-agentteams-controller}"
MCP_DIR="$REVGUARD_HOME/agentteams/mcp/higress"
PRINCIPALS_FILE="${REVGUARD_PRINCIPALS_FILE:-$REVGUARD_HOME/config/demo_principals.json}"
WORKERS="revguard-intake revguard-evidence revguard-policy revguard-calculation revguard-rootcause revguard-risk revguard-executor revguard-verifier revguard-knowledge"

principal_for_actor() {
  python3 - "$1" "$PRINCIPALS_FILE" <<'PY'
import json
import sys

actor, path = sys.argv[1:]
with open(path, encoding="utf-8") as stream:
    principals = json.load(stream)
print(next(key for key, value in principals.items() if value["actor"] == actor))
PY
}

for command in docker python3; do
  command -v "$command" >/dev/null || { echo "缺少命令: $command" >&2; exit 1; }
done
docker inspect "$CONTROLLER" >/dev/null
test -f "$MCP_DIR/manifest.json"

echo "==> 1/4 同步 9 份 actor-scoped Higress MCP 定义"
docker exec "$CONTROLLER" rm -rf /tmp/revguard-higress-mcp
docker exec "$CONTROLLER" mkdir -p /tmp/revguard-higress-mcp
docker cp "$MCP_DIR/." "$CONTROLLER:/tmp/revguard-higress-mcp/"

echo "==> 2/4 注册 REST-to-MCP Server（后端凭证只进入 Higress）"
for worker in $WORKERS; do
  backend_key=$(principal_for_actor "$worker")
  docker exec -e REVGUARD_MCP_BACKEND_KEY="$backend_key" "$CONTROLLER" bash -lc '
    set -Eeuo pipefail
    source /opt/agentteams/scripts/lib/gateway-api.sh
    gateway_ensure_session
    bash /opt/agentteams/agent/skills/mcp-server-management/scripts/setup-mcp-server.sh \
      "'"$worker"'" "$REVGUARD_MCP_BACKEND_KEY" \
      --yaml-file "/tmp/revguard-higress-mcp/'"$worker"'.yaml"
  '
  unset backend_key
done

echo "==> 3/4 收紧 consumer allowlist，并给每个 Worker 只下发自己的 MCP Server"
docker exec -e REVGUARD_MCP_WORKERS="$WORKERS" "$CONTROLLER" bash -lc '
  set -Eeuo pipefail
  source /opt/agentteams/scripts/lib/agentteams-env.sh
  source /opt/agentteams/scripts/lib/gateway-api.sh
  gateway_ensure_session
  domain="${AGENTTEAMS_AI_GATEWAY_DOMAIN:-aigw-local.agentteams.io}"
  for worker in $REVGUARD_MCP_WORKERS; do
    server="mcp-$worker"
    body=$(jq -n --arg server "$server" --arg consumer "worker-$worker" \
      "{mcpServerName:\$server,consumers:[\$consumer]}")
    response=$(curl -fsS -X PUT "http://127.0.0.1:8001/v1/mcpServer/consumers" \
      -b "$HIGRESS_COOKIE_FILE" -H "Content-Type: application/json" -d "$body")
    echo "$response" | jq -e ".success != false" >/dev/null

    creds="/data/worker-creds/$worker.env"
    worker_key=$(sed -n "s/^WORKER_GATEWAY_KEY=\"\(.*\)\"$/\1/p" "$creds")
    test -n "$worker_key"
    config_dir="/root/agentteams-fs/agents/$worker/config"
    config="$config_dir/mcporter.json"
    mkdir -p "$config_dir"
    jq -n --arg server "$server" --arg domain "$domain" --arg key "$worker_key" \
      "{mcpServers:{(\$server):{url:(\"http://\"+\$domain+\":8080/mcp-servers/\"+\$server+\"/mcp\"),transport:\"http\",headers:{Authorization:(\"Bearer \"+\$key)}}}}" \
      > "$config"
    ln -sfn "$config" "/root/agentteams-fs/agents/$worker/mcporter-servers.json"
    mc cp "$config" "${AGENTTEAMS_STORAGE_PREFIX}/agents/$worker/config/mcporter.json" >/dev/null
  done
'

# Full mode must fail closed: business Workers no longer retain a direct RevGuard API key.
for worker in $WORKERS; do
  docker exec "$CONTROLLER" mc rm --force \
    "agentteams/agentteams-storage/agents/$worker/.copaw.secret/revguard_api_key" \
    >/dev/null 2>&1 || true
  container="agentteams-worker-$worker"
  docker exec "$container" rm -f \
    "/root/.copaw-worker/$worker/.copaw.secret/revguard_api_key"
  docker exec "$CONTROLLER" cat "/root/agentteams-fs/agents/$worker/config/mcporter.json" \
    | docker exec -i "$container" sh -lc \
      'mkdir -p config && tee config/mcporter.json >/dev/null'
done

echo "==> 4/4 核验每个 Worker 只能发现自己的 MCP Server"
for worker in $WORKERS; do
  container="agentteams-worker-$worker"
  for _ in $(seq 1 30); do
    count=$(docker exec "$container" sh -lc \
      'test -f config/mcporter.json && jq ".mcpServers | length" config/mcporter.json || echo 0' \
      2>/dev/null || echo 0)
    [ "$count" = "1" ] && break
    sleep 2
  done
  [ "$count" = "1" ] || { echo "$worker MCP 配置未同步" >&2; exit 1; }
  configured=$(docker exec "$container" jq -r '.mcpServers | keys[0]' config/mcporter.json)
  [ "$configured" = "mcp-$worker" ] || {
    echo "$worker 意外获得 MCP Server: $configured" >&2
    exit 1
  }
done

echo "Higress MCP Gateway 已完成：9 个独立 Server，9 个单 Worker consumer allowlist。"
