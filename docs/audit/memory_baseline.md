# Memory Baseline — Phase C.2.4

> **Phase:** C.2 — Memory Restoration & Cognitive Baseline  
> **基线时间:** `2026-08-02T13:07:15.144871Z`  
> **状态:** **EXCELLENT** (Grade A+)  
> **关联文档:**
> - [memory_cleanup_report.md](./memory_cleanup_report.md)
> - [memory_migration_result.md](./memory_migration_result.md)
> - [memory_review_decision.md](./memory_review_decision.md)
> - [memory_migration_preflight.md](./memory_migration_preflight.md)

## 1. Memory 当前状态

| 指标 | 数值 |
| --- | --- |
| **memory.json 总记录数** | **286** |
| 文件大小(字节) | 134969 |
| 文件大小(可读) | 131.81 KB |
| SHA-256 | `c5eb530ca9db76a1295e65e860a1a74f7bffa5729ae94e981bf1fc403532e624` |
| normal_user | 286 |
| system_pollution | 0 |
| ai_internal_pollution | 0 |
| invalid | 0 |
| **污染率** | **0.0%** |
| **invalid 率** | **0.0%** |
| **schema 合法率** | **100.0%** |
| **质量评分** | **100.0 / 100** |
| **综合等级** | **A+ (EXCELLENT)** |

### 1.1 backup / archive 状态

| 类别 | 数量 | 备注 |
| --- | --- | --- |
| archive 文件 | 3 | Phase C.2 期间累计 |
| backup 文件 | 4 | Phase C.2 期间累计 |
| 最新 backup | `D:\Yuyi Project\QianWuYuyi-AI_自动驾驶版\QianWuYuyi-AI\data\memory_backup\memory.json.20260802T124154664235Z` | 用于 rollback |

**Memory 健康状态: ✅ HEALTHY**

## 2. Memory 类型分析

| 类型 | 数量 | 占比 | 状态 |
| --- | --- | --- | --- |
| `user_fact` | 0 | 0.00% | — |
| `user_preference` | 0 | 0.00% | — |
| `user_event` | 0 | 0.00% | — |
| `user_experience` | 0 | 0.00% | — |
| `user_goal` | 0 | 0.00% | — |
| `user_emotion` | 0 | 0.00% | — |
| `user_relationship` | 0 | 0.00% | — |
| `important_experience` | 0 | 0.00% | — |
| `user_shared` | 286 | 100.00% | 已知 |

## 3. 时间跨度分析

| 指标 | 数值 |
| --- | --- |
| 最早 memory 时间 | `2026-08-02T19:36:08.763951` |
| 最新 memory 时间 | `2026-08-02T20:23:49.960887` |
| 覆盖时间范围 | 47.7 分钟 |
| 时间分桶数(按小时) | 2 |

### 3.1 按小时分布

| 小时(UTC) | 数量 |
| --- | --- |
| `2026-08-02T19` | 154 |
| `2026-08-02T20` | 132 |

## 4. Memory Quality 评分

| 维度 | 评分 | 说明 |
| --- | --- | --- |
| pollution_rate | 0.0% | 目标:0% |
| invalid_rate | 0.0% | 目标:0% |
| schema_valid_rate | 100.0% | 目标:100% |
| 清洁度(cleanliness) | 100.0 / 100 | pollution 减分 |
| 合法度(invalidity) | 100.0 / 100 | invalid 减分 |
| **综合评分** | **100.0 / 100** | Grade A+ |

### 4.1 关键断言

- `system_pollution = 0`: ✅ (实际 0)
- `ai_internal_pollution = 0`: ✅ (实际 0)
- `retrieval_ready`: ✅

## 5. PollutionGuard 状态确认

| 检查项 | 实际 | 状态 |
| --- | --- | --- |
| 禁止类型 (FORBIDDEN_TYPES) 记录数 | 0 | ✅ |
| 禁止 role (system/assistant/tool/...) 记录数 | 0 | ✅ |
| 白名单类型激活 | True | ✅ |
| 未知类型记录数 | 0 | ✅ |

### 5.1 验证结论

- ✅ 禁止类型拦截正常,system/tool/debug/runtime_experience 等未进入 memory
- ✅ user memory 白名单正常工作

## 6. Memory → Growth → SelfModel 链路状态

链路: **Memory → Event → GrowthEvaluator → GrowthProposal → PersonalityGrowthHistory → SelfModel**

| 节点 | 角色 | 当前状态 |
| --- | --- | --- |
| **Memory** | 持久化用户记忆 | ✅ 干净 (286 条 normal_user) |
| **Event** | 从 Memory 抽取的经验事件 | ✅ 由 MemoryExtractor 处理(Phase 5.0_d3_b) |
| **GrowthEvaluator** | 评估事件是否触发 growth | ✅ 已集成 (src/growth/growth_evaluator.py) |
| **GrowthProposal** | growth 变更提案 | ✅ ProposalStore 持久化 (data/proposals/) |
| **PersonalityGrowthHistory** | growth 历史记录 | ✅ PersonalityGrowthRecord 记录 |
| **SelfModel** | 自模型消费 growth | ✅ SelfModelManager 接入 (Phase 6.0+) |

### 6.1 链路连通性

- ✅ MemoryStore.add() → PollutionGuard → 写入 data/memory.json
- ✅ MemoryExtractor 从 memory.json 提取 experience 候选
- ✅ ExperienceReflection 周期触发 → GrowthEvaluator
- ✅ GrowthProposal → ProposalStore → GrowthApproval
- ✅ PersonalityGrowthState 更新 → SelfModel 可见

## 7. Baseline Snapshot

> **此 Snapshot 作为未来长期运行的比较基准。**

| 字段 | 值 |
| --- | --- |
| `baseline_date` | `2026-08-02T13:07:15.144871Z` |
| `memory_count` | 286 |
| `normal_user_count` | 286 |
| `quality_score` | 100.0 |
| `grade` | A+ |
| `pollution_rate` | 0.0% |
| `invalid_rate` | 0.0% |
| `schema_valid_rate` | 100.0% |
| `pollution_status` | CLEAN |
| `growth_connection_status` | CONNECTED |
| `source_sha256` | `c5eb530ca9db76a1...` |

## 8. 后续动作

| 任务 | 状态 |
| --- | --- |
| C.2.0 Migration Preflight | ✅ 完成 |
| C.2.1 Memory 人工审核 | ✅ 完成 |
| C.2.2 Memory 迁移执行 | ✅ 完成 |
| C.2.3 防污染机制 | ✅ 完成 |
| C.2.4 Memory Baseline | ✅ 完成 (本次) |
| C.2.5 全量回归 | ⏸ 暂不执行(本阶段要求停止) |

## 9. 监控阈值

| 指标 | 当前 | 阈值 | 状态 |
| --- | --- | --- | --- |
| pollution_rate | 0.0% | < 5% | ✅ |
| invalid_rate | 0.0% | < 5% | ✅ |
| memory_count | 286 | _开放_ | — |

---

> 报告生成者: `scripts/memory_baseline.py` (Phase C.2.4)
> 数据源: `data/memory.json` (只读) + `data/memory_archive/` + `data/memory_backup/`
> 分类规则: 与 `scripts/audit_memory.py` 保持一致
