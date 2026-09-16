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
  python3 "$REVGUARD_HOME/scripts/configure_demo_principals.py" \
    --env "$REVGUARD_HOME/.env" --template "$PRINCIPALS_FILE" --lookup "$1"
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
    worker="'"$worker"'"
    server="mcp-$worker"
    domain="${AGENTTEAMS_AI_GATEWAY_DOMAIN:-aigw-local.agentteams.io}"
    # The bundled setup script grants every Worker access. Register directly so
    # a deployment never temporarily grants executor tools to other roles.
    request() {
      local method="$1" path="$2" body="$3" response
      response=$(curl -fsS -X "$method" "http://127.0.0.1:8001$path" \
        -b "$HIGRESS_COOKIE_FILE" -H "Content-Type: application/json" -d "$body")
      if [ -n "$response" ]; then
        echo "$response" | jq -e ".success != false" >/dev/null || {
          echo "Higress $method $path failed (response withheld: may contain credentials)" >&2
          return 1
        }
      fi
    }
    source_body=$(jq -n --arg name "$worker-api" \
      "{type:\"dns\",name:\$name,domain:\"revguard-api.internal\",port:9000,protocol:\"http\"}")
    # Existing DNS sources are idempotent; only HTTP 409 is an allowed failure.
    code=$(curl -sS -o /dev/null -w "%{http_code}" -X POST \
      http://127.0.0.1:8001/v1/service-sources -b "$HIGRESS_COOKIE_FILE" \
      -H "Content-Type: application/json" -d "$source_body")
    case "$code" in 200|201|204|409) ;; *) echo "DNS source failed: HTTP $code" >&2; exit 1;; esac
    body=$(jq -n --arg server "$server" --arg domain "$domain" --arg worker "$worker" \
      --rawfile yaml "/tmp/revguard-higress-mcp/$worker.yaml" \
      --arg key "$REVGUARD_MCP_BACKEND_KEY" \
      "{name:\$server,description:\$server,type:\"OPEN_API\",mcpServerName:\$server,
        rawConfigurations:(\$yaml | sub(\"accessToken: \\\"\\\"\"; \"accessToken: \"+(\$key|tojson))),
        domains:[\$domain],services:[{name:(\$worker+\"-api.dns\"),port:9000,weight:100}],
        consumerAuthInfo:{type:\"key-auth\",enable:true,allowedConsumers:[\"worker-\"+\$worker]}}")
    request PUT /v1/mcpServer "$body"
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
    # PUT /consumers ADDS; it does not replace the allowlist in this version.
    # Revoke only excess grants on this exact RevGuard server (never globally).
    detail=$(curl -fsS -b "$HIGRESS_COOKIE_FILE" "http://127.0.0.1:8001/v1/mcpServer/$server")
    excess=$(echo "$detail" | jq -c --arg consumer "worker-$worker" \
      ".data.consumerAuthInfo.allowedConsumers | map(select(. != \$consumer))")
    if [ "$excess" != "[]" ]; then
      revoke=$(jq -n --arg server "$server" --argjson consumers "$excess" \
        "{mcpServerName:\$server,consumers:\$consumers}")
      response=$(curl -fsS -X DELETE "http://127.0.0.1:8001/v1/mcpServer/consumers" \
        -b "$HIGRESS_COOKIE_FILE" -H "Content-Type: application/json" -d "$revoke")
      [ -z "$response" ] || echo "$response" | jq -e ".success != false" >/dev/null
    fi
    body=$(jq -n --arg server "$server" --arg consumer "worker-$worker" \
      "{mcpServerName:\$server,consumers:[\$consumer]}")
    response=$(curl -fsS -X PUT "http://127.0.0.1:8001/v1/mcpServer/consumers" \
      -b "$HIGRESS_COOKIE_FILE" -H "Content-Type: application/json" -d "$body")
    [ -z "$response" ] || echo "$response" | jq -e ".success != false" >/dev/null
    curl -fsS -b "$HIGRESS_COOKIE_FILE" "http://127.0.0.1:8001/v1/mcpServer/$server" \
      | jq -e --arg consumer "worker-$worker" \
        ".data.consumerAuthInfo | .enable == true and .allowedConsumers == [\$consumer]" >/dev/null

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

echo "==> 4/4 核验配置与真实 MCP 访问隔离（含跨角色拒绝）"
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

python3 "$REVGUARD_HOME/scripts/verify_higress_isolation.py"

echo "Higress MCP Gateway 已完成：9 个独立 Server，9 个单 Worker consumer allowlist。"
