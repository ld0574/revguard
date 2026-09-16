"""RevGuard — 面向企业渠道佣金结算异常的多智能体治理平台。

核心包结构：
- models.py         领域模型与状态机
- rule_engine.py    确定性规则引擎（ADR-001）
- policy_matcher.py 政策版本 Time Travel
- risk.py           L0-L3 风险分级
- skills.py         Skill 注册中心
- mocks.py          Mock 系统 + 工具契约（ADR-003）
- orchestrator.py   Case 状态机编排
- store.py          SQLite 持久化
- trace.py          Trace 记录
- report.py         审计报告渲染
- api.py            FastAPI 服务（可选）
"""

__version__ = "0.1.0"
