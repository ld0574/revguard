# HITL / Higress 对齐与录制说明（2026-08-31）

## 两份差距文档的处理结果

2026-08-31 用户已确认：本轮按 `admin` 演示账号验收，企业 SSO/OIDC、个人实名归因列为后续项。
以下仍区分“账号级机制已实现”和“具体自然人归因/SSO 已验收”：
当前共享演示账号不能证明具体个人身份，SSO/OIDC 也未接入，不把后续项写成已完成，详见
[逐条完成审计](goal-completion-audit-2026-08-31.md)。

| 缺口 | 当前实现 | 验收方法 |
|---|---|---|
| 静态 approver key 可冒充人审 | WebUI 已删除该 key；审批/恢复只接收短时身份动作证明 | 静态 key 请求拒绝；API/HITL 测试 |
| 审计只记 actor | 后端验证 Matrix 登录和 whoami，精确 subject 白名单；记录 sub/auth_time/认证方式/证明指纹 | 权限边界及 HUMAN_IDENTITY_VERIFIED / APPROVAL_DECIDED |
| 人审凭证可跨动作/案件用 | 120 秒证明绑定 case_id、approval_id、action、唯一 jti；业务决定状态与事务防重放 | HITL、API 与安全测试 |
| Agent 自批 | MCP 不暴露审批或恢复工具；Orchestrator 到人审停止；后端验证证明 | Worker 工具清单、SOUL、拒绝测试 |
| Worker 直连后端 REST | 9 个 Higress REST-to-MCP Server，16 个 Skill；业务 Worker 无后端 key；公开示例后端 key 升级为随机私密值 | 实际 StageTask 的 skill_transport=higress-mcp；旧公开 Worker key 拒绝 |
| 所有 Worker 发现所有工具 | 每个 Worker 只一个配置，网关 consumer 精确 allowlist | 工具发现 + 跨 consumer 实际拒绝 |
| MCP 失败被隐藏 | 错误保留 HTTP 状态和原因；已配置 MCP 不回退 REST | 真实 422 回归与 Adapter 测试 |
| 稿子仍写双 Agent/同步审批 | 改为真实多 Worker、Higress、Matrix 人审、202 后台执行和五页签素材 | submission 最新视频稿与当前页面 |

采用“独立网页带外审批”，没有把审批包装成 Agent Skill，也没有声称复用了 AGT 原生
Approval Gate。Matrix 是当前环境实际可用的账号身份源；密码只转发给该身份源验证，
不存入案件、Trace、Matrix 消息或浏览器持久存储。

## 审批账号怎么用

完整部署默认将安装 AgentTeams 时的 admin Matrix 账号映射为“财务负责人（演示）”。
这是方便复赛复现的**演示身份映射**，不是企业自然人 SSO 或 MFA 验收。
账号密码沿用安装信息，由人通过密码管理器使用，不写入公开 README、录屏或聊天。
本轮无需新增个人账号；由组员在审批弹窗登录 `admin`，点击“验证审批账号”，检查案件/动作
绑定后亲自提交。不是 SSH 的 `root`，也不是任何 `revguard-*` Worker。
视频统一表述为“演示审批账号验证＋人工确认”。

后续接入正式个人账号时，由管理员在服务端 `.env` 配置精确白名单，例如：

```dotenv
REVGUARD_HITL_MATRIX_USERS_JSON={"@finance-reviewer:your-matrix-server":{"actor":"finance.lead","display_name":"财务审核人"}}
```

先在身份源创建并由本人保管该账号，再配置白名单；不要允许任何 `revguard-*` Worker
或共享机器人账号审批。部署脚本重跑会保留已有白名单，不会覆盖回默认 admin。
每次敏感动作重新验证账号；120 秒过期后重新登录。完整部署使用 Matrix 内网地址，
浏览器到服务端的生产入口必须上 HTTPS，并对登录尝试限流。

`--local` 不自带 Matrix 身份源。未配置时能复现到人审暂停，不能借恢复静态 key 来
自动通过；内核完整验证使用 `make verify-ci`，实际录制使用 `--full`。

## 一键复现

在公开仓库根目录（包含 Makefile 的 revguard 目录）执行：

```bash
# 已安装 AgentTeams v1.2.0、配置可用模型且额度充足
bash scripts/deploy_demo.sh --full --model MiniMax-M3
```

脚本配置 PolarDB、API、Worker、MCP、Matrix 和 WebUI；首次空库会播种 8 案。
不传 `--reset` 保留现有记录。有活动任务时拒绝重建 API。只有确认独立演示库可清空时
才加 `--reset`；它会清空所有合成案件运行证据，不是仅重置当前下拉选中的案件。

## 正式录制前的门禁

当前验收库同时保留两种实跑：0002 为 ROLLED_BACK/20 任务，0008 为 CLOSED/17 任务。
偏差演练全局只注入一次，已由 0002 消耗，所以当前 0008 不会再故意失败。以下单案回滚
镜头要求在组员确认可清空的录制库重置后，首先运行 0008；不能只重启 API 就期待再注入。

1. 健康检查 ready=true、backend=postgresql-polardb；Team 为 9/9 ready，模型额度可用。
2. CASE-0008 从 CREATED 到 WAITING_FOR_APPROVAL；八项证据，差额 14,400 KES。
3. 组员在网页亲自验证账号并批准；不要用自动化测试的登录/提交冒充真人录制。
4. 最终 20 个成功任务均经 Higress，初次验证 32,401 对 32,400，差异 1 KES，
   两笔冲销后恢复 18,000；案件 ROLLED_BACK、回滚验证 PASSED。
5. 拍真实任务输入输出、中文阶段、MCP 网关与关联 ID；不打开凭证配置。
6. 测试/Trace 数字取本次产物；价值模拟同时拍假设与“非真实收益”声明。

## 尚未宣称完成的生产能力与评审风险

- 企业 OIDC/SSO、MFA、实名归属、专职审批账号运维、HTTPS/登录限流还需生产接入。
  密码认证证明“持有白名单账号凭据”，不构成物理真人在场或防钓鱼证明。
- 云 PolarDB 高可用/PITR、真实企业数据与真实降本增效尚未验收。
- 当前主要展示受约束、状态驱动的协作；Manager 的开放式动态任务规划不是本次已验证能力。
  复赛规则对动态协作有加分要求，不能把确定性服务端决策描述成 LLM 自主推理。
- UI 截图只能证明画面和状态对应，不能单独证明完整无障碍合规或生产安全。

录制就绪与生产就绪是两个结论。以上边界应留在方案/答辩备份页，不能为了叙事删掉。
