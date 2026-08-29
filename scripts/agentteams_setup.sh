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
MODEL="${MODEL:-${AGENTTEAMS_DEFAULT_MODEL:-MiniMax-M3}}"
REVGUARD_API_BASE_URL="${REVGUARD_API_BASE_URL:-http://revguard-api:9000}"
WORKER_CONTAINER_PREFIX="${WORKER_CONTAINER_PREFIX:-agentteams-worker-}"
AGENTTEAMS_NETWORK="${AGENTTEAMS_NETWORK:-agentteams-net}"
INSTALL_WORKER_SKILLS="${INSTALL_WORKER_SKILLS:-true}"
CONFIGURE_MATRIX_WORKER_ROOMS="${CONFIGURE_MATRIX_WORKER_ROOMS:-true}"
REVGUARD_PRINCIPALS_FILE="${REVGUARD_PRINCIPALS_FILE:-$REVGUARD_HOME/config/demo_principals.json}"
TEAM="revguard-team"
WORKERS="orchestrator intake evidence policy calculation rootcause risk executor verifier knowledge"
SKILL_WORKERS="orchestrator intake evidence policy calculation rootcause risk executor verifier knowledge"

TMP_SOUL_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_SOUL_DIR"' EXIT

principal_for_actor() {
  python3 - "$1" "$REVGUARD_PRINCIPALS_FILE" <<'PY'
import json
import sys

actor, path = sys.argv[1:]
with open(path, encoding="utf-8") as stream:
    principals = json.load(stream)
print(next(key for key, value in principals.items() if value["actor"] == actor))
PY
}

echo "==> 0/6 核验 RevGuard API 与 AgentTeams 共用网络"
docker network inspect "$AGENTTEAMS_NETWORK" >/dev/null
docker inspect revguard-api >/dev/null
if ! docker network inspect -f '{{range .Containers}}{{.Name}}{{"\n"}}{{end}}' \
  "$AGENTTEAMS_NETWORK" | grep -qx 'revguard-api'; then
  docker network connect --alias revguard-api "$AGENTTEAMS_NETWORK" revguard-api
fi

echo "==> 1/6 渲染并同步 SOUL 文件到 controller 容器"
for w in $WORKERS; do
  sed \
    -e "s|{{REVGUARD_API_BASE_URL}}|$REVGUARD_API_BASE_URL|g" \
    "$REVGUARD_HOME/agentteams/workers/revguard-$w.md" \
    > "$TMP_SOUL_DIR/revguard-$w.md"
done
docker exec "$CONTROLLER" mkdir -p /tmp/agentteams/workers
docker cp "$TMP_SOUL_DIR/." "$CONTROLLER:/tmp/agentteams/workers/"

echo "==> 2/6 创建/更新 1 Orchestrator + 9 Worker（model=$MODEL）"
for w in $WORKERS; do
  docker exec "$CONTROLLER" agt apply worker \
    --name "revguard-$w" \
    --soul-file "/tmp/agentteams/workers/revguard-$w.md" \
    --model "$MODEL" | tail -1
done

echo "==> 3/6 组建 Team（leader=revguard-orchestrator）"
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

echo "==> 4/6 安装 skills-only RevGuard Adapter"
if [ "$INSTALL_WORKER_SKILLS" = "true" ]; then
  controller_skill_dir="/tmp/agentteams/skills/revguard-api"
  docker exec "$CONTROLLER" rm -rf "$controller_skill_dir"
  docker exec "$CONTROLLER" mkdir -p "$controller_skill_dir"
  docker cp "$REVGUARD_HOME/agentteams/skills/revguard-api/." \
    "$CONTROLLER:$controller_skill_dir/"
  for w in $SKILL_WORKERS; do
    worker="revguard-$w"
    # Worker 会以 MinIO 为单一事实源同步 skills；只拷容器临时层会在下一轮
    # mirror 时被清理，重启后也无法复现。
    docker exec "$CONTROLLER" mc mirror --overwrite \
      "$controller_skill_dir/" \
      "agentteams/agentteams-storage/agents/$worker/skills/revguard-api/" >/dev/null

    api_key=$(principal_for_actor "$worker")
    printf '%s' "$api_key" | docker exec -i "$CONTROLLER" mc pipe \
      "agentteams/agentteams-storage/agents/$worker/.copaw.secret/revguard_api_key" \
      >/dev/null
    unset api_key
  done

  echo "等待 Worker 从持久存储同步 Adapter"
  for w in $SKILL_WORKERS; do
    worker="revguard-$w"
    container="${WORKER_CONTAINER_PREFIX}${worker}"
    skill_script="/root/.copaw-worker/$worker/skills/revguard-api/scripts/revguard_call.py"
    principal_file="/root/.copaw-worker/$worker/.copaw.secret/revguard_api_key"
    docker exec "$CONTROLLER" agt worker ensure-ready --name "$worker" >/dev/null
    for _ in $(seq 1 35); do
      running=$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || true)
      if [ "$running" = "true" ] \
        && docker exec "$container" test -f "$skill_script" \
        && docker exec "$container" test -s "$principal_file"; then
        break
      fi
      sleep 2
    done
    running=$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || true)
    if [ "$running" != "true" ] \
      || ! docker exec "$container" test -f "$skill_script" \
      || ! docker exec "$container" test -s "$principal_file"; then
      echo "Adapter 或独立 Principal 未同步到 Worker: $worker" >&2
      exit 1
    fi
    docker exec "$container" chmod 700 "$skill_script"
  done
else
  echo "INSTALL_WORKER_SKILLS=false，跳过 Adapter 安装"
fi

echo "==> 5/6 同步 CoPaw 运行时激活模型"
for w in $WORKERS; do
  container="${WORKER_CONTAINER_PREFIX}revguard-$w"
  docker exec -i -e REVGUARD_TARGET_MODEL="$MODEL" "$container" python3 - <<'PY'
import json
import os
import urllib.request

base = "http://127.0.0.1:8088"
target = os.environ["REVGUARD_TARGET_MODEL"]
with urllib.request.urlopen(base + "/api/models", timeout=10) as response:
    providers = json.load(response)
provider = next(item for item in providers if item.get("id") == "agentteams-gateway")
known = {
    item.get("id")
    for item in [*(provider.get("models") or []), *(provider.get("extra_models") or [])]
}
if target not in known:
    payload = json.dumps({"id": target, "name": target}).encode()
    req = urllib.request.Request(
        base + "/api/models/agentteams-gateway/models", data=payload,
        method="POST", headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10):
        pass
payload = json.dumps({
    "provider_id": "agentteams-gateway", "model": target, "scope": "global",
}).encode()
req = urllib.request.Request(
    base + "/api/models/active", data=payload, method="PUT",
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=10) as response:
    active = json.load(response).get("active_llm") or {}
if active.get("provider_id") != "agentteams-gateway" or active.get("model") != target:
    raise SystemExit(f"active model mismatch: {active}")
PY
done

echo "==> 6/6 状态与 LLM Gateway 核验"
for w in $WORKERS; do
  docker exec "$CONTROLLER" agt worker ensure-ready --name "revguard-$w" >/dev/null &
done
wait
docker exec "$CONTROLLER" agt get workers
for _ in $(seq 1 24); do
  ready=$(docker exec "$CONTROLLER" agt get teams 2>/dev/null | awk -v n="$TEAM" '$1==n {print $NF}')
  [ "$ready" = "9/9" ] && break
  sleep 5
done
[ "$ready" = "9/9" ] || {
  echo "Team 未在超时内达到 9/9 Ready（当前: ${ready:-unknown}）" >&2
  exit 1
}
docker exec "$CONTROLLER" agt get teams

docker exec -i -e REVGUARD_TARGET_MODEL="$MODEL" \
  "${WORKER_CONTAINER_PREFIX}revguard-intake" python3 - <<'PY'
import json
import os
import urllib.request

base = os.environ["AGENTTEAMS_AI_GATEWAY_URL"].rstrip("/")
if not base.endswith("/v1"):
    base += "/v1"
token = os.environ.get("AGENTTEAMS_WORKER_GATEWAY_KEY") or os.environ["AGENTTEAMS_AUTH_TOKEN"]
payload = json.dumps({
    "model": os.environ["REVGUARD_TARGET_MODEL"],
    "messages": [{"role": "user", "content": "reply OK"}],
    "max_tokens": 4,
}).encode()
req = urllib.request.Request(
    base + "/chat/completions", data=payload, method="POST",
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
)
with urllib.request.urlopen(req, timeout=30) as response:
    if response.status != 200:
        raise SystemExit(f"AI Gateway preflight failed: HTTP {response.status}")
PY

if [ "$CONFIGURE_MATRIX_WORKER_ROOMS" = "true" ]; then
  echo "==> 写入 Worker 独立 Matrix 房间映射（不输出凭证）"
  python3 "$REVGUARD_HOME/scripts/configure_matrix_worker_rooms.py" \
    --env "$REVGUARD_HOME/.env" \
    --container-prefix "$WORKER_CONTAINER_PREFIX"
fi

echo
echo "完成。在 Element Web（http://<host>:8088）进入 revguard-team 聊天室即可演示。"
echo "Worker 通过 $REVGUARD_API_BASE_URL/api/v1/skills/{name}/invoke 调用 RevGuard Skill 层。"
echo "API key 必须由 AgentTeams Secret/Adapter 注入，禁止写入 SOUL 或聊天消息。"
