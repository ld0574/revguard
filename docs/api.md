# RevGuard API 参考

> 实现：`revguard/api.py`（FastAPI）。启动：`uvicorn revguard.api:app --host 0.0.0.0 --port 8000`。
> 交互式文档：服务启动后访问 `/docs`（Swagger UI）。
> 所有端点只返回结构化 JSON；错误格式为 HTTP 状态码 + 明确信息。

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `REVGUARD_DB_PATH` | `data/revguard.db` | SQLite 案件库路径 |
| `REVGUARD_FIXTURES_DIR` | `data/fixtures` | Mock 系统数据目录 |
| `REVGUARD_OUTPUT_DIR` | `data/outputs` | Trace / Case Memory 输出目录 |
| `REVGUARD_REPORT_DIR` | `docs/reports` | 审计报告输出目录 |
| `REVGUARD_APPROVAL_MODE` | `wait` | `wait`：L2 挂起等人工审批；`auto`：模拟审批直通（Demo 用） |
| `REVGUARD_FINANCE_FAIL_TIMES` | `1` | 财务接口故障注入次数（演示失败重试） |

## 案件

### `POST /api/v1/cases` — 创建案件

请求体（全部可选，模拟一封非结构化申诉也可以只给 `description`）：

```json
{
  "source": "MANUAL",
  "case_type": "COMMISSION_UNDERPAYMENT",
  "partner_id": null,
  "partner_name": "Mombasa Coastal Distributors",
  "order_id": null,
  "description": "代理商申诉：7 月佣金只收到 18000，认为应为 27000",
  "claim": {"actual_amount": 18000, "expected_amount": 27000, "currency": "KES"}
}
```

返回 `201` + Case 实体（含系统分配 `case_id`、初始 `status=CREATED`）。

### `GET /api/v1/cases` — 案件列表

返回 `{"cases": [...]}`。

### `GET /api/v1/cases/{case_id}` — 案件详情

返回 Case + `evidence[]` + `audit_events[]`。404：案件不存在。

### `POST /api/v1/cases/{case_id}/run` — 从头运行编排链路

仅 `status=CREATED` 可调用（否则 409）。同步执行完整链路；
`APPROVAL_MODE=wait` 且风险 ≥L2 时，返回的案件停在 `WAITING_FOR_APPROVAL`。

### `POST /api/v1/cases/{case_id}/approval` — 人工审批并自动续跑

```json
{"decision": "APPROVED", "approver": "finance.lead@company.com", "comment": "差额属实，同意补付"}
```

- 仅 `WAITING_FOR_APPROVAL` 状态可调用（否则 409）；
- `APPROVED`：状态推进到 `READY_TO_EXECUTE`，**自动续跑**受控执行 + 独立验证 + 归档，返回验证结果；
- `REJECTED`：案件进入 `REJECTED`，记录原因。

### `GET /api/v1/cases/{case_id}/trace` — Trace 回放

导出全量 Span（任务拆解、工具调用、重试、状态迁移），供评委回放"谁、何时、做了什么"。

### `GET /api/v1/cases/{case_id}/report` — 审计报告

返回 `{"case_id": ..., "markdown": "..."}`（报告文件位于 `docs/reports/CASE-*.md`）。404：报告尚未生成。

## 工具与 Skill

### `POST /api/v1/tools/call` — 统一工具契约入口

AgentTeams Worker 访问 Skill 层的唯一入口（设计文档 13.1）：

```json
{
  "tool_name": "commission.submit_adjustment",
  "parameters": {"order_id": "ORD-KE-2026-0715", "component": "SALES_COMMISSION", "amount": "9000.00"},
  "case_id": "CASE-2026-0001",
  "actor": "revguard-executor",
  "scope": ["commission:write"],
  "idempotency_key": "CASE-2026-0001:SALES_COMMISSION"
}
```

返回统一信封：`{"success": bool, "data": ..., "error": {"type": ..., "message": ...}, "receipt": "RCPT-..."}`。
权限不足、幂等冲突、审批凭证无效都会以结构化错误返回并写入审计。

### `GET /api/v1/skills` — Skill 清单

返回 14 个 Skill 的完整契约（输入输出/依赖/失败处理/安全边界/复用价值），
与 `docs/skills.md` 同源（由 `scripts/gen_skill_docs.py` 生成）。

### `GET /api/v1/health` — 健康检查

返回 `{"status": "ok", "cases": <案件数>}`。
