#!/usr/bin/env bash
# RevGuard 决赛演示/现场冒烟测试
# 用法：bash rehearsal_smoke.sh <演示栈基础地址>
#   例：bash rehearsal_smoke.sh http://10.10.10.202:19088
#   例：bash rehearsal_smoke.sh https://demo.example.com
# 输出每项 PASS/FAIL，任一 FAIL 即不应采用现场 Demo（改用视频兜底）。
set -u
BASE="${1:-http://10.10.10.202:19088}"
VIEWER="rg-demo-viewer-key-1"
pass=0; fail=0
ok()  { echo "  PASS  $1"; pass=$((pass+1)); }
bad() { echo "  FAIL  $1"; fail=$((fail+1)); }
check() { # name, expected_code, actual_code
  if [ "$3" = "$2" ]; then ok "$1 (HTTP $3)"; else bad "$1 (期望 $2，实际 $3)"; fi
}

echo "== RevGuard 决赛现场冒烟：$BASE =="
echo "-- 1/5 API 健康 --"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$BASE/api/v1/health")
check "API 健康" 200 "$code"

echo "-- 2/5 演示驾驶舱页面 --"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$BASE/demo/")
check "WebUI 页面" 200 "$code"

echo "-- 3/5 双案例可演示状态（真人审批记录完好） --"
S1=$(curl -s -m 10 "$BASE/api/v1/cases/CASE-2026-0001" -H "Authorization: Bearer $VIEWER" | python3 -c "import json,sys;print(json.load(sys.stdin).get('status'))" 2>/dev/null)
if [ "$S1" = "CLOSED" ]; then
  ok "CASE-2026-0001 = CLOSED"
elif [ "$S1" = "WAITING_FOR_APPROVAL" ]; then
  ok "CASE-2026-0001 = WAITING_FOR_APPROVAL（等待现场人工审批）"
else
  bad "CASE-2026-0001 = ${S1:-不可达}"
fi
S8=$(curl -s -m 10 "$BASE/api/v1/cases/CASE-2026-0008" -H "Authorization: Bearer $VIEWER" | python3 -c "import json,sys;print(json.load(sys.stdin).get('status'))" 2>/dev/null)
[ "$S8" = "ROLLED_BACK" ] && ok "CASE-2026-0008 = ROLLED_BACK" || bad "CASE-2026-0008 = ${S8:-不可达}"

echo "-- 4/5 证据来源仍为真实 ERPNext --"
ERP=$(curl -s -m 10 "$BASE/api/v1/cases/CASE-2026-0001/trace" -H "Authorization: Bearer $VIEWER" | grep -o erpnext | head -1)
[ "$ERP" = "erpnext" ] && ok "Trace 含 erpnext 来源" || bad "Trace 未发现 erpnext 来源"
HITL=$(curl -s -m 10 "$BASE/api/v1/cases/CASE-2026-0001/trace" -H "Authorization: Bearer $VIEWER" | grep -o "HumanIdentityVerification" | head -1)
if [ "$HITL" = "HumanIdentityVerification" ]; then
  ok "Trace 含真人身份验证 Span"
elif [ "$S1" = "WAITING_FOR_APPROVAL" ]; then
  ok "真人身份验证将在现场审批后产生"
else
  bad "未发现真人身份验证 Span"
fi

echo "-- 5/5 ERPNext 站点可达（经服务器本机） --"
# 该检查在服务器侧执行：ssh <202> curl -s -o /dev/null -w '%{http_code}' http://localhost:18080/api/method/ping
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "http://localhost:18080/api/method/ping" 2>/dev/null)
[ "$code" = "200" ] && ok "ERPNext ping = 200（在 202 本机执行时）" || echo "  SKIP  ERPNext ping 需在 202 本机执行（ssh 后运行）"

echo
echo "== 结果：$pass PASS / $fail FAIL =="
if [ "$fail" = "0" ]; then
  echo "结论：现场 Demo 通路可用。建议现场仍保留与当前候选版本同源的视频作为兜底。"
else
  echo "结论：存在 $fail 项失败 —— 现场改用视频兜底（submission/finals-media/revguard_demo_webui_20260917.mp4 + 截图序列）。"
  exit 1
fi
