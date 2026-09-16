# ADR-0006：Skill Registry 单一事实源

- 状态：已采纳
- 日期：2026-08-12

## 背景

Skill 的实现、输入输出、允许身份、文档和 OpenAPI 若分别维护，会在比赛材料中形成无法验证的能力声明。

## 决策

16 个 Skill 以代码注册表和 JSON Schema 为单一事实源：版本、类型、依赖、失败处理、安全边界与复用范围位于 `SKILL_REGISTRY`，actor 白名单位于 `SKILL_ACTORS`。`docs/skills.md` 和 OpenAPI 3.1 的 `x-revguard-skill-registry` 均由脚本生成，包含 Schema、允许身份、调用路径和示例。

## 后果与验证

- `func` 等内部对象不会进入公共契约。
- CI 执行 `--check` 比较确定性生成结果；新增、删除或修改 Skill 而未更新快照会失败。
- AgentTeams StageTask 必须携带 task ID，并按注册表允许身份调用对应 Skill。
