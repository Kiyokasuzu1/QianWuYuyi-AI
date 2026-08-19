# Phase 4.2-B 审计报告：Relationship Runtime 接线审计

日期：2026-08-12
性质：**纯审计，未改任何代码**（按任务卡要求，接线位置待批准后再实施）
前置：Phase 4.2-A 契约统一已完成（`docs/audit/PHASE4_2A_CONTRACT_UNIFICATION_REPORT.md`）

---

## 1. record_relationship_interaction 审计

### 1.1 定义位置

`RuntimeCore.record_relationship_interaction()`（`src/runtime/runtime_core.py:2864-2902`）。

实现在 RuntimeCore，**不在** RelationshipManager / Orchestrator。完整实现：

```
RelationshipIntelligenceEngine.process_interaction(state, model, user_message, evidence_id, emotion_tag)
  → repository.save_state(state)
  → repository.save_relationship_model(model)
  → notify_relationship_changed({reason, interaction, trust_change, milestones})
```

全程 try/except fail-soft，异常隔离返回 None。装配由 config `relationship_enabled: True`
门控（`runtime_core.py:670-685`，**默认关闭**），四件套
（repository / state_runtime / model_runtime / intelligence_engine）任一缺失即返回 None。

### 1.2 当前调用情况

| 调用方 | 类型 |
|---|---|
| `tests/runtime/test_relationship_intelligence_runtime.py:26` | 测试（4.2-A 后已转绿） |
| 生产代码 | **零调用** ❌ |

结论：写路径已建成且经集成测试验证，但从未被生命循环触发——**需要接线**。

### 1.3 输入来源分析

签名只需 3 个参数，**全部可从现有 Runtime 资产获得，无需扩字段**：

| 参数 | 来源 | 可得阶段 |
|---|---|---|
| `user_message` | `ctx.user_message`（Stage 1 `_stage_01_receive_event` 从 `event.payload["text"]` 提取，runtime_core.py:4350-4362） | Stage 1 之后 |
| `emotion_tag` | 情绪快照的 `mood` / `dominant` 字段（`emotion_runtime_adapter.py:404-435` 产出） | Stage 3 之后 |
| `evidence_id` | 4.1D 已打通的 Experience→Memory 映射（experience journal 的 memory_id）；`_on_event` 中 `experience_builder.start_building`（runtime_core.py:827-833）在 Stage 1 后即启动，finish 在行动分派末尾 | 回合内可得（也可留空，默认 ""） |

`process_interaction`（`relationship_model.py:73`）内部：
extractor 从 user_message 提取事件 → evaluator 门禁 → 通过才更新
familiarity/trust/collaboration、stage 推断、shared_experience（collaboration 类）、
milestone（stage 跃迁）。**语义是「经历→理解→状态」，不是好感度计数器**，
符合任务卡红线。

## 2. Runtime 生命周期结构与插入位置

### 2.1 阶段调度事实

- 阶段表冻结于 `src/runtime/stages.py`（17 个有效阶段 + `RUNTIME_LIFECYCLE_ORDER`），
  分派表 `STAGE_TO_METHOD_NAME` 在 `lifecycle_executor.py:63-85`，
  **被 `tests/test_runtime_stage_contract.py` 的 17 断言锁死**。
- Stage 1 后有 C-1 契约：exactly-once 调 `_on_event`（self_state/world_state 更新 +
  experience start_building）。
- `stage_hooks`（configure_ports 可注入）**只在 legacy `runtime.py:4060` 消费，
  LifecycleExecutor 不消费** → 不能作为 process() 路径的非侵入注入点。
- 生产聊天路径：`OrchestratorRuntimeBridge._try_runtime`（orchestrator_runtime_bridge.py:167-211）
  构造 `Event(type="user_input", payload={"text": ...})` → `RuntimeCore.process()`。
  C.1 `RuntimeCycleOrchestrator`（C.5 relationship adapter 的宿主）**只有测试引用，
  生产零调用**——它是休眠资产。

### 2.2 插入位置评估

任务卡顾问建议：不放 Stage 14，放「Stage 1 之后或 Stage 16 之前」，类似 Emotion Update。
结合实际数据依赖：

| 方案 | 说明 | 评估 |
|---|---|---|
| A. Stage 1 后立即记录 | 只有 user_message；emotion_tag 不可得（Stage 3 才更新） | 过早，情绪上下文丢失 |
| **B. Stage 3 之后新增 `RELATIONSHIP_UPDATE` 阶段** | user_message ✅、emotion_tag ✅（Stage 3 刚更新）、evidence_id ✅；语义与 Emotion Update 平行：「发生了一次互动 → 关系理解」；本轮后续 Stage 即可读到新关系状态 | **推荐**。语义最干净、可追踪性最好（last_stage_order / _phase_errors 均可见） |
| C. Stage 16 之前记录 | 本轮关系变化来不及进 Response，要下一轮才可见 | 不满足验收设计「context 进入 Response」的当轮可见性 |
| D. 挂在现有阶段方法内（不加阶段） | 不动冻结表 | 混入他阶段职责；关系更新在阶段轨迹中不可见，违背「完整可追踪链路」原则 |

方案 B 的成本：stages.py +1 枚举、executor 表 +1 行、stage 契约测试 17→18、
RUNTIME_LIFECYCLE_ORDER +1。改动小且全部集中，符合最小修改原则。

## 3. relationship_context={} 审计（runtime_core.py:4986）

### 3.1 为什么为空

Stage 14 降级路径（`_stage_14_response_generation`，engine ref 分支）中，
`self_model_context` 已在 Phase 4.1 从硬编码 `{}` 改接 Stage 9 快照（4983 行注释为证），
`relationship_context={}` 是同期遗留的最后一个硬编码上下文缺口——**就是 4.2-B 的目标**。

### 3.2 现有可复用资产（禁止重写，只接线）

| 资产 | 位置 | 适配度 |
|---|---|---|
| **RelationshipRuntimeAdapter（Phase C.5，只读）** | `src/runtime/adapters/impl/relationship_runtime_adapter.py` | 产出 `relationship_output`：`current_metrics`（familiarity/trust/collaboration/interaction_frequency）、`stage`/`stage_label`、`labels`、`relationship_summary`（已过 RelationshipBoundary 检查）、互动/信任变化摘要。**语义现成、边界检查现成**，但面向 RuntimeCycleContext，需一个薄转接把输出挂到 RuntimeCore ctx |
| `build_relationship_context()` | `src/relationship/relationship_context.py:45` | 事件库聚合视图（by_event_type / recent_events / confidence_avg）。**红线 3：明确禁止进 prompt**——只能做 audit/dashboard 数据源，不能做 Response 上下文 |
| `PromptContextBuilder._read_relationship_context()` | `src/context/prompt_context_builder.py:292` | 面向旧 Phase4 schema（closeness_score/dynamic_traits/trust_level），与现行 RelationshipState（familiarity/trust/collaboration）不同构，非本阶段目标 |
| legacy 抽取 | `orchestrator_runtime_bridge.py:286` `_extract_legacy_relationship` 读 `orch.relationship_state.get()` | legacy fallback 路径专用，不影响本阶段 |

### 3.3 消费端契约

`engine.generate(relationship_context=...)`（src/engine.py）：
dict 渲染为 `【关系状态】k: v` 行（211-213、329-330）。
**推荐注入形态**：C.5 adapter 的 `relationship_summary`（自然语言、已过边界检查）+
`stage_label` 等少量结构化字段，而不是直接倒 state 数值——
符合「关系状态→交流方式变化」，避免把数值维度暴露给 LLM 自由发挥。

## 4. 接线方案概要（待批准后实施）

```
Stage 1 Receive Event (ctx.user_message)
  ↓
Stage 2 Memory Retrieval
  ↓
Stage 3 Emotion Update (emotion_tag 可得)
  ↓
【新增】RELATIONSHIP_UPDATE
  → record_relationship_interaction(
       user_message=ctx.user_message,
       emotion_tag=emotion_snapshot.mood,
       evidence_id=当轮 experience memory_id（可得则传，否则 ""）)
  → fail-soft：relationship_enabled=False / 装配缺失 → no-op
  → 关系快照写 ctx.relationship_snapshot（复用 C.5 输出形态）
  ↓
Stage 4-13（不变；Stage 6/9 可顺带读到 ctx.relationship_snapshot）
  ↓
Stage 14 Response Generation
  → relationship_context=ctx.relationship_snapshot 派生（消灭 4986 硬编码）
```

禁止项复核：不新建 Relationship 系统 ✅（复用 record + C.5 输出形态）；
不引入好感度模型 ✅（deltas 由现有 engine 内部 `_plan_deltas` 决定，不新增数值逻辑）；
不改 Personality ✅；Relationship 不直接改人格 ✅。

## 5. 验收设计（按任务卡第四阶段）

新增测试 `tests/runtime/test_relationship_stage_wiring.py`：

1. **当轮写入**：真实 RuntimeCore（`relationship_enabled: True` + 临时目录），
   `process(Event(user_input, "我们一起长期合作这个项目，我很相信你"))` →
   断言 `model.total_interactions >= 1`、`total_shared_experiences >= 1`、
   `relationship_changed` 领域事件 >= 1（复用现有集成测试断言集）。
2. **连续性**：第二轮 process 相似输入 → `total_interactions` 递增、
   `interaction_frequency` 提升（经历累积，非单次快照）。
3. **context 进 Response**：构造 engine ref 假引擎捕获 kwargs →
   断言 `relationship_context != {}` 且包含 stage/label/summary 键。
4. **fail-soft**：`relationship_enabled` 缺省 → process 全程无错、
   `relationship_context` 为空、阶段错误表无记录。
5. **红线**：注入文本触发 RelationshipBoundary 非 SAFE →
   `relationship_summary` 被清空（C.5 既有行为，接线后不丢失）。
6. 回归：stage 契约测试 17→18 更新；`pytest tests/runtime -x` 全绿。

## 6. 风险与注意

- **冻结表变更**：stages.py / executor / 契约测试三处必须同步，否则契约测试立即红。
- **双 runtime 并存**：C.5 adapter 宿主（C.1 编排器）生产休眠，接线目标是 17 阶段路径；
  不要试图先激活 C.1 编排器（超范围）。
- **orchestrator 朴素路径**：orchestrator.py / orchestrator_hooks.py 的 trust±0.05 加减法仍在，
  与本次接线并行存在，退役留待后续（任务卡已定调）。
- **experience evidence_id**：当轮 memory_id 的最优获取点待实施时确认
  （4.1D 的 experience→memory 映射在 MemoryAdapter 内，可在 Stage 2 后从
  memory_adapter 查询最近一次 experience 写入）。

## 7. 遗留登记（更新）

- fallback 语义问题（ResponseAdapterImpl 忽略显式 fallback_reply）：
  按顾问决议记录为「**4.2-B 之后处理：Response fallback 语义统一**」，本阶段不动。
- engine_context 组合污染 / growth_digest 既有失败：维持 4.2-A 报告 §7 登记。

## 8. 结论

接线前提全部就绪：写路径（record）完整且有集成测试背书；输入三要素在 Stage 3 后全部可得；
读路径有 C.5 现成输出形态（含边界检查）；消费端契约明确。
**推荐方案 B：Stage 3 后新增 RELATIONSHIP_UPDATE 阶段 + Stage 14 接
ctx.relationship_snapshot**，等批准后按「最小修改 → 测试 → 影响报告」实施。
