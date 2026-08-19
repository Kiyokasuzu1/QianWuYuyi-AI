# SelfModel Runtime Baseline — Phase C.4.4

> **Phase:** C.4.4 — SelfModel Runtime Baseline (等待真实交互数据)
> **生成时间:** 2026-08-02
> **状态:** ⏳ **WAITING_FOR_RUNTIME_DATA**
> **基线模式:** SelfModel 数据将在真实长期交互中自然生成

---

## 0. 概述

本文件定义 **SelfModel Runtime Baseline**(运行时基线)。
当前 SelfModel 数据目录(`data/self_model/`)尚未初始化,这是 Phase C.2 迁移后的
正常状态 — SelfModel 数据将随真实交互自然累积。

一旦 SelfModel 数据生成,执行:
```bash
python scripts/selfmodel_consistency_check.py
```

将自动生成 [selfmodel_consistency_report.md](./selfmodel_consistency_report.md)。

---

## 1. 预期 SelfModel 数据结构

### 1.1 `data/self_model/beliefs.jsonl`

每行一条 belief 记录(JSON Lines),字段:

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `belief_id` | str | 主键 |
| `user_id` | str | 关联用户 |
| `trait` | str | belief 关联的特质(如"温柔") |
| `value` | float | belief 强度 [0, 1] |
| `confidence` | float | 置信度 [0, 1] |
| `source_event_id` | str | 触发事件 |
| `created_at` | str | ISO 8601 |
| `updated_at` | str | ISO 8601 |

### 1.2 `data/self_model/history.jsonl`

SelfModel 状态变更历史,字段:

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `record_id` | str | 主键 |
| `dimension` | str | 变更维度(如"warmth") |
| `value` | float | 新值 |
| `delta` | float | 变化量 |
| `source` | str | 变更来源(proposal / growth / event) |
| `source_id` | str | 关联 proposal/event id |
| `timestamp` | str | ISO 8601 |

### 1.3 `data/self_model/reflection.jsonl`

SelfModel 反思记录,字段:

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `reflection_id` | str | 主键 |
| `topic` | str | 反思主题 |
| `insight` | str | 反思内容 |
| `user_id` | str | 关联用户 |
| `timestamp` | str | ISO 8601 |

### 1.4 `data/self_model/relationship.jsonl`

Relationship 状态记录,字段:

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `relationship_id` | str | 主键 |
| `user_id` | str | 用户(必须唯一) |
| `trust` | float | 信任度 |
| `intimacy` | float | 亲密度 |
| `familiarity` | float | 熟悉度 |
| `updated_at` | str | ISO 8601 |

---

## 2. 基线期望(Empty 状态)

当前状态:**所有 SelfModel 文件均不存在或为空**。

| 文件 | 期望 | 实际 |
| --- | --- | --- |
| `beliefs.jsonl` | 数据将随交互生成 | 空 |
| `history.jsonl` | 数据将随交互生成 | 空 |
| `reflection.jsonl` | 数据将随交互生成 | 空 |
| `relationship.jsonl` | 数据将随交互生成 | 空 |

> **正常状态** — 这是 Phase C.2 迁移后预期状态,无需修复。

---

## 3. 数据生成触发条件

SelfModel 数据将在以下条件满足时生成:

1. **真实 LLM 调用**:用户与羽依的真实对话
2. **Growth 事件评估通过**:重要事件触发 GrowthEvaluator
3. **Proposal accept**:人工 review approve 后,调用 `ProposalManager.accept_proposal()`
4. **Reflection 触发**:定期 reflection cycle 触发反思

> ⚠️ 当前阶段未启用真实 LLM,因此 SelfModel 数据为空。
> 一旦进入真实运行(Phase D),数据将自然累积。

---

## 4. 一致性检查(等待数据后执行)

数据生成后,执行 `scripts/selfmodel_consistency_check.py`:

### 4.1 检查项

| 检查项 | 阈值 | 说明 |
| --- | --- | --- |
| **identity 稳定** | 核心特质不被漂移 | 检查 beliefs.jsonl |
| **personality 无异常漂移** | 单步 delta ≤ 0.5 | 检查 history.jsonl |
| **growth_history 连续** | 时间顺序正确 | 检查 history.jsonl |
| **relationship 不越权** | 无多 user_id 污染 | 检查所有文件 |

### 4.2 报告输出

报告将自动生成到:
- `docs/audit/selfmodel_consistency_report.md`

### 4.3 当前状态摘要

```
identity: ⚠️ 数据未生成
personality: ⚠️ 数据未生成
growth_history: ⚠️ 数据未生成
relationship: ✅ 无多用户污染(0 user_id)
overall_status: WAITING_FOR_RUNTIME_DATA
```

---

## 5. 监控与告警

通过 `scripts/runtime_health_monitor.py`(Phase C.4.1)持续监控:

| 指标 | 期望 | 当前 |
| --- | --- | --- |
| SelfModel data_status | empty → initialized | empty |
| CoreIdentity 变化尝试 | = 0 | 0 ✅ |
| LLM 错误次数 | < 5% | 9 条(历史遗留) |

---

## 6. 后续行动

1. ⏳ **等待真实交互** — SelfModel 数据将随真实用户交互自然生成
2. ⏳ **执行 selfmodel_consistency_check.py** — 数据产生后立即跑一致性检查
3. ⏳ **人工 review** — review 报告中的 issues(若有)
4. ⏳ **进入 Phase D** — 满足全部一致性条件后,可进入下一阶段

---

## 7. 关联文档

| 文档 | 说明 |
| --- | --- |
| [selfmodel_consistency_report.md](./selfmodel_consistency_report.md) | 一致性检查输出(当前显示数据未生成) |
| [memory_health_report.md](./memory_health_report.md) | Memory 长期健康监控 |
| [runtime_health_report.md](./runtime_health_report.md) | Runtime 整体监控 |
| `scripts/selfmodel_consistency_check.py` | 一致性检查脚本(Phase C.3.4) |
| `scripts/runtime_health_monitor.py` | Runtime 监控脚本(Phase C.4.1) |

---

> **生成者:** `docs/audit/selfmodel_runtime_baseline.md` (Phase C.4.4)
> **状态:** ⏳ 等待真实交互数据生成
> **下一步:** 真实用户交互产生 SelfModel 数据 → 执行一致性检查
