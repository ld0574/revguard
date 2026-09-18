#!/usr/bin/env bash
# Point the AgentTeams MCP bridge (revguard-api.internal) at one RevGuard stack.
#
#   202 hosts three RevGuard stacks that can share the AgentTeams network:
#     * prod : container revguard-api     (19000, 常驻演示栈)
#     * dev  : container revguard-api-dev (19088, 0.6.0 研发/验收栈)
#     * experiment : revguard-api-experiment (no host port, disposable ablation DB)
#
#   Docker DNS resolves one alias at a time, so exactly one stack may own
#   `revguard-api.internal`.  Workers then call that stack's Skill API and the
#   other stack's AgentTeams runs fail fast with `Agent task 不存在`.
#
# 用法（在 202 上执行）：
#   bash switch_agentteams_api_target.sh dev    # 决赛演示/研发：19088 rc2
#   bash switch_agentteams_api_target.sh prod   # 常驻演示：19000
#   bash switch_agentteams_api_target.sh experiment # 只供隔离消融实验使用
#   bash switch_agentteams_api_target.sh status
set -Eeuo pipefail

NETWORK="${AGENTTEAMS_NETWORK:-agentteams-net}"
CONTROLLER="${CONTROLLER:-agentteams-controller}"
DEV_CONTAINER="${DEV_CONTAINER:-revguard-api-dev}"
PROD_CONTAINER="${PROD_CONTAINER:-revguard-api}"
EXPERIMENT_CONTAINER="${EXPERIMENT_CONTAINER:-revguard-api-experiment}"
ALIAS="revguard-api.internal"
PROBE_WORKER="${PROBE_WORKER:-agentteams-worker-revguard-intake}"
PROBE_CONFIG="${PROBE_CONFIG:-/root/.copaw-worker/revguard-intake/config/mcporter.json}"

usage() {
  echo "用法: $0 {dev|prod|experiment|status}" >&2
  exit 2
}

target_container() {
  case "$1" in
    dev) echo "$DEV_CONTAINER" ;;
    prod) echo "$PROD_CONTAINER" ;;
    experiment) echo "$EXPERIMENT_CONTAINER" ;;
    *) usage ;;
  esac
}

has_alias() { # container
  docker inspect -f \
    '{{range $k, $v := .NetworkSettings.Networks}}{{if eq $k "'"$NETWORK"'"}}{{range $v.Aliases}}{{.}} {{end}}{{end}}{{end}}' \
    "$1" 2>/dev/null | grep -qw "$ALIAS"
}

resolved_release() {
  docker exec "$CONTROLLER" bash -lc \
    "curl -sS -m 8 http://$ALIAS:9000/api/v1/health" 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("release","?"))' 2>/dev/null \
    || echo "unreachable"
}

target_ip() {
  docker inspect -f \
    '{{with index .NetworkSettings.Networks "'"$NETWORK"'"}}{{.IPAddress}}{{end}}' \
    "$1"
}

gateway_endpoint_ips() {
  docker exec "$CONTROLLER" bash -lc \
    'curl -fsS http://127.0.0.1:15000/clusters?format=json' 2>/dev/null \
    | python3 -c '
import json
import sys
data = json.load(sys.stdin)
addresses = {
    host.get("address", {}).get("socket_address", {}).get("address")
    for cluster in data.get("cluster_statuses", [])
    for host in cluster.get("host_statuses", [])
    if host.get("hostname") == "revguard-api.internal"
}
print(" ".join(sorted(address for address in addresses if address)))
' 2>/dev/null || true
}

refresh_gateway_if_stale() {
  local expected_ip current_ips
  expected_ip=$(target_ip "$TARGET")
  current_ips=$(gateway_endpoint_ips)
  if [[ "$current_ips" == "$expected_ip" ]]; then
    return 0
  fi
  echo "==> 刷新 Higress 数据面 DNS（${current_ips:-未发现端点} -> $expected_ip）"
  docker exec "$CONTROLLER" bash -lc \
    'kill -TERM "$(pgrep -f "^/usr/local/bin/envoy -c etc/istio/proxy/envoy-rev.json" | head -n 1)"'
  for _ in $(seq 1 45); do
    [[ "$(gateway_endpoint_ips)" == "$expected_ip" ]] && return 0
    sleep 1
  done
  echo "FAIL: Higress 数据面未切到 $expected_ip（实际: $(gateway_endpoint_ips)）" >&2
  return 1
}

probe_gateway_target() {
  local output
  output=$(docker exec "$PROBE_WORKER" sh -lc \
    "cd /root/.copaw-worker/revguard-intake && mcporter call mcp-revguard-intake.BoundStageTask --args '{\"taskId\":\"TASK-SWITCH-PROBE\"}' --config '$PROBE_CONFIG' --output json" \
    2>&1 || true)
  if grep -q 'Agent task 不存在: TASK-SWITCH-PROBE' <<<"$output"; then
    echo "gateway : MCP read probe reached $TARGET"
    return 0
  fi
  echo "FAIL: MCP read probe did not reach $TARGET" >&2
  return 1
}

show_status() {
  local ips
  # 别名缺失时容器 DNS 会向上游解析同名公网记录，只信任私网地址。
  ips=$(docker exec "$CONTROLLER" bash -lc "getent hosts $ALIAS" 2>/dev/null \
        | awk '{print $1}' | grep -E '^(10\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.)' | tr '\n' ' ' || true)
  echo "alias   : $ALIAS -> ${ips:-未解析（私网地址缺失）}"
  echo "release : $(resolved_release)"
  for container in "$PROD_CONTAINER" "$DEV_CONTAINER" "$EXPERIMENT_CONTAINER"; do
    if has_alias "$container"; then echo "owner   : $container"; fi
  done
}

if [ $# -ne 1 ]; then usage; fi
if [ "$1" = "status" ]; then show_status; exit 0; fi

TARGET=$(target_container "$1")
docker inspect "$TARGET" >/dev/null

others=()
for container in "$PROD_CONTAINER" "$DEV_CONTAINER" "$EXPERIMENT_CONTAINER"; do
  [ "$container" = "$TARGET" ] || others+=("$container")
done

if ! has_alias "$TARGET"; then
  echo "==> $TARGET 绑定 $ALIAS"
  docker network disconnect "$NETWORK" "$TARGET" 2>/dev/null || true
  docker network connect --alias revguard-api --alias "$ALIAS" "$NETWORK" "$TARGET"
fi

for other in "${others[@]}"; do
  if has_alias "$other"; then
    echo "==> $other 释放 $ALIAS"
    docker network disconnect "$NETWORK" "$other"
  fi
done

sleep 1
refresh_gateway_if_stale
show_status
case "$1" in
  dev|experiment) expected="0.6" ;;
  prod) expected="0.5" ;;
esac
release=$(resolved_release)
case "$release" in
  "$expected"*)
    probe_gateway_target
    echo "OK: AgentTeams MCP 后端为 $1 栈（$release）"
    ;;
  *) echo "FAIL: 期望 $expected*，实际 $release" >&2; exit 1 ;;
esac
