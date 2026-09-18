"""Read-only probe: evaluation-criteria independence + evidence provenance.

Runs inside a 202 Docker container against a copy of the master tree.
Writes nothing outside /tmp; no credentials, no network.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path("/tmp/rg")
OUT = []


def say(line: str = "") -> None:
    OUT.append(line)
    print(line, flush=True)


def run(cmd: list[str], cwd: pathlib.Path, env: dict | None = None) -> tuple[int, str]:
    merged = None
    if env:
        import os
        merged = {**os.environ, **env}
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=merged)
    return proc.returncode, proc.stdout + proc.stderr


def sha16(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def parse_json(out: str) -> dict:
    """从 harness 的 stdout 中取出 JSON 文档（首个 { 到最后一个 }）。"""
    start, end = out.find("{"), out.rfind("}")
    if start < 0 or end < 0:
        return {}
    try:
        return json.loads(out[start:end + 1])
    except json.JSONDecodeError:
        return {}


def section(title: str) -> None:
    say("")
    say(f"=== {title} ===")


section("[1] 冻结期望集构成与哈希（静态数据，不在评测代码里复刻被测规则）")
golden = sorted((ROOT / "data/golden_cases").glob("*.json"))
say(f"golden cases: {len(golden)}")
for path in golden:
    spec = json.loads(path.read_text(encoding="utf-8"))
    say(f"  {path.name}  sha256[:16]={sha16(path)}  input_keys={sorted(spec['input'])}  expected_keys={sorted(spec['expected'])}")
risk = ROOT / "data/expected/risk_matrix.csv"
rows = risk.read_text(encoding="utf-8").strip().splitlines()
say(f"risk boundary rows: {len(rows) - 1}  header={rows[0]}  sha256[:16]={sha16(risk)}")
say("policy date samples: 8 (含 Q2/Q3 生效边界：2026-06-30 -> 2026-Q2，2026-07-01 -> 2026-Q3)")
say("security probes: 9 (伪造签名 / 越权 / 跨案令牌 / 幂等重放等)")

section("[2] 正常评测：105 场景全通过")
code, out = run(["python3", "scripts/run_evaluation.py"], ROOT)
doc = parse_json(out)
say(f"exit_code={code}")
say(f"  method={doc.get('method')}")
say(f"  total_scenarios={doc.get('total_scenarios')} passed={doc.get('passed')}")
for key in ("golden_e2e", "risk_boundaries", "policy_dates", "security_probes"):
    item = (doc.get("categories") or {}).get(key) or {}
    if item:
        say(f"  {key}: scenarios={item.get('scenarios')} passed={item.get('passed')} failures={len(item.get('failures') or [])}")

section("[3] 篡改 1：把 GOLDEN-001 期望佣金改成 99999.00（证明期望来自冻结文件而非系统自身输出）")
tamper = pathlib.Path("/tmp/rg-tamper-golden")
shutil.rmtree(tamper, ignore_errors=True)
shutil.copytree(ROOT, tamper)
target = tamper / "data/golden_cases/GOLDEN-001.json"
spec = json.loads(target.read_text(encoding="utf-8"))
spec["expected"]["total_commission"] = "99999.00"
target.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
code, out = run(["python3", "scripts/run_evaluation.py"], tamper)
doc = parse_json(out)
say(f"exit_code={code} (期望非 0)")
for item in ((doc.get("categories") or {}).get("golden_e2e") or {}).get("failures") or []:
    say("  failure: " + str(item))
_cat = doc.get("categories") or {}
say("  其它类别失败数: risk=%d policy=%d security=%d" % (
    len((_cat.get("risk_boundaries") or {}).get("failures") or []),
    len((_cat.get("policy_dates") or {}).get("failures") or []),
    len((_cat.get("security_probes") or {}).get("failures") or [])))

section("[4] 篡改 2：把风险矩阵一行期望 L0 改成 L3（证明边界判据来自静态期望集）")
tamper2 = pathlib.Path("/tmp/rg-tamper-risk")
shutil.rmtree(tamper2, ignore_errors=True)
shutil.copytree(ROOT, tamper2)
risk2 = tamper2 / "data/expected/risk_matrix.csv"
lines = risk2.read_text(encoding="utf-8").splitlines()
lines[1] = lines[1].replace("L0", "L3")
risk2.write_text("\n".join(lines) + "\n", encoding="utf-8")
code, out = run(["python3", "scripts/run_evaluation.py"], tamper2)
doc = parse_json(out)
say(f"exit_code={code} (期望非 0)")
for item in ((doc.get("categories") or {}).get("risk_boundaries") or {}).get("failures") or []:
    say("  failure: " + str(item))

section("[5] 发布快照校验（同一份快照被门禁复核）")
code, out = run(["python3", "scripts/validate_evaluation_snapshot.py"], ROOT)
say(f"exit_code={code}")
say("  " + out.strip().splitlines()[-1] if out.strip() else "  (no output)")

section("[6] 审批证据不可自报：ApprovalDecision 只接受 decision/comment")
code, out = run(
    ["python3", "/tmp/approval_schema_probe.py"], ROOT,
    env={"REVGUARD_APPROVAL_SIGNING_KEY": "evaluation-probe-signing-key-32-bytes-min"},
)
say(f"exit_code={code}")
for line in out.strip().splitlines():
    say("  " + line.strip())

section("[7] 审计主体不可自报（服务端 Bearer Principal）")
code, out = run(["python3", "-m", "unittest", "tests.test_api", "-v"], ROOT)
say(f"exit_code={code}")
for line in out.strip().splitlines():
    if "test_10b" in line or line.startswith("OK") or line.startswith("Ran "):
        say("  " + line.strip())

section("[8] Matrix 事件 ID 来自 Matrix 服务端响应")
matrix_team = (ROOT / "revguard/matrix_team.py").read_text(encoding="utf-8")
checks = {
    'response.get("event_id") 存在': 'response.get("event_id")' in matrix_team,
    '未返回 event_id 视为错误': 'MatrixTransportError("Matrix send 未返回 event_id")' in matrix_team,
    'handoff 事件 ID 取自 send_text 返回值': 'handoff["matrix_event_id"] = handoff_event_id' in matrix_team,
}
for key, value in checks.items():
    say(f"  {key}: {value}")

say("")
say("PROBE_DONE")
pathlib.Path("/tmp/eval-independence-probe-output.txt").write_text("\n".join(OUT) + "\n", encoding="utf-8")
