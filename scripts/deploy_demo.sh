#!/usr/bin/env bash
# RevGuard 可复现部署总入口。
#
# 在 202 Docker 环境运行最小闭环：bash scripts/deploy_demo.sh
# 决赛完整环境（保留案件）：bash scripts/deploy_demo.sh --full
set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ENV_FILE="$ROOT_DIR/.env"
PROFILE="local"
RESET="false"
OBSERVABILITY="false"
MODEL="${AGENTTEAMS_DEFAULT_MODEL:-gpt-5.6-sol}"

usage() {
  cat <<'EOF'
用法：bash scripts/deploy_demo.sh [选项]

选项：
  --local          SQLite + 本地 MCP + WebUI（默认，只需要 Docker）
  --full           PolarDB-PG + AgentTeams Matrix + WebUI + 可观测组件
  --observability  在最小拓扑中也部署 Grafana、Prometheus、日志与 Trace 后端
  --reset          清空合成演示状态并重新播种 8 个 Golden Case
  --model NAME     AgentTeams Worker 模型（默认 gpt-5.6-sol）
  -h, --help       显示帮助

示例：
  bash scripts/deploy_demo.sh
  bash scripts/deploy_demo.sh --full --model gpt-5.6-sol
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --local) PROFILE="local" ;;
    --full) PROFILE="full"; OBSERVABILITY="true" ;;
    --observability) OBSERVABILITY="true" ;;
    --reset) RESET="true" ;;
    --model)
      [ "$#" -ge 2 ] || { echo "--model 缺少参数" >&2; exit 2; }
      MODEL="$2"
      shift
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

log() { printf '\n==> %s\n' "$*"; }
fail() { echo "部署失败: $*" >&2; exit 1; }

on_error() {
  code=$?
  echo >&2
  echo "部署在第 $1 行失败（exit=$code）。" >&2
  echo "诊断：docker compose logs --tail=200 revguard-api" >&2
  [ "$PROFILE" = "full" ] && echo "AgentTeams：docker exec agentteams-controller agt get teams" >&2
  exit "$code"
}
trap 'on_error $LINENO' ERR

for command in docker curl openssl python3; do
  command -v "$command" >/dev/null 2>&1 || fail "缺少命令: $command"
done
docker compose version >/dev/null 2>&1 || fail "需要 Docker Compose v2（docker compose）"

cd "$ROOT_DIR"
umask 077
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

env_get() {
  python3 - "$ENV_FILE" "$1" <<'PY'
import sys
from pathlib import Path

path, wanted = Path(sys.argv[1]), sys.argv[2]
for line in path.read_text(encoding="utf-8").splitlines():
    if line.startswith(wanted + "="):
        print(line.split("=", 1)[1])
        break
PY
}

env_set() {
  python3 - "$ENV_FILE" "$1" "$2" <<'PY'
import sys
from pathlib import Path

path, key, value = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
result = []
replaced = False
for line in lines:
    if line.startswith(key + "="):
        result.append(f"{key}={value}")
        replaced = True
    else:
        result.append(line)
if not replaced:
    result.append(f"{key}={value}")
path.write_text("\n".join(result) + "\n", encoding="utf-8")
path.chmod(0o600)
PY
}

env_set_if_missing() {
  [ -n "$(env_get "$1")" ] || env_set "$1" "$2"
}

wait_container_healthy() {
  container="$1"
  attempts="${2:-90}"
  for _ in $(seq 1 "$attempts"); do
    state=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)
    [ "$state" = "healthy" ] && return 0
    [ "$state" = "exited" ] && return 1
    sleep 2
  done
  return 1
}

wait_api() {
  for _ in $(seq 1 90); do
    curl -fsS http://127.0.0.1:19000/api/v1/health >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

guard_no_active_runs() {
  if ! docker inspect -f '{{.State.Running}}' revguard-api 2>/dev/null | grep -q true; then
    return 0
  fi
  if ! active_runs=$(docker exec revguard-api python -c '
from revguard.api import store
active = {"QUEUED", "STARTING", "RUNNING"}
print(",".join(
    case["case_id"] for case in store.list_cases()
    if (case.get("team_run") or {}).get("status") in active
))
' 2>/dev/null); then
    fail "无法核实当前案件运行状态，停止部署；请先恢复数据库并确认运行已结束"
  fi
  [ -z "$active_runs" ] || fail \
    "检测到活动 AgentTeams 运行: $active_runs；请等待结束，不能以 reset 绕过在途资金操作"
}

guard_no_active_runs

release_version=$(docker run --rm --user root -v "$ROOT_DIR:/workspace:ro" \
  --entrypoint python python:3.11-slim -c \
  'import tomllib; print(tomllib.load(open("/workspace/pyproject.toml", "rb"))["project"]["version"])')
env_set REVGUARD_RELEASE_VERSION "$release_version"
env_set_if_missing REVGUARD_APPROVAL_SIGNING_KEY "$(openssl rand -hex 32)"
env_set REVGUARD_ALLOW_INSECURE_DEMO_KEYS true
env_set REVGUARD_ENABLE_LEGACY_TOOL_API false
env_set REVGUARD_ENABLE_RECORDING_UI true
env_set REVGUARD_RESET_ON_START false
env_set_if_missing REVGUARD_VERIFICATION_TAMPER_AMOUNT 0
env_set_if_missing REVGUARD_POSTING_TAMPER_AMOUNT 0
env_set_if_missing REVGUARD_TEAM_RUN_STALE_AFTER_SECONDS 600

compose=(docker compose -f docker-compose.yml)
if [ "$PROFILE" = "full" ]; then
  compose+=(-f docker-compose.agentteams.yml -f docker-compose.polardb.yml)
  docker run --rm --user root -v "$ROOT_DIR:/workspace" -w /workspace \
    --entrypoint python python:3.11-slim scripts/configure_demo_principals.py --env /workspace/.env
fi
if [ "$OBSERVABILITY" = "true" ]; then
  docker run --rm --user root -v "$ROOT_DIR:/workspace" -w /workspace \
    --entrypoint python python:3.11-slim scripts/prepare_observability.py
  compose+=(-f docker-compose.observability.yml)
fi

if [ "$PROFILE" = "local" ]; then
  log "部署本地可复现环境（SQLite + MCP Team）"
  env_set REVGUARD_TEAM_TRANSPORT mcp
  if [ "$RESET" = "true" ]; then
    REVGUARD_RESET_ON_START=true "${compose[@]}" up -d --build revguard-api
    wait_api || fail "API 未就绪"
    "${compose[@]}" up -d --force-recreate --no-deps revguard-api
  else
    "${compose[@]}" up -d --build revguard-api
  fi
else
  log "核验 AgentTeams 与宿主机资源"
  docker inspect agentteams-controller >/dev/null 2>&1 || fail "未找到 agentteams-controller；请先安装 AgentTeams v1.2.0"
  docker network inspect agentteams-net >/dev/null 2>&1 || fail "未找到 agentteams-net"
  available_gb=$(docker info --format '{{json .MemTotal}}' | tr -d '"' | awk '{printf "%d", $1/1024/1024/1024}')
  [ "$available_gb" -ge 6 ] || fail "完整环境建议至少 6 GiB Docker 内存，当前约 ${available_gb} GiB"

  env_set_if_missing REVGUARD_POLARDB_USER revguard_owner
  env_set_if_missing REVGUARD_POLARDB_PASSWORD "$(openssl rand -hex 24)"
  env_set REVGUARD_ALLOW_DATABASE_RESET true
  env_set REVGUARD_TEAM_TRANSPORT matrix

  log "启动 PolarDB-PG 并应用核心 Schema"
  "${compose[@]}" up -d polardb-pg
  wait_container_healthy revguard-polardb 90 || fail "PolarDB 未通过健康检查"
  docker exec revguard-polardb sh -lc \
    'PGPASSWORD="$POLARDB_PASSWORD" psql -v ON_ERROR_STOP=1 -U "$POLARDB_USER" -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='"'"'revguard'"'"'" | grep -q 1 || PGPASSWORD="$POLARDB_PASSWORD" createdb -U "$POLARDB_USER" revguard'
  log "构建镜像并以短时容器完整迁移核心与资金恢复 Schema"
  "${compose[@]}" build revguard-api
  "${compose[@]}" run --rm -T --no-deps --entrypoint python revguard-api -c \
    'import os; os.environ["REVGUARD_MIGRATION_DATABASE_URL"] = os.environ["REVGUARD_DATABASE_URL"]; from scripts.migrate_polardb import main; main()'
  "${compose[@]}" up -d --no-build revguard-api
  wait_api || fail "RevGuard API 未就绪"

  log "创建/更新 AgentTeams 角色、Team、Adapter 与 Matrix 房间"
  REVGUARD_HOME="$ROOT_DIR" \
  REVGUARD_API_BASE_URL=http://revguard-api:9000 \
  MODEL="$MODEL" \
  bash scripts/agentteams_setup.sh

  log "重新加载自动发现的 Matrix 配置"
  "${compose[@]}" up -d --force-recreate --no-deps revguard-api
fi

if [ "$OBSERVABILITY" = "true" ]; then
  log "校验并启动可观测组件和只读 Grafana 共享看板"
  "${compose[@]}" run --rm --no-deps --entrypoint promtool prometheus check config /etc/prometheus/prometheus.yaml
  "${compose[@]}" up -d --no-build
  "${compose[@]}" run --rm -T --no-deps --user root \
    -v "$PWD/.runtime/observability:/run/observability" \
    --entrypoint python revguard-api scripts/prepare_grafana_embed.py
fi

wait_api || fail "RevGuard API 未在时限内就绪"

case_count=$(curl -fsS http://127.0.0.1:19000/api/v1/health | python3 -c 'import json,sys; print(json.load(sys.stdin).get("cases", 0))')
if [ "$RESET" = "true" ]; then
  log "按显式 reset 请求重置并播种 8 个合成 Golden Case"
  curl -fsS -X POST \
    -H 'Authorization: Bearer rg-demo-operator-key' \
    http://127.0.0.1:19000/api/v1/demo/reset >/dev/null
elif [ "$case_count" = "0" ]; then
  log "补充首次部署的 Golden Case（不清空已有账务或审计）"
  docker exec revguard-api python -c \
    'from revguard.api import store; from scripts.seed_demo import seed_store; seed_store(store, quiet=True)'
fi

log "执行部署验收"
health=$(curl -fsS http://127.0.0.1:19000/api/v1/health)
ready=$(python3 -c 'import json,sys; print(str(json.load(sys.stdin).get("ready", False)).lower())' <<<"$health")
cases=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("cases", 0))' <<<"$health")
[ "$ready" = "true" ] || fail "API ready=false"
[ "$cases" -ge 8 ] || fail "Golden Case 数量不足: $cases"
curl -fsS http://127.0.0.1:19000/demo/ >/dev/null || fail "WebUI 不可访问"

if [ "$PROFILE" = "full" ]; then
  backend=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("backend", ""))' <<<"$health")
  [ "$backend" = "postgresql-polardb" ] || fail "预期 PolarDB backend，实际为 $backend"
  team_ready=$(docker exec agentteams-controller agt get teams 2>/dev/null | awk '$1=="revguard-team" {print $NF}')
  [ "$team_ready" = "9/9" ] || fail "AgentTeams Team 未达到 9/9 Ready: ${team_ready:-missing}"
  docker exec agentteams-worker-revguard-executor \
    grep -q -- '--message-id-hex' \
    /root/.copaw-worker/revguard-executor/skills/revguard-api/scripts/revguard_call.py \
    || fail "Executor Adapter 未同步到最新版本"
fi

echo
echo "部署完成："
echo "  WebUI  http://127.0.0.1:19000/demo/"
echo "  Health http://127.0.0.1:19000/api/v1/health"
echo "  模式    $PROFILE"
echo "  案件    $cases"
echo "  配置    $ENV_FILE（权限 0600，请勿提交）"
