#!/usr/bin/env bash
# Point the AgentTeams MCP bridge (revguard-api.internal) at one RevGuard stack.
#
#   202 hosts two isolated RevGuard stacks that share the AgentTeams network:
#     * prod : container revguard-api     (19000, 常驻演示栈)
#     * dev  : container revguard-api-dev (19088, 0.6.0 研发/验收栈)
#
#   Docker DNS resolves one alias at a time, so exactly one stack may own
#   `revguard-api.internal`.  Workers then call that stack's Skill API and the
#   other stack's AgentTeams runs fail fast with `Agent task 不存在`.
#
# 用法（在 202 上执行）：
#   bash switch_agentteams_api_target.sh dev    # 决赛彩排/研发：19088 rc2
#   bash switch_agentteams_api_target.sh prod   # 常驻演示：19000
#   bash switch_agentteams_api_target.sh status
set -Eeuo pipefail

NETWORK="${AGENTTEAMS_NETWORK:-agentteams-net}"
CONTROLLER="${CONTROLLER:-agentteams-controller}"
DEV_CONTAINER="${DEV_CONTAINER:-revguard-api-dev}"
PROD_CONTAINER="${PROD_CONTAINER:-revguard-api}"
ALIAS="revguard-api.internal"

usage() {
  echo "用法: $0 {dev|prod|status}" >&2
  exit 2
}

target_container() {
  case "$1" in
    dev) echo "$DEV_CONTAINER" ;;
    prod) echo "$PROD_CONTAINER" ;;
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

show_status() {
  local ips
  # 别名缺失时容器 DNS 会向上游解析同名公网记录，只信任私网地址。
  ips=$(docker exec "$CONTROLLER" bash -lc "getent hosts $ALIAS" 2>/dev/null \
        | awk '{print $1}' | grep -E '^(10\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.)' | tr '\n' ' ' || true)
  echo "alias   : $ALIAS -> ${ips:-未解析（私网地址缺失）}"
  echo "release : $(resolved_release)"
  for container in "$PROD_CONTAINER" "$DEV_CONTAINER"; do
    if has_alias "$container"; then echo "owner   : $container"; fi
  done
}

if [ $# -ne 1 ]; then usage; fi
if [ "$1" = "status" ]; then show_status; exit 0; fi

TARGET=$(target_container "$1")
OTHER="$DEV_CONTAINER"
[ "$TARGET" = "$DEV_CONTAINER" ] && OTHER="$PROD_CONTAINER"

docker inspect "$TARGET" >/dev/null
docker inspect "$OTHER" >/dev/null

if ! has_alias "$TARGET"; then
  echo "==> $TARGET 绑定 $ALIAS"
  docker network disconnect "$NETWORK" "$TARGET" 2>/dev/null || true
  docker network connect --alias revguard-api --alias "$ALIAS" "$NETWORK" "$TARGET"
fi

if has_alias "$OTHER"; then
  echo "==> $OTHER 释放 $ALIAS"
  docker network disconnect "$NETWORK" "$OTHER"
fi

sleep 1
show_status
case "$1" in
  dev) expected="0.6.0" ;;
  prod) expected="0.5" ;;
esac
release=$(resolved_release)
case "$release" in
  "$expected"*) echo "OK: AgentTeams MCP 后端为 $1 栈（$release）" ;;
  *) echo "FAIL: 期望 $expected*，实际 $release" >&2; exit 1 ;;
esac
