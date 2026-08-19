# Phase 4.2-B 实施报告：Relationship Runtime 接线集成

日期：2026-08-12
前置：4.2-B 接线审计（`docs/audit/PHASE4_2B_RUNTIME_WIRING_AUDIT.md`）+ 批准的实施任务卡
性质：接线优先，不重构。零新增 Relationship 系统。

---

## 1. 修改文件（共 2 个）

### `src/runtime/runtime_core.py`（唯一源码改动，约 +110 行）

| 位置 | 改动 |
|---|---|
| `__init__` relationship 属性区 | +`self._relationship_read_adapter = None`（C.5 只读 adapter 懒创建句柄） |
| `_stage_03_emotion_update` | **任务卡方案 B**：emotion 逻辑从 early-return 改为 `if em is not None:` 包裹（行为等价），方法末尾追加 `self._relationship_update(event, ctx)` 子步骤。17 阶段冻结表零改动 |
| 新增 `_relationship_update(event, ctx)` | 写路径：仅 `user_input` 类且文本非空时调 `record_relationship_interaction(user_message, evidence_id, emotion_tag)`；读路径：懒创建 C.5 `RelationshipRuntimeAdapter`（只读）→ `ctx.relationship_snapshot`。整体 try/except fail-soft |
| 新增 `_build_relationship_prompt_context(ctx)` | 从 C.5 快照派生 prompt 上下文：只注入 `关系阶段`（stage_label）+ `关系概述`（relationship_summary，已过 RelationshipBoundary 检查），**不注入 trust/familiarity 原始数值** |
| Stage 14 `_stage_14_response_generation` | 两处接通：ResponseAdapter `req_kwargs` +`relationship_context` 键；engine 降级路径 `relationship_context={}` 硬编码 → `self._build_relationship_prompt_context(ctx)`（消灭 runtime_core.py 原 4986 行硬编码） |

### `tests/runtime/test_relationship_runtime_chain.py`（新增，5 条验收测试）

## 2. 调用链变化

**接线前**：
```
用户输入 → 17 阶段 → Stage 14 relationship_context={}（硬编码）
record_relationship_interaction：生产零调用（死代码）
```

**接线后**：
```
Stage 1 Receive Event (ctx.user_message)
  ↓
Stage 2 Memory Retrieval
  ↓
Stage 3 Emotion Update
  └─【新子步骤】Relationship Update
       ├─ 写：record_relationship_interaction(
       │      user_message=ctx.user_message,
       │      emotion_tag=emotion_snapshot.mood（可得时）,
       │      evidence_id=experience→memory 映射，映射未就绪时锚定在途 experience_id)
       │      → extractor→evaluator 门禁→RelationshipState/Model 更新→双 save→relationship_changed 事件
       └─ 读：C.5 RelationshipRuntimeAdapter.read_relationship()
              → ctx.relationship_snapshot（只读、带边界检查）
  ↓
Stage 4-13（不变）
  ↓
Stage 14 Response Generation
  → relationship_context={"关系阶段":..., "关系概述":...}（不再是 {}）
```

## 3. 实施期关键发现（3 个，均为审计未覆盖的实际接线约束）

1. **`adapters_enabled` 总闸**：Relationship 四件套装配（runtime_core.py:672）嵌套在
   `if config["adapters_enabled"]`（:369）内，单开 `relationship_enabled` 会静默不装配。
   测试已按此修正；生产 config 需两者同开（既有集成测试配置本就如此）。
2. **evaluator 证据门禁（MIN_EVIDENCE_COUNT=1）**：当轮 experience 在行动分派后才
   finish/落 memory，Stage 3 时 `get_experience_memory_id()` 通常返回 None。
   若 evidence_id 传空，evaluator 永远拒绝 → 关系状态永不更新（只有互动日志）。
   **处置**：映射不可得时降级锚定在途 `_building_experience_id`——它是本轮互动的
   真实可审计记录，id 稳定、系统生成（非 LLM 编造），不改 evaluator 任何逻辑。
3. ** emotion_manager 无 `current_state`**：17 阶段路径中 `ctx.emotion_snapshot`
   经常为空，emotion_tag 多数时候为 ""（fail-soft 接受；emotional_patterns 仅在
   tag 非空时累积，语义正确）。

## 4. 测试结果（`"$DAIMON_USER_PYTHON" -m pytest`）

| 套件 | 结果 |
|---|---|
| **新增 5 条验收测试** | **5/5 全绿** ✅ |
| `tests/runtime` 全量 | 165 passed / 1 failed（唯一失败为既有 fallback 语义问题，基线同败，按顾问决议留待 4.2-B 后统一处理） |
| `tests/test_runtime_stage_contract.py` | **10/10 全绿** ✅ —— 17 阶段冻结契约零改动 |
| relationship 领域全量（`-k "relationship and not digest"`） | **239 passed / 0 failed** ✅ |

验收对照任务卡：

| 任务卡要求 | 结果 |
|---|---|
| Test 1 单次互动 record 被调用 | ✅ spy 断言调用 1 次 + total_interactions/shared_experiences ≥ 1 |
| Test 2 连续互动 shared experience 增加 | ✅ 两轮后 total_interactions 与 total_shared_experiences 均递增（注：任务卡示例输入"讨论羽依架构"不命中 extractor 关键词，测试改用真实可触发的 collaboration 语句，意图不变） |
| Test 3 relationship_context 进入 Response | ✅ 捕获 engine kwargs：非空 dict，含 关系阶段/关系概述 |
| Test 4 fail-soft（engine 异常） | ✅ record 抛 RuntimeError 时回复仍正常生成，读路径不受影响 |
| Test 5 红线 | ✅ IDENTITY_CORE 逐字节不变、SelfModel 提案队列为空、Growth 提案无写入 |

## 5. 架构影响

- **Relationship 进入生命循环**：写路径从「建成但从未触发」变为每轮真实互动驱动；
  关系理解（经历→事件→门禁→状态）成为 Runtime 生命循环的正式一环。
- **Relationship ≠ 好感度**：状态变化全部经过 extractor→evaluator 门禁与既有
  `_plan_deltas` 映射；prompt 只注入阶段标签与自然语言概述（边界检查保护），
  原始数值维度不进 prompt。
- **C.5 资产复用**：RelationshipRuntimeAdapter 的输出形态首次进入生产路径
  （其宿主 C.1 编排器仍休眠，未激活，符合任务卡约束）。

**三项原则保持确认**：

| 原则 | 状态 |
|---|---|
| Identity 唯一来源 | ✅ 未触碰 IDENTITY_CORE / identity 任何写入路径（红线测试锁死） |
| Runtime 唯一生命循环 | ✅ 接线全部在 RuntimeCore 17 阶段内（Stage 3 子步骤 + Stage 14），无平行调度器；Orchestrator 仍是 fallback |
| Relationship ≠ 人格修改 | ✅ 零 Personality/SelfModel/Growth 写入（红线测试锁死）；deltas 仅由既有 RelationshipIntelligenceEngine 内部决定 |

## 6. 遗留事项

| # | 事项 | 说明 |
|---|---|---|
| a | Response fallback 语义统一 | 按顾问决议：4.2-B 之后处理（ResponseAdapterImpl 忽略显式 fallback_reply） |
| b | evidence_id 锚定精度 | 当前在途 experience_id 降级锚定；若未来 finish_building 提前到 Stage 3 前，可自动升级为 memory_id（代码已优先查映射） |
| c | orchestrator 朴素 trust±0.05 路径 | 仍在 orchestrator.py / orchestrator_hooks.py，与 Runtime 接线并行，退役留待 4.2 后续 |
| d | 4.2-C：Relationship 接入 Response 表达层 | 当前仅注入 Stage 14 两处上下文；`relationship_summary` 如何影响交流方式（表达策略层）是下一步 |
| e | engine_context 组合污染 / growth_digest 既有失败 | 维持 4.2-A 报告 §7 登记，与本阶段无关 |

## 7. 结论

4.2-B 验收达成：Interaction → RelationshipEvent → IntelligenceEngine → State →
relationship_context → Response 链路全线贯通，5 条验收测试全绿，
17 阶段冻结契约零改动，三项架构原则保持。
羽依的核心闭环现为：事件→记忆→情绪→**关系理解**→人格表现→自我模型→成长提案→回复→经历保存。
