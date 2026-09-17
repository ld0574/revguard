# Skill 三级摘要（manifest / instruction / callable）

RevGuard 把 16 个 Skill 当作"可被多个 Agent 复用的稳定能力"发布，因此必须回答一个
第三方最常问的问题：**我看到的 Skill 清单，和真正跑起来的函数，是同一个东西吗？**

三级摘要给出可复算的答案。每个 Skill 固定三个 SHA-256：

| 级别 | 摘要对象 | 回答的问题 | 变了意味着什么 |
|---|---|---|---|
| `manifest` | 版本、类型、描述、依赖工具、失败处理、安全边界、复用场景、MCP annotations | 这个 Skill **声称自己是什么** | 能力声明变了，必须重新评审 |
| `instruction` | 公开输入/输出 JSON Schema | 它**承诺的输入输出语义是什么** | 契约变了，调用方/文档/Worker 提示词都要跟着变 |
| `callable` | 实现函数的规范化源码（`inspect.getsource`） | 它**实际执行的是哪段代码** | 实现被改写，哪怕版本号没动也会被发现 |

只有三者同时固定，才能排除"清单不动、实现被换"这种最隐蔽的漂移。

## 基线文件

基线固定在同一 git commit 的：

```
config/skill-integrity.json
```

结构：

```json
{
  "schema": "revguard.skill-integrity/1",
  "skill_count": 16,
  "levels": ["manifest", "instruction", "callable"],
  "skills": {
    "ApprovalRouteSkill": {
      "version": "1.0.0",
      "manifest": "sha256:…",
      "instruction": "sha256:…",
      "callable": "sha256:…"
    }
  }
}
```

## 加载期 fail-closed

API 进程与 MCP 进程在启动时都会执行 `assert_registry_integrity(SKILL_REGISTRY)`：
基线缺失、schema 不受支持、Skill 新增/删除、任意一级摘要漂移，都会抛出
`SkillIntegrityError` 并**拒绝启动**。这是门禁而不是告警——一份无法自证的 Skill 清单
不应该对外提供服务。

## 复现与核验（第三方两条命令）

```bash
python3 scripts/gen_skill_integrity.py --check      # 三级摘要是否与基线一致
python3 scripts/gen_skill_integrity.py --write      # 确认改动后重新生成基线
```

运行期还可以直接读接口（需要 viewer 身份）：

```bash
curl -s -H "Authorization: Bearer $REVGUARD_VIEWER_KEY" \
  http://127.0.0.1:9000/api/v1/skills | jq '.skills[] | {name, integrity}'
```

`config/skill-integrity.json` 会随仓库发布，因此任何人都能在本地重算摘要并与发布基线
逐条比对；不一致的公开仓库无法通过 `--check`。

## 运行记录里的落点

每次 Skill 调用都会把三级摘要写进该案件的 `SKILL_INVOKED` 审计事件：

```json
{
  "skill": "ApprovalRouteSkill",
  "version": "1.0.0",
  "skill_receipt": "SKR-…",
  "skill_integrity": {
    "manifest": "sha256:…",
    "instruction": "sha256:…",
    "callable": "sha256:…"
  }
}
```

因此一条运行记录不仅可以追到"哪个 Worker 调了哪个 Skill"，还可以追到
"那一刻执行的是哪段实现"。审计事件在 PostgreSQL 侧由 trigger 强制 append-only，
摘要不会在事后被改写。

## 边界

- 三级摘要证明的是**完整性**（同一性、可复算、可对账），不证明实现本身正确；正确性由
  确定性评测集、Golden Case 与资金内核测试承担。
- 摘要绑定的是源码文本而不是构建产物；镜像摘要与 SBOM 另行发布，二者互补。
- `callable` 摘要使用规范化源码（去包裹缩进、去行尾空白），格式重排不会误报，
  任何语义改动都会改变摘要。
