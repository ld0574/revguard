#!/usr/bin/env bash
# 统一的 Compose profile 入口。
#
# 这个脚本只负责选择 Compose 文件，不替代 scripts/deploy_demo.sh 的
# 迁移、播种、AgentTeams 和部署验收流程。
set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PROFILE="${1:-}"

usage() {
  cat <<'EOF'
用法：bash scripts/compose_profile.sh <profile> <docker compose args...>

Profile：
  local            SQLite 演示栈（docker-compose.yml）
  production       AgentTeams + PolarDB + 可观测性
  enterprise       production + ERPNext 企业网络覆盖层
  verify           一次性验证栈（项目 revguard-verify）

示例：
  bash scripts/compose_profile.sh local up -d --build
  bash scripts/compose_profile.sh production ps
  bash scripts/compose_profile.sh enterprise up -d --build
EOF
}

if [[ -z "$PROFILE" || "$PROFILE" == "-h" || "$PROFILE" == "--help" ]]; then
  usage
  exit 0
fi
shift

files=()
project=""
case "$PROFILE" in
  local)
    files=(docker-compose.yml)
    ;;
  production)
    files=(docker-compose.yml docker-compose.agentteams.yml docker-compose.polardb.yml docker-compose.observability.yml)
    ;;
  enterprise)
    files=(docker-compose.yml docker-compose.agentteams.yml docker-compose.polardb.yml docker-compose.observability.yml docker-compose.enterprise.yml)
    ;;
  verify)
    files=(docker-compose.verify.yml)
    project="revguard-verify"
    ;;
  *)
    echo "未知 profile：$PROFILE" >&2
    usage >&2
    exit 2
    ;;
esac

compose=(docker compose --project-directory "$ROOT_DIR")
if [[ -n "${COMPOSE_PROJECT_NAME:-}" ]]; then
  compose+=(-p "$COMPOSE_PROJECT_NAME")
elif [[ -n "$project" ]]; then
  compose+=(-p "$project")
fi
for file in "${files[@]}"; do
  compose+=(-f "$ROOT_DIR/$file")
done

exec "${compose[@]}" "$@"
