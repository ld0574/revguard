# 案件审计哈希链：机器可读运行证据（2026-09-18）

对应决赛优化建议【方向二 ①】：把**原始机器可读运行证据随包**（DB 审计表哈希链 + trace
spans 导出），用**真实 Matrix 事件关联**替代伪 ID，并支持第三方**独立复算**。

本目录导出两条决赛运行记录的完整审计链（不是摘要），第三方无需 RevGuard 运行栈、无需
数据库、无需凭据即可复算；库侧 SQL 复算结果与导出 JSON 的 `summary` 逐一对应。

## 1. 导出内容

| 文件 | 内容 | SHA-256 |
|---|---|---|
| `audit-chain-case-2026-0001.json` | CASE-2026-0001 全链 435 行（seq 1–693）+ 代次/运行窗口 + 库级校验 | `38925b0a4107d743d03c3d4c48c6347d764dded84b1aaa2f30a31d6810fd90a1` |
| `audit-chain-case-2026-0008.json` | CASE-2026-0008 全链 335 行（seq 15–924）+ 代次/运行窗口 + 库级校验 | `e657d713071dc32309033028753eed2697e47a8c09287900758a3859959ccc61` |
| `chain-verification.sql` | 库侧复算脚本（全库 + 单案 + 链头） | `960ab83f6aa7f5a460264b5707480cf2e8ac10b2649b15cc2b3826be8ec2e2b3` |
| `sql-verification-CASE-2026-0001.txt` | CASE-2026-0001 的库侧复算输出 | `87785007af82ebdb2f5cc8e56d8ae7d9d510fa4f1fa59a89d853d4ff436aca29` |
| `sql-verification-CASE-2026-0008.txt` | CASE-2026-0008 的库侧复算输出 | `7a37d1012cbcbb03f6de5e40a8473f9e84f4de7625b5a9157ddca21688cedf65` |

导出脚本：`scripts/export_case_audit_chain.py`（只读 API，不写数据库、不打印凭据）。
离线校验脚本：`scripts/check_audit_chain_export.py`（不连数据库）。回归用例：
`tests/test_audit_chain_export.py`（5 项：自洽通过 + 篡改行哈希 / 断链 / 摘要漂移 / 库级校验失败四种负例）。离线脚本已在 **Python 3.9 与本项目 3.11 环境**分别复算通过（时间解析兼容任意位数微秒）。

## 2. 已复算结论（2026-09-18，202 Docker 演示栈 `0.6.0-rc3`）

| 项 | CASE-2026-0001 | CASE-2026-0008 |
|---|---|---|
| 全链行数 / seq 区间 | 435 / 1–693 | 335 / 15–924 |
| 链头 `row_hash` | `9700d81070080a32f995cabb12e12d491f4e277a457f40d22c2e39345ee46b05` | `6608c015372b9a34c44595e7aae7cf79fa72ad18cb35ec22dd920dfe778ca95a` |
| 录制代次 | `REC-36B14AC3` | `REC-63A0C9EC` |
| 代次窗口（`seq > start_seq`） | 110 行（seq 584–693） | 121 行（seq 804–924） |
| 运行窗口（与官网回放页同口径） | 109 行（seq 585–693） | 120 行（seq 805–924） |
| 库侧 digest / 链接 / 行哈希 mismatch | 0 / 0 / 0 | 0 / 0 / 0 |
| 全库链校验 | 1093 行 / 断链 0 | 同左（同一库） |
| 离线 `check_audit_chain_export.py` | 通过 | 通过 |

**两个窗口为什么差 1 行**：代次窗口起点是标记行 `DEMO_CASE_REPREPARED`（seq 583 / 803）之后的
`TEAM_RUN_STARTED`（seq 584 / 804）；官网回放页按「首个 trace span 起点」过滤，而
`TEAM_RUN_STARTED` 比首个 span 早 6 ms（CASE-0001：20:22:09.9453Z vs 20:22:09.951843Z），
因此回放页少这一行。两条口径都写在导出 JSON 的 `generation_window.rule` 与 `run_window.rule` 里，
链头与窗口尾行完全一致，`first_seq`/`last_seq` 可直接与该页数字对照。

## 3. 链算法（与库内触发器一致，不依赖 RevGuard 代码）

```text
canonical_row = jsonb_build_object('case_id', case_id, 'actor', actor, 'event', event,
                                   'detail', detail, 'created_at', created_at)::text
row_digest    = sha256(canonical_row)
row_hash      = sha256(previous_hash || ':' || row_digest)
previous_hash = 同一 case_id 上一行的 row_hash；首行为 'GENESIS'
```

实现出处：`migrations/polardb/001_core.sql` 的 `revguard_audit_chain_before_insert()`；
`BEFORE INSERT` 触发器按 `case_id` 取事务级 advisory lock，串行化同案链；第二个触发器拒绝
`UPDATE / DELETE / TRUNCATE`，因此导出行可视为 append-only。

## 4. 第三方复算方式

```bash
# ① 只凭导出文件复算（不需要数据库）：链接 + 行哈希 + 摘要一致性
python scripts/check_audit_chain_export.py docs/evidence/audit-chain-20260918/*.json

# ② 在库侧复算 canonical row digest（需要可读 audit_events 的库连接）
docker exec -i <postgres-container> psql -U <user> -d <db> -X -q \
  -v case_id=CASE-2026-0001 -f - < docs/evidence/audit-chain-20260918/chain-verification.sql
```

`chain-verification.sql` 的三段输出分别回答：「全库链接与行哈希是否自洽」「单案
canonical row digest 能否由原始字段复算」「单案链头与行数是否与导出 JSON 一致」。

## 5. 边界

- 导出是**只读**的：脚本只调用 `/api/v1/cases/{id}`、`/api/v1/cases/{id}/dashboard`、
  `/api/v1/ops/metrics` 与 `/api/v1/health`。
- 审计行**原样导出**（否则无法复算），导出前做 fail-closed 扫描：命中凭据字段带值、
  `Bearer`、私钥头、内部主机或内网 IPv4 直接失败。本轮两条链均通过扫描。
- 保留真实 Matrix 房间 / 事件 ID 与演示审批账号，因为它们是「真人批准 → 后续执行 →
  最终状态」的核验锚点；审批人是**演示账号**（`@admin:matrix-local.agentteams.io:8086`，
  显示名「财务负责人（演示）」），不是真实企业员工，也不代表任何机构。
- 本目录是**本地 PostgreSQL（Docker 主从）**上的运行证据，不等同云 PolarDB 生产高可用；
  也不代表外部 ERP 会计写入已验收。
