# ADR-0002：Executor / Verifier 分离

- 状态：已采纳
- 日期：2026-08-12

## 背景

由执行写入的同一身份自行宣布成功，会把工具回执等同于业务正确性，无法发现落账偏差，也会扩大权限。

## 决策

`revguard-executor` 只持有草稿、入账和冲销权限；`revguard-verifier` 只持有账本只读权限。执行后的金额与分项由独立查询验证。验证失败进入 `ROLLBACK_REQUIRED`，冲销后再执行独立回滚验证。

## 后果与验证

- 写权限和验收权限不落在同一 Principal。
- Tool receipt 证明调用发生，Verifier evidence 证明结果满足预期；两者不得互相替代。
- 故障注入测试覆盖“写入成功但验证偏差”及一次性回滚能力令牌。
