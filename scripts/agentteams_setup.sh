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
TEAM="revguard-team"
WORKERS="orchestrator intake evidence policy calculation rootcause risk executor verifier knowledge"

echo "==> 1/4 同步 SOUL 文件到 controller 容器"
docker cp "$REVGUARD_HOME/agentteams/workers" "$CONTROLLER:/tmp/agentteams/"

echo "==> 2/4 创建/更新 10 个 Worker（model=$MODEL）"
for w in $WORKERS; do
  docker exec "$CONTROLLER" agt apply worker \
    --name "revguard-$w" \
    --soul-file "/tmp/agentteams/workers/revguard-$w.md" \
    --model "$MODEL" | tail -1
done

echo "==> 3/4 组建 Team（leader=revguard-orchestrator）"
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

echo "==> 4/4 状态核验"
docker exec "$CONTROLLER" agt get workers
docker exec "$CONTROLLER" agt get teams

echo
echo "完成。在 Element Web（http://<host>:8088）进入 revguard-team 聊天室即可演示。"
echo "Worker 通过 http://10.10.10.202:19000/api/v1/tools/call 调用 RevGuard Skill 层。"
