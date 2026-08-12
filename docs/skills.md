# RevGuard Skill 清单

> 本文件由 `scripts/gen_skill_docs.py` 从 `revguard/skills.py` 的 SKILL_REGISTRY
> 自动生成，请勿手工编辑。字段对齐参赛手册附录 B。

共 **16** 个 Skill。设计原则：输入输出结构化、单一稳定能力、
LLM 理解与确定性计算分离、失败返回明确错误类型、高风险 Skill 强制审批凭证。

| Skill | 类型 | 用途 | 依赖工具 | 失败处理 | 安全边界 | 复用场景 |
|---|---|---|---|---|---|---|
| `CaseNormalizeSkill` v1.0.0 | deterministic | 申诉/工单解析为标准化案件实体 | - | missing_entity | read_only=True, pii=True | commission_dispute, ticket_intake |
| `EntityResolveSkill` v1.0.0 | tool | 解析代理商为唯一系统实体 | crm.get_partner | not_found, ambiguous | read_only=True, pii=True | commission_dispute, partner_lookup |
| `EvidenceCollectSkill` v1.1.0 | tool | 跨系统真实并行证据采集与完整度评分 | crm.*, contract.*, policy.*, finance.* | tool_unavailable_retry, evidence_gap | read_only=True, pii=False | commission_dispute, batch_reconciliation, audit |
| `PolicyVersionMatchSkill` v1.0.0 | deterministic | 按业务时点匹配政策版本 | - | no_effective_version, version_conflict | read_only=True, pii=False | commission_dispute, policy_simulation |
| `CommissionCalculateSkill` v1.0.0 | deterministic | 规则引擎确定性佣金复算 | rule_engine | invalid_schema, missing_rule, conflicting_rule | read_only=True, pii=False | commission_dispute, policy_simulation, batch_reconciliation |
| `DifferenceExplainSkill` v1.0.0 | deterministic | 差异解释与根因判定 | - | evidence_conflict | read_only=True, pii=False | commission_dispute, audit |
| `RiskClassifySkill` v1.0.0 | policy | L0-L3 风险分级与审批路由判定 | - | unknown_policy, missing_threshold | write_permission=False | commission_dispute, batch_reconciliation, any_write_action |
| `ApprovalRouteSkill` v1.0.0 | tool | 创建审批单并路由审批角色 | workflow.create_approval | workflow_unavailable | write_permission=approval | any_approval_needed_case |
| `PermissionCheckSkill` v1.0.0 | policy | 执行前权限与审批凭证校验 | - | auth_failed, missing_token | write_permission=False | any_write_action |
| `IdempotencyGuardSkill` v1.0.0 | policy | 幂等键冲突检查 | store | idempotency_conflict | write_permission=False | any_write_action |
| `AdjustmentDraftSkill` v1.0.0 | tool | 创建不生效的佣金调整草稿 | commission.create_adjustment_draft | tool_unavailable | write_permission=commission_draft | commission_dispute |
| `LedgerAdjustSkill` v2.0.0 | tool | 提交调整写入台账（签名审批凭证+幂等） | commission.submit_adjustment | auth_failed, idempotency_conflict | write_permission=commission_post | commission_dispute |
| `LedgerReverseSkill` v1.0.0 | tool | 验证失败后以一次性能力令牌反向冲销 | commission.reverse_adjustment | auth_failed, token_replayed, idempotency_conflict | write_permission=commission_reverse | commission_dispute, any_reversible_write |
| `PostActionVerifySkill` v1.0.0 | tool | 独立查询验证执行结果 | finance.get_commission_ledger | tool_unavailable | read_only=True | any_executed_case |
| `PostRollbackVerifySkill` v1.0.0 | tool | 独立确认回滚后恢复执行前净额 | finance.get_commission_ledger | tool_unavailable, rollback_variance | read_only=True | any_reversible_write |
| `CaseToDatasetSkill` v1.0.0 | deterministic | 案件轨迹沉淀为评测样本 | - | incomplete_trace | read_only=False | evaluation, knowledge_base |

## 输入 / 输出契约

### CaseNormalizeSkill

- 必填输入：`raw_case`
- 可选输入：-
- 输出：`entities`, `missing_fields`, `claim`
- 调用：`POST /api/v1/skills/CaseNormalizeSkill/invoke`
- 允许身份：`revguard-intake`
- 说明：申诉/工单解析为标准化案件实体

<details><summary>Input / Output JSON Schema</summary>

```json
{
  "input": {
    "type": "object",
    "properties": {
      "raw_case": {
        "type": "object",
        "additionalProperties": true
      }
    },
    "required": [
      "raw_case"
    ],
    "additionalProperties": false,
    "examples": [
      {
        "raw_case": {
          "partner_id": "AGT-10001",
          "order_id": "EZ202608001"
        }
      }
    ]
  },
  "output": {
    "type": "object",
    "properties": {
      "entities": {
        "type": "object",
        "additionalProperties": true
      },
      "missing_fields": {
        "type": "array",
        "items": {
          "type": "string"
        }
      },
      "claim": {
        "type": "object",
        "additionalProperties": true
      }
    },
    "required": [
      "entities",
      "missing_fields",
      "claim"
    ],
    "additionalProperties": false
  }
}
```

</details>

### EntityResolveSkill

- 必填输入：`entities`
- 可选输入：-
- 输出：`partner`, `resolved_by`
- 调用：`POST /api/v1/skills/EntityResolveSkill/invoke`
- 允许身份：`revguard-intake`
- 说明：解析代理商为唯一系统实体

<details><summary>Input / Output JSON Schema</summary>

```json
{
  "input": {
    "type": "object",
    "properties": {
      "entities": {
        "type": "object",
        "additionalProperties": true
      }
    },
    "required": [
      "entities"
    ],
    "additionalProperties": false
  },
  "output": {
    "type": "object",
    "properties": {
      "partner": {
        "type": "object",
        "additionalProperties": true
      },
      "resolved_by": {
        "type": "string",
        "enum": [
          "partner_id",
          "partner_name"
        ]
      }
    },
    "required": [
      "partner",
      "resolved_by"
    ],
    "additionalProperties": false
  }
}
```

</details>

### EvidenceCollectSkill

- 必填输入：`partner`, `order_id`
- 可选输入：-
- 输出：`evidence`, `collected`, `evidence_gaps`, `evidence_score`, `parallel`
- 调用：`POST /api/v1/skills/EvidenceCollectSkill/invoke`
- 允许身份：`revguard-evidence`
- 说明：跨系统真实并行证据采集与完整度评分

<details><summary>Input / Output JSON Schema</summary>

```json
{
  "input": {
    "type": "object",
    "properties": {
      "partner": {
        "type": "object",
        "additionalProperties": true
      },
      "order_id": {
        "type": "string",
        "minLength": 1
      }
    },
    "required": [
      "partner",
      "order_id"
    ],
    "additionalProperties": false
  },
  "output": {
    "type": "object",
    "properties": {
      "evidence": {
        "type": "array",
        "items": {
          "type": "object",
          "additionalProperties": true
        }
      },
      "collected": {
        "type": "object",
        "additionalProperties": true
      },
      "evidence_gaps": {
        "type": "array",
        "items": {
          "type": "string"
        }
      },
      "evidence_score": {
        "type": "number",
        "minimum": 0,
        "maximum": 1
      },
      "parallel": {
        "type": "object",
        "additionalProperties": true
      }
    },
    "required": [
      "evidence",
      "collected",
      "evidence_gaps",
      "evidence_score",
      "parallel"
    ],
    "additionalProperties": false
  }
}
```

</details>

### PolicyVersionMatchSkill

- 必填输入：`versions`, `facts`
- 可选输入：`time_basis`
- 输出：`policy_id`, `policy_version`, `time_basis`, `decision_date`, `effective_rule_set`, `cited_clauses`, `excluded_versions`, `unresolved_conflicts`, `confidence`
- 调用：`POST /api/v1/skills/PolicyVersionMatchSkill/invoke`
- 允许身份：`revguard-policy`
- 说明：按业务时点匹配政策版本

<details><summary>Input / Output JSON Schema</summary>

```json
{
  "input": {
    "type": "object",
    "properties": {
      "versions": {
        "type": "array",
        "items": {
          "type": "object",
          "additionalProperties": true
        }
      },
      "facts": {
        "type": "object",
        "additionalProperties": true
      },
      "time_basis": {
        "type": "string",
        "enum": [
          "order_date",
          "payment_date"
        ]
      }
    },
    "required": [
      "versions",
      "facts"
    ],
    "additionalProperties": false
  },
  "output": {
    "type": "object",
    "properties": {
      "policy_id": {
        "type": "string",
        "minLength": 1
      },
      "policy_version": {
        "type": "string",
        "minLength": 1
      },
      "time_basis": {
        "type": "string",
        "minLength": 1
      },
      "decision_date": {
        "type": "string",
        "minLength": 1
      },
      "effective_rule_set": {
        "type": "object",
        "additionalProperties": true
      },
      "cited_clauses": {
        "type": "array"
      },
      "excluded_versions": {
        "type": "array",
        "items": {
          "type": "object",
          "additionalProperties": true
        }
      },
      "unresolved_conflicts": {
        "type": "array"
      },
      "confidence": {
        "type": "number",
        "minimum": 0,
        "maximum": 1
      }
    },
    "required": [
      "policy_id",
      "policy_version",
      "time_basis",
      "decision_date",
      "effective_rule_set",
      "cited_clauses",
      "excluded_versions",
      "unresolved_conflicts",
      "confidence"
    ],
    "additionalProperties": false
  }
}
```

</details>

### CommissionCalculateSkill

- 必填输入：`rule_dsl`, `facts`, `currency`
- 可选输入：-
- 输出：`eligible`, `total_commission`, `currency`, `components`, `rounding_rule`, `calculation_hash`, `policy_version`, `eligibility_failures`, `facts_snapshot`
- 调用：`POST /api/v1/skills/CommissionCalculateSkill/invoke`
- 允许身份：`revguard-calculation`
- 说明：规则引擎确定性佣金复算

<details><summary>Input / Output JSON Schema</summary>

```json
{
  "input": {
    "type": "object",
    "properties": {
      "rule_dsl": {
        "type": "object",
        "additionalProperties": true
      },
      "facts": {
        "type": "object",
        "additionalProperties": true
      },
      "currency": {
        "type": "string",
        "minLength": 1
      }
    },
    "required": [
      "rule_dsl",
      "facts",
      "currency"
    ],
    "additionalProperties": false
  },
  "output": {
    "type": "object",
    "properties": {
      "eligible": {
        "type": "boolean"
      },
      "total_commission": {
        "type": [
          "string",
          "number"
        ]
      },
      "currency": {
        "type": "string",
        "minLength": 1
      },
      "components": {
        "type": "array",
        "items": {
          "type": "object",
          "additionalProperties": true
        }
      },
      "rounding_rule": {
        "type": "string"
      },
      "calculation_hash": {
        "type": "string",
        "minLength": 1
      },
      "policy_version": {
        "type": "string"
      },
      "eligibility_failures": {
        "type": "array"
      },
      "facts_snapshot": {
        "type": "object",
        "additionalProperties": true
      }
    },
    "required": [
      "eligible",
      "total_commission",
      "currency",
      "components",
      "rounding_rule",
      "calculation_hash",
      "policy_version",
      "eligibility_failures",
      "facts_snapshot"
    ],
    "additionalProperties": false
  }
}
```

</details>

### DifferenceExplainSkill

- 必填输入：`calculation`, `ledger_entries`, `matched_policy_version`
- 可选输入：`tier_conflict`
- 输出：`diffs`, `total_expected`, `total_posted`, `total_delta`, `root_causes`, `confidence`
- 调用：`POST /api/v1/skills/DifferenceExplainSkill/invoke`
- 允许身份：`revguard-rootcause`
- 说明：差异解释与根因判定

<details><summary>Input / Output JSON Schema</summary>

```json
{
  "input": {
    "type": "object",
    "properties": {
      "calculation": {
        "type": "object",
        "properties": {
          "eligible": {
            "type": "boolean"
          },
          "total_commission": {
            "type": [
              "string",
              "number"
            ]
          },
          "currency": {
            "type": "string",
            "minLength": 1
          },
          "components": {
            "type": "array",
            "items": {
              "type": "object",
              "additionalProperties": true
            }
          },
          "rounding_rule": {
            "type": "string"
          },
          "calculation_hash": {
            "type": "string",
            "minLength": 1
          },
          "policy_version": {
            "type": "string"
          },
          "eligibility_failures": {
            "type": "array"
          },
          "facts_snapshot": {
            "type": "object",
            "additionalProperties": true
          }
        },
        "required": [
          "eligible",
          "total_commission",
          "currency",
          "components",
          "rounding_rule",
          "calculation_hash",
          "policy_version",
          "eligibility_failures",
          "facts_snapshot"
        ],
        "additionalProperties": false
      },
      "ledger_entries": {
        "type": "array",
        "items": {
          "type": "object",
          "additionalProperties": true
        }
      },
      "matched_policy_version": {
        "type": "string",
        "minLength": 1
      },
      "tier_conflict": {
        "type": [
          "string",
          "null"
        ]
      }
    },
    "required": [
      "calculation",
      "ledger_entries",
      "matched_policy_version"
    ],
    "additionalProperties": false
  },
  "output": {
    "type": "object",
    "properties": {
      "diffs": {
        "type": "array",
        "items": {
          "type": "object",
          "additionalProperties": true
        }
      },
      "total_expected": {
        "type": [
          "string",
          "number"
        ]
      },
      "total_posted": {
        "type": [
          "string",
          "number"
        ]
      },
      "total_delta": {
        "type": [
          "string",
          "number"
        ]
      },
      "root_causes": {
        "type": "array",
        "items": {
          "type": "string"
        }
      },
      "confidence": {
        "type": "number",
        "minimum": 0,
        "maximum": 1
      }
    },
    "required": [
      "diffs",
      "total_expected",
      "total_posted",
      "total_delta",
      "root_causes",
      "confidence"
    ],
    "additionalProperties": false
  }
}
```

</details>

### RiskClassifySkill

- 必填输入：`action_type`, `adjustment_amount`, `currency`, `evidence_score`, `case_type`
- 可选输入：`policy_conflict`, `order_count`
- 输出：`risk_level`, `approval_required`, `approver_role`, `execution_constraints`, `rollback_plan_required`, `reason_codes`
- 调用：`POST /api/v1/skills/RiskClassifySkill/invoke`
- 允许身份：`revguard-risk`
- 说明：L0-L3 风险分级与审批路由判定

<details><summary>Input / Output JSON Schema</summary>

```json
{
  "input": {
    "type": "object",
    "properties": {
      "action_type": {
        "type": "string",
        "minLength": 1
      },
      "adjustment_amount": {
        "type": [
          "string",
          "number"
        ]
      },
      "currency": {
        "type": "string",
        "minLength": 1
      },
      "evidence_score": {
        "type": "number",
        "minimum": 0,
        "maximum": 1
      },
      "case_type": {
        "type": "string",
        "minLength": 1
      },
      "policy_conflict": {
        "type": "boolean"
      },
      "order_count": {
        "type": "integer",
        "minimum": 1
      }
    },
    "required": [
      "action_type",
      "adjustment_amount",
      "currency",
      "evidence_score",
      "case_type"
    ],
    "additionalProperties": false
  },
  "output": {
    "type": "object",
    "properties": {
      "risk_level": {
        "type": "string",
        "enum": [
          "L0",
          "L1",
          "L2",
          "L3"
        ]
      },
      "approval_required": {
        "type": "boolean"
      },
      "approver_role": {
        "type": [
          "string",
          "null"
        ]
      },
      "execution_constraints": {
        "type": "object",
        "additionalProperties": true
      },
      "rollback_plan_required": {
        "type": "boolean"
      },
      "reason_codes": {
        "type": "array",
        "items": {
          "type": "string"
        }
      }
    },
    "required": [
      "risk_level",
      "approval_required",
      "approver_role",
      "execution_constraints",
      "rollback_plan_required",
      "reason_codes"
    ],
    "additionalProperties": false
  }
}
```

</details>

### ApprovalRouteSkill

- 必填输入：`risk`, `amount`, `component_quota`, `currency`, `action_summary`
- 可选输入：-
- 输出：`approval_id`, `case_id`, `action_summary`, `amount`, `component_quota`, `currency`, `risk_level`, `approver_role`, `status`, `created_at`
- 调用：`POST /api/v1/skills/ApprovalRouteSkill/invoke`
- 允许身份：`revguard-risk`
- 说明：创建审批单并路由审批角色

<details><summary>Input / Output JSON Schema</summary>

```json
{
  "input": {
    "type": "object",
    "properties": {
      "risk": {
        "type": "object",
        "properties": {
          "risk_level": {
            "type": "string",
            "enum": [
              "L0",
              "L1",
              "L2",
              "L3"
            ]
          },
          "approval_required": {
            "type": "boolean"
          },
          "approver_role": {
            "type": [
              "string",
              "null"
            ]
          },
          "execution_constraints": {
            "type": "object",
            "additionalProperties": true
          },
          "rollback_plan_required": {
            "type": "boolean"
          },
          "reason_codes": {
            "type": "array",
            "items": {
              "type": "string"
            }
          }
        },
        "required": [
          "risk_level",
          "approval_required",
          "approver_role",
          "execution_constraints",
          "rollback_plan_required",
          "reason_codes"
        ],
        "additionalProperties": false
      },
      "amount": {
        "type": [
          "string",
          "number"
        ]
      },
      "component_quota": {
        "type": "object",
        "additionalProperties": true
      },
      "currency": {
        "type": "string",
        "minLength": 1
      },
      "action_summary": {
        "type": "string",
        "minLength": 1
      }
    },
    "required": [
      "risk",
      "amount",
      "component_quota",
      "currency",
      "action_summary"
    ],
    "additionalProperties": false
  },
  "output": {
    "type": "object",
    "properties": {
      "approval_id": {
        "type": "string",
        "minLength": 1
      },
      "case_id": {
        "type": "string",
        "minLength": 1
      },
      "action_summary": {
        "type": "string",
        "minLength": 1
      },
      "amount": {
        "type": [
          "string",
          "number"
        ]
      },
      "component_quota": {
        "type": "object",
        "additionalProperties": true
      },
      "currency": {
        "type": "string",
        "minLength": 1
      },
      "risk_level": {
        "type": "string",
        "enum": [
          "L1",
          "L2",
          "L3"
        ]
      },
      "approver_role": {
        "type": [
          "string",
          "null"
        ]
      },
      "status": {
        "type": "string",
        "enum": [
          "PENDING"
        ]
      },
      "created_at": {
        "type": "string",
        "minLength": 1
      }
    },
    "required": [
      "approval_id",
      "case_id",
      "action_summary",
      "amount",
      "component_quota",
      "currency",
      "risk_level",
      "approver_role",
      "status",
      "created_at"
    ],
    "additionalProperties": false
  }
}
```

</details>

### PermissionCheckSkill

- 必填输入：`action_type`, `risk`
- 可选输入：`approval`
- 输出：`authorized`
- 调用：`POST /api/v1/skills/PermissionCheckSkill/invoke`
- 允许身份：`revguard-executor`
- 说明：执行前权限与审批凭证校验

<details><summary>Input / Output JSON Schema</summary>

```json
{
  "input": {
    "type": "object",
    "properties": {
      "action_type": {
        "type": "string",
        "enum": [
          "DRAFT",
          "LEDGER_ADJUST",
          "LEDGER_REVERSE"
        ]
      },
      "risk": {
        "type": "object",
        "properties": {
          "risk_level": {
            "type": "string",
            "enum": [
              "L0",
              "L1",
              "L2",
              "L3"
            ]
          },
          "approval_required": {
            "type": "boolean"
          },
          "approver_role": {
            "type": [
              "string",
              "null"
            ]
          },
          "execution_constraints": {
            "type": "object",
            "additionalProperties": true
          },
          "rollback_plan_required": {
            "type": "boolean"
          },
          "reason_codes": {
            "type": "array",
            "items": {
              "type": "string"
            }
          }
        },
        "required": [
          "risk_level",
          "approval_required",
          "approver_role",
          "execution_constraints",
          "rollback_plan_required",
          "reason_codes"
        ],
        "additionalProperties": false
      },
      "approval": {
        "type": [
          "object",
          "null"
        ],
        "additionalProperties": true
      }
    },
    "required": [
      "action_type",
      "risk"
    ],
    "additionalProperties": false
  },
  "output": {
    "type": "object",
    "properties": {
      "authorized": {
        "type": "boolean"
      }
    },
    "required": [
      "authorized"
    ],
    "additionalProperties": false
  }
}
```

</details>

### IdempotencyGuardSkill

- 必填输入：`idempotency_key`
- 可选输入：-
- 输出：
- 调用：`POST /api/v1/skills/IdempotencyGuardSkill/invoke`
- 允许身份：`revguard-executor`
- 说明：幂等键冲突检查

<details><summary>Input / Output JSON Schema</summary>

```json
{
  "input": {
    "type": "object",
    "properties": {
      "idempotency_key": {
        "type": "string",
        "minLength": 1
      }
    },
    "required": [
      "idempotency_key"
    ],
    "additionalProperties": false
  },
  "output": {
    "type": [
      "object",
      "null"
    ],
    "additionalProperties": true
  }
}
```

</details>

### AdjustmentDraftSkill

- 必填输入：`order_id`, `component`, `delta`, `currency`
- 可选输入：`reason`
- 输出：`action_id`, `order_id`, `case_id`, `component`, `amount`, `currency`, `reason`, `status`, `created_at`
- 调用：`POST /api/v1/skills/AdjustmentDraftSkill/invoke`
- 允许身份：`revguard-executor`
- 说明：创建不生效的佣金调整草稿

<details><summary>Input / Output JSON Schema</summary>

```json
{
  "input": {
    "type": "object",
    "properties": {
      "order_id": {
        "type": "string",
        "minLength": 1
      },
      "component": {
        "type": "string",
        "minLength": 1
      },
      "delta": {
        "type": [
          "string",
          "number"
        ]
      },
      "currency": {
        "type": "string",
        "minLength": 1
      },
      "reason": {
        "type": "string"
      }
    },
    "required": [
      "order_id",
      "component",
      "delta",
      "currency"
    ],
    "additionalProperties": false
  },
  "output": {
    "type": "object",
    "properties": {
      "action_id": {
        "type": "string",
        "minLength": 1
      },
      "order_id": {
        "type": "string",
        "minLength": 1
      },
      "case_id": {
        "type": "string",
        "minLength": 1
      },
      "component": {
        "type": "string",
        "minLength": 1
      },
      "amount": {
        "type": [
          "string",
          "number"
        ]
      },
      "currency": {
        "type": "string",
        "minLength": 1
      },
      "reason": {
        "type": "string"
      },
      "status": {
        "type": "string",
        "enum": [
          "DRAFT"
        ]
      },
      "created_at": {
        "type": "string",
        "minLength": 1
      }
    },
    "required": [
      "action_id",
      "order_id",
      "case_id",
      "component",
      "amount",
      "currency",
      "reason",
      "status",
      "created_at"
    ],
    "additionalProperties": false
  }
}
```

</details>

### LedgerAdjustSkill

- 必填输入：`action_id`, `approval_token`, `policy_version`, `idempotency_key`
- 可选输入：-
- 输出：`action_id`, `status`, `ledger_entry`, `before_snapshot`, `after_snapshot`, `rollback_token`
- 调用：`POST /api/v1/skills/LedgerAdjustSkill/invoke`
- 允许身份：`revguard-executor`
- 说明：提交调整写入台账（签名审批凭证+幂等）

<details><summary>Input / Output JSON Schema</summary>

```json
{
  "input": {
    "type": "object",
    "properties": {
      "action_id": {
        "type": "string",
        "minLength": 1
      },
      "approval_token": {
        "type": "string",
        "minLength": 1
      },
      "policy_version": {
        "type": "string",
        "minLength": 1
      },
      "idempotency_key": {
        "type": "string",
        "minLength": 1
      }
    },
    "required": [
      "action_id",
      "approval_token",
      "policy_version",
      "idempotency_key"
    ],
    "additionalProperties": false
  },
  "output": {
    "type": "object",
    "properties": {
      "action_id": {
        "type": "string",
        "minLength": 1
      },
      "status": {
        "type": "string",
        "enum": [
          "SUBMITTED"
        ]
      },
      "ledger_entry": {
        "type": "object",
        "additionalProperties": true
      },
      "before_snapshot": {
        "type": "array",
        "items": {
          "type": "object",
          "additionalProperties": true
        }
      },
      "after_snapshot": {
        "type": "array",
        "items": {
          "type": "object",
          "additionalProperties": true
        }
      },
      "rollback_token": {
        "type": "string",
        "minLength": 1
      }
    },
    "required": [
      "action_id",
      "status",
      "ledger_entry",
      "before_snapshot",
      "after_snapshot",
      "rollback_token"
    ],
    "additionalProperties": false
  }
}
```

</details>

### LedgerReverseSkill

- 必填输入：`ledger_id`, `rollback_token`, `idempotency_key`
- 可选输入：-
- 输出：`reversal_entry`, `reversed_entry`
- 调用：`POST /api/v1/skills/LedgerReverseSkill/invoke`
- 允许身份：`revguard-executor`
- 说明：验证失败后以一次性能力令牌反向冲销

<details><summary>Input / Output JSON Schema</summary>

```json
{
  "input": {
    "type": "object",
    "properties": {
      "ledger_id": {
        "type": "string",
        "minLength": 1
      },
      "rollback_token": {
        "type": "string",
        "minLength": 1
      },
      "idempotency_key": {
        "type": "string",
        "minLength": 1
      }
    },
    "required": [
      "ledger_id",
      "rollback_token",
      "idempotency_key"
    ],
    "additionalProperties": false
  },
  "output": {
    "type": "object",
    "properties": {
      "reversal_entry": {
        "type": "object",
        "additionalProperties": true
      },
      "reversed_entry": {
        "type": "object",
        "additionalProperties": true
      }
    },
    "required": [
      "reversal_entry",
      "reversed_entry"
    ],
    "additionalProperties": false
  }
}
```

</details>

### PostActionVerifySkill

- 必填输入：`order_id`, `expected_components`
- 可选输入：-
- 输出：`verification_status`, `expected_amount`, `actual_amount`, `variance`, `component_checks`, `evidence_refs`, `rollback_required`, `checked_at`
- 调用：`POST /api/v1/skills/PostActionVerifySkill/invoke`
- 允许身份：`revguard-verifier`
- 说明：独立查询验证执行结果

<details><summary>Input / Output JSON Schema</summary>

```json
{
  "input": {
    "type": "object",
    "properties": {
      "order_id": {
        "type": "string",
        "minLength": 1
      },
      "expected_components": {
        "type": "array",
        "items": {
          "type": "object",
          "additionalProperties": true
        }
      }
    },
    "required": [
      "order_id",
      "expected_components"
    ],
    "additionalProperties": false
  },
  "output": {
    "type": "object",
    "properties": {
      "verification_status": {
        "type": "string",
        "enum": [
          "PASSED",
          "FAILED"
        ]
      },
      "expected_amount": {
        "type": [
          "string",
          "number"
        ]
      },
      "actual_amount": {
        "type": [
          "string",
          "number"
        ]
      },
      "variance": {
        "type": [
          "string",
          "number"
        ]
      },
      "component_checks": {
        "type": "array",
        "items": {
          "type": "object",
          "additionalProperties": true
        }
      },
      "evidence_refs": {
        "type": "array",
        "items": {
          "type": "string"
        }
      },
      "rollback_required": {
        "type": "boolean"
      },
      "checked_at": {
        "type": "string",
        "minLength": 1
      }
    },
    "required": [
      "verification_status",
      "expected_amount",
      "actual_amount",
      "variance",
      "component_checks",
      "evidence_refs",
      "rollback_required",
      "checked_at"
    ],
    "additionalProperties": false
  }
}
```

</details>

### PostRollbackVerifySkill

- 必填输入：`order_id`, `expected_snapshot`
- 可选输入：-
- 输出：`verification_status`, `component_checks`, `evidence_refs`, `checked_at`
- 调用：`POST /api/v1/skills/PostRollbackVerifySkill/invoke`
- 允许身份：`revguard-verifier`
- 说明：独立确认回滚后恢复执行前净额

<details><summary>Input / Output JSON Schema</summary>

```json
{
  "input": {
    "type": "object",
    "properties": {
      "order_id": {
        "type": "string",
        "minLength": 1
      },
      "expected_snapshot": {
        "type": "array",
        "items": {
          "type": "object",
          "additionalProperties": true
        }
      }
    },
    "required": [
      "order_id",
      "expected_snapshot"
    ],
    "additionalProperties": false
  },
  "output": {
    "type": "object",
    "properties": {
      "verification_status": {
        "type": "string",
        "enum": [
          "PASSED",
          "FAILED"
        ]
      },
      "component_checks": {
        "type": "array",
        "items": {
          "type": "object",
          "additionalProperties": true
        }
      },
      "evidence_refs": {
        "type": "array",
        "items": {
          "type": "string"
        }
      },
      "checked_at": {
        "type": "string",
        "minLength": 1
      }
    },
    "required": [
      "verification_status",
      "component_checks",
      "evidence_refs",
      "checked_at"
    ],
    "additionalProperties": false
  }
}
```

</details>

### CaseToDatasetSkill

- 必填输入：`case`, `shared_state`
- 可选输入：`verification`
- 输出：`case_id`, `label`, `case_type`, `input`, `expected_policy_version`, `expected_amount`, `root_causes`, `verification`, `archived_at`
- 调用：`POST /api/v1/skills/CaseToDatasetSkill/invoke`
- 允许身份：`revguard-knowledge`
- 说明：案件轨迹沉淀为评测样本

<details><summary>Input / Output JSON Schema</summary>

```json
{
  "input": {
    "type": "object",
    "properties": {
      "case": {
        "type": "object",
        "additionalProperties": true
      },
      "shared_state": {
        "type": "object",
        "additionalProperties": true
      },
      "verification": {
        "type": "object",
        "additionalProperties": true
      }
    },
    "required": [
      "case",
      "shared_state"
    ],
    "additionalProperties": false
  },
  "output": {
    "type": "object",
    "properties": {
      "case_id": {
        "type": "string",
        "minLength": 1
      },
      "label": {
        "type": "string",
        "enum": [
          "GOLDEN",
          "BAD",
          "SAFE_ROLLBACK"
        ]
      },
      "case_type": {
        "type": "string",
        "minLength": 1
      },
      "input": {
        "type": "object",
        "additionalProperties": true
      },
      "expected_policy_version": {
        "type": [
          "string",
          "null"
        ]
      },
      "expected_amount": {
        "type": [
          "string",
          "number",
          "null"
        ]
      },
      "root_causes": {
        "type": "array",
        "items": {
          "type": "string"
        }
      },
      "verification": {
        "type": "object",
        "additionalProperties": true
      },
      "archived_at": {
        "type": "string",
        "minLength": 1
      }
    },
    "required": [
      "case_id",
      "label",
      "case_type",
      "input",
      "expected_policy_version",
      "expected_amount",
      "root_causes",
      "verification",
      "archived_at"
    ],
    "additionalProperties": false
  }
}
```

</details>
