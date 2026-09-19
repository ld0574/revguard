#!/usr/bin/env bash
# RevGuard 可复现部署总入口。
#
# 在企业内网生产环境（10.10.10.202）运行最小闭环：bash scripts/deploy_demo.sh
# 决赛完整环境（保留案件）：bash scripts/deploy_demo.sh --full
set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ENV_FILE="$ROOT_DIR/.env"
PROFILE="local"
RESET="false"
OBSERVABILITY="false"
MODEL="${AGENTTEAMS_DEFAULT_MODEL:-}"
if [ -z "$MODEL" ] && [ -f "$ENV_FILE" ]; then
  MODEL=$(sed -n 's/^AGENTTEAMS_DEFAULT_MODEL=//p' "$ENV_FILE" | tail -n 1)
fi
MODEL="${MODEL:-glm-5.3-flash}"

usage() {
  cat <<'EOF'
用法：bash scripts/deploy_demo.sh [选项]

选项：
  --local          SQLite + MCP 参考链路 + WebUI（默认，只需要 Docker）
  --full           PolarDB-PG + AgentTeams Matrix + WebUI + 可观测组件
  --observability  在最小拓扑中也部署 Grafana、Prometheus、日志与 Trace 后端
  --reset          清空合成演示状态并重新播种 8 个 Golden Case
  --model NAME     AgentTeams Worker 模型（默认 glm-5.3-flash）
  -h, --help       显示帮助

示例：
  bash scripts/deploy_demo.sh
  bash scripts/deploy_demo.sh --full --model glm-5.3-flash
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

for command in docker curl openssl python3 flock; do
  command -v "$command" >/dev/null 2>&1 || fail "缺少命令: $command"
done
docker compose version >/dev/null 2>&1 || fail "需要 Docker Compose v2（docker compose）"

cd "$ROOT_DIR"
umask 077
# The supported CLI uses the host's local daemon. Do not unlink the lock inode.
exec 9>/tmp/revguard-deployment.lock
flock -n 9 || fail "另一个部署仍在进行；不允许并发修改配置或替换服务"
deployment_owner=$(openssl rand -hex 16)
project=$(docker compose -f docker-compose.yml config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')
python3 scripts/quiesce_api.py preflight --profile "$PROFILE" --project "$project"
if [ "$PROFILE" = "full" ]; then
  [ "$(docker inspect -f '{{.State.Running}}' agentteams-controller 2>/dev/null || true)" = "true" ] \
    || fail "请先安装并启动 AgentTeams v1.2.0；本脚本不安装 Controller"
  docker network inspect agentteams-net >/dev/null 2>&1 || fail "未找到 agentteams-net"
  docker exec agentteams-controller agt get teams >/dev/null 2>&1 || fail "AgentTeams Controller 尚未就绪"
  available_gb=$(docker info --format '{{json .MemTotal}}' | tr -d '"' | awk '{printf "%d", $1/1024/1024/1024}')
  [ "$available_gb" -ge 6 ] || fail "完整环境至少需要 6 GiB Docker 内存，当前约 ${available_gb} GiB"
fi
# A routine update must not silently remove previously installed observability.
previous_compose=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.config_files"}}' revguard-api 2>/dev/null || true)
case "$previous_compose" in *docker-compose.observability.yml*) OBSERVABILITY="true" ;; esac
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
    curl -fsS http://127.0.0.1:19000/api/v1/health/live >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

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
  env_set_if_missing REVGUARD_POLARDB_USER revguard_owner
  env_set_if_missing REVGUARD_POLARDB_PASSWORD "$(openssl rand -hex 24)"
  env_set REVGUARD_ALLOW_DATABASE_RESET true
  env_set REVGUARD_TEAM_TRANSPORT matrix
  docker run --rm --user root -v "$ROOT_DIR:/workspace" -w /workspace \
    --entrypoint python python:3.11-slim scripts/configure_demo_principals.py --env /workspace/.env
fi
if [ "$(env_get REVGUARD_ENTERPRISE_PROVIDER)" = "erpnext" ] \
   && docker network inspect revguard-enterprise >/dev/null 2>&1; then
  log "接入 ERPNext 真实企业系统网络（revguard-enterprise）"
  compose+=(-f docker-compose.enterprise.yml)
else
  env_set REVGUARD_ENTERPRISE_PROVIDER mock
fi
if [ "$OBSERVABILITY" = "true" ]; then
  docker run --rm --user root -v "$ROOT_DIR:/workspace" -w /workspace \
    --entrypoint python python:3.11-slim scripts/prepare_observability.py
  compose+=(-f docker-compose.observability.yml)
fi

if [ "$PROFILE" = "local" ]; then
  env_set REVGUARD_TEAM_TRANSPORT mcp
fi

log "先构建，再确认静止并停止旧 API"
"${compose[@]}" build revguard-api
python3 scripts/quiesce_api.py stop --profile "$PROFILE" --project "$project" --owner "$deployment_owner"
log "保存跨重启维护标记；失败后重跑部署，验收通过才开放业务"
"${compose[@]}" run --rm -T --no-deps --entrypoint python revguard-api -c \
  'from revguard.deployment import prepare_fence; import sys; prepare_fence(sys.argv[1])' "$deployment_owner"

if [ "$PROFILE" = "local" ]; then
  log "准备 SQLite + MCP 参考拓扑"
else
  log "启动 PolarDB-PG 并应用核心 Schema"
  "${compose[@]}" up -d polardb-pg
  wait_container_healthy revguard-polardb 90 || fail "PolarDB 未通过健康检查"
  docker exec revguard-polardb sh -lc \
    'PGPASSWORD="$POLARDB_PASSWORD" psql -v ON_ERROR_STOP=1 -U "$POLARDB_USER" -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='"'"'revguard'"'"'" | grep -q 1 || PGPASSWORD="$POLARDB_PASSWORD" createdb -U "$POLARDB_USER" revguard'
  log "构建镜像并以短时容器完整迁移核心与资金恢复 Schema"
  "${compose[@]}" run --rm -T --no-deps --entrypoint python revguard-api -c \
    'import os; os.environ["REVGUARD_MIGRATION_DATABASE_URL"] = os.environ["REVGUARD_DATABASE_URL"]; from scripts.migrate_polardb import main; main()'
fi

log "停机状态下检查持久化任务，并播种合成案件"
"${compose[@]}" run --rm -T --no-deps --entrypoint python revguard-api -c '
import sys
from revguard.api import store,gateway
from revguard.runtime_barrier import acquire_runtime_lease,assert_recording_quiescent
from scripts.seed_demo import seed_store
with acquire_runtime_lease(store,exclusive=True):
    assert_recording_quiescent(store,gateway.journal)
    seed_store(store,reset=sys.argv[1]=="true",quiet=True,gateway=gateway,
               reset_actor="deployment-operator")
' "$RESET"
"${compose[@]}" up -d --no-build revguard-api
wait_api || fail "RevGuard API 进程未就绪；维护标记保留"

if [ "$PROFILE" = "full" ]; then
  log "创建/更新 AgentTeams 角色、Team、Adapter 与 Matrix 房间"
  gateway_base=$(env_get AGENTTEAMS_OPENAI_BASE_URL)
  gateway_key=$(env_get AGENTTEAMS_LLM_API_KEY)
  REVGUARD_HOME="$ROOT_DIR" \
  REVGUARD_API_BASE_URL=http://revguard-api:9000 \
  MODEL="$MODEL" \
  AGENTTEAMS_OPENAI_BASE_URL_OVERRIDE="$gateway_base" \
  AGENTTEAMS_LLM_API_KEY_OVERRIDE="$gateway_key" \
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

log "执行部署验收"
health=$(curl -fsS http://127.0.0.1:19000/api/v1/health)
cases=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("cases", 0))' <<<"$health")
docker exec revguard-api python -c 'from revguard.api import store; assert store.readiness()["ready"]' \
  || fail "数据库未就绪；维护标记保留"
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

log "全部验收通过，最后解除维护封锁"
docker exec revguard-api python -c '
import sys,time
from revguard.api import store,gateway
from revguard.deployment import release_fence
from revguard.runtime_barrier import acquire_runtime_lease,assert_recording_quiescent,RuntimeBusy
for attempt in range(40):
    try:
        with acquire_runtime_lease(store,exclusive=True):
            assert_recording_quiescent(store,gateway.journal)
            release_fence(sys.argv[1])
        break
    except RuntimeBusy:
        if attempt==39: raise
        time.sleep(.2)
' "$deployment_owner"

echo
echo "部署完成："
echo "  WebUI  http://127.0.0.1:19000/demo/"
echo "  Health http://127.0.0.1:19000/api/v1/health"
echo "  模式    $PROFILE"
echo "  案件    $cases"
echo "  配置    $ENV_FILE（权限 0600，请勿提交）"
