#!/bin/bash
# RevGuard × AgentTeams 一键联调脚本（在 AgentTeams 宿主机上执行）。
#
# 前置条件：
#   1. RevGuard API 已部署：cd /root/revguard && docker compose up -d
#   2. agentteams-controller 容器运行中，容器内 agt 可用
#
# 用法：bash scripts/agentteams_setup.sh
# 幂等：重复执行安全（agt apply 为 create-or-update 语义）。
set -euo pipefail

REVGUARD_HOME="${REVGUARD_HOME:-/root/revguard}"
CONTROLLER="${CONTROLLER:-agentteams-controller}"
MODEL="${MODEL:-moonshotai/kimi-k3}"
REVGUARD_API_BASE_URL="${REVGUARD_API_BASE_URL:-http://revguard-api:9000}"
WORKER_CONTAINER_PREFIX="${WORKER_CONTAINER_PREFIX:-agentteams-worker-}"
INSTALL_WORKER_SKILLS="${INSTALL_WORKER_SKILLS:-true}"
TEAM="revguard-team"
WORKERS="orchestrator intake evidence policy calculation rootcause risk executor verifier knowledge"
SKILL_WORKERS="orchestrator intake evidence policy calculation rootcause risk executor verifier knowledge"

TMP_SOUL_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_SOUL_DIR"' EXIT

echo "==> 1/5 渲染并同步 SOUL 文件到 controller 容器"
for w in $WORKERS; do
  sed \
    -e "s|{{REVGUARD_API_BASE_URL}}|$REVGUARD_API_BASE_URL|g" \
    "$REVGUARD_HOME/agentteams/workers/revguard-$w.md" \
    > "$TMP_SOUL_DIR/revguard-$w.md"
done
docker exec "$CONTROLLER" mkdir -p /tmp/agentteams/workers
docker cp "$TMP_SOUL_DIR/." "$CONTROLLER:/tmp/agentteams/workers/"

echo "==> 2/5 创建/更新 10 个 Worker（model=$MODEL）"
for w in $WORKERS; do
  docker exec "$CONTROLLER" agt apply worker \
    --name "revguard-$w" \
    --soul-file "/tmp/agentteams/workers/revguard-$w.md" \
    --model "$MODEL" | tail -1
done

echo "==> 3/5 组建 Team（leader=revguard-orchestrator）"
TEAM_PHASE=$(docker exec "$CONTROLLER" agt get teams 2>/dev/null | awk -v n="$TEAM" '$1==n {print $2}')
if [ "$TEAM_PHASE" = "Active" ]; then
  echo "Team $TEAM 已 Active，跳过重建"
else
  # 仅在非 Active（不存在/Failed/Pending）时重建；删除是异步的，必须等彻底消失再建，否则 409
  if [ -n "$TEAM_PHASE" ]; then
    docker exec "$CONTROLLER" agt delete team "$TEAM" || true
    for _ in $(seq 1 12); do
      sleep 5
      docker exec "$CONTROLLER" agt get teams 2>/dev/null | grep -q "$TEAM" || break
    done
  fi
  docker exec "$CONTROLLER" agt create team \
    --name "$TEAM" \
    --leader-name revguard-orchestrator \
    --workers revguard-intake,revguard-evidence,revguard-policy,revguard-calculation,revguard-rootcause,revguard-risk,revguard-executor,revguard-verifier,revguard-knowledge \
    --description "RevGuard 渠道佣金结算异常多 Agent 协同"
fi

echo "==> 4/5 安装 skills-only RevGuard Adapter"
if [ "$INSTALL_WORKER_SKILLS" = "true" ]; then
  for w in $SKILL_WORKERS; do
    worker="revguard-$w"
    container="${WORKER_CONTAINER_PREFIX}${worker}"
    docker exec "$CONTROLLER" agt worker ensure-ready --name "$worker" >/dev/null
    for _ in $(seq 1 30); do
      docker inspect "$container" >/dev/null 2>&1 && break
      sleep 2
    done
    docker inspect "$container" >/dev/null 2>&1 || {
      echo "Worker 容器未就绪: $container" >&2
      exit 1
    }
    skill_dir="/root/.copaw-worker/$worker/skills/revguard-api"
    docker exec "$container" mkdir -p "$skill_dir"
    docker cp "$REVGUARD_HOME/agentteams/skills/revguard-api/." "$container:$skill_dir/"
    docker exec "$container" chmod 700 "$skill_dir/scripts/revguard_call.py"
  done
else
  echo "INSTALL_WORKER_SKILLS=false，跳过 Adapter 安装"
fi

echo "==> 5/5 状态核验"
docker exec "$CONTROLLER" agt get workers
docker exec "$CONTROLLER" agt get teams

echo
echo "完成。在 Element Web（http://<host>:8088）进入 revguard-team 聊天室即可演示。"
echo "Worker 通过 $REVGUARD_API_BASE_URL/api/v1/skills/{name}/invoke 调用 RevGuard Skill 层。"
echo "API key 必须由 AgentTeams Secret/Adapter 注入，禁止写入 SOUL 或聊天消息。"
