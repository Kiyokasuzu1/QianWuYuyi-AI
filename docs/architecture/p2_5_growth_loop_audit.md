# P2.5 Growth Loop 最小激活 — Phase 0 审计 + Phase 1 设计

> 阶段：P2.5（Growth Loop 最小激活）
> 本文档范围：Phase 0 审计结论 + Phase 1 最小激活设计（只审计与设计，不包含实现）
> 约束：默认关闭 / 新增独立 flag / 不自动修改人格 / 不绕过 MutationGateway / 不开启无限自主成长 / 不接 ActionSystem / 不接 Scheduler

---

## 1. 结论摘要

GrowthPipeline 的组件**全部已存在**，且其中一段已在生产运行：
`RuntimeCore._stage_04_growth_evaluation`（Stage 04 GROWTH_EVALUATION）已经把「真实经历 →
Growth 管线 → pending Proposal」接进了 17 阶段主循环，并受 Phase 4.3 红线冻结约束。

P2.5 的缺口不是「没有 Growth 模块」，而是**三条连接点缺失**：

1. **Runtime 认知循环事件 → Growth 评估**：B.15 的 cycle 事件 / ReflectionRecord 只进
   CognitiveTimeline 与 EventBus，不喂给 GrowthEvaluator。
2. **独立激活 flag**：Stage 04 无条件运行（有经历就评估），B.15 激活组合不涉及 growth，
   不存在独立 flag 控制「事件 → evaluator → proposal」这条只读链。
3. **Reflection 完成节点 → Growth**：`reflection_growth_bridge` 存在但 config-off；
   Stage 11 的 ReflectionRecord（ctx._b15_reflection_record）不进入成长评估。

本阶段设计（Phase 1）选择在 **LifecycleExecutor.execute() 末尾（Reflection 完成节点之后、
cycle_completed 发布之后）** 增加一个独立 flag 门控的只读 adapter：
**只允许 事件产生 → evaluator 判断 → proposal 生成（pending、内存态），禁止自动 apply。**

---

## 2. 已存在组件清单

| 组件 | 位置 | 职责 | 本阶段使用方式 |
|---|---|---|---|
| GrowthEvaluator | src/growth/growth_evaluator.py | 标准化事件 → 8 字段成长元数据（confidence/source_reliability/stability/consistency/impact/evidence_quality/growth_level/growth_domain/growth_allowed/target_candidates/applied_delta） | **只读调用**（纯函数，无副作用） |
| GrowthIntegrationService | src/growth/growth_integration.py:73 | process_event / accept_experience / apply_proposal；auto_accept=False（0.8 门槛冻结） | 不直接调用（其 accept_experience 为 Phase 4.3 冻结的唯一入口，且 create_proposal 会落盘 data/） |
| GrowthEngine | src/growth/growth_engine.py | apply / apply_evaluated / apply_proposal（变更执行引擎） | **禁止调用** |
| ProposalManager | src/growth/proposal_manager.py | create/accept/reject/apply/list/get（create 会持久化） | 不调用（避免 data/ 写入） |
| ProposalStore | src/growth/proposal_store.py | data/proposals/proposals.jsonl append-only | **禁止触碰** |
| MutationGateway | src/governance/mutation_gateway.py:76 | evaluate(MutationRequest) → 四态决策（ACCEPT/REJECT/NEED_REVIEW/DEFER）+ audit_writer 审计 + proposal_store 落账（B.10） | 运行时**不调用**；仅测试路径演示「唯一修改入口」 |
| MutationRequest / MutationDecision | src/governance/mutation_contract.py:117 | mutation_id/source_event/actor_identity/target_domain/target_path/proposed_change/evidence/context_snapshot/risk_level；target_domain ∈ MUTATION_TARGETS | 仅测试路径构造 |
| approval_manager | src/growth/approval_manager.py | 提案审核 | 已存在，无需改动（proposal 默认 pending 即进入其队列语义） |
| reflection_growth_bridge | src/growth/reflection_growth_bridge.py | ReflectionRecord → growth 桥 | 存在但 config-off（reflection_growth_bridge_enabled 默认 False）；本阶段不启用 |
| GrowthProposal（canonical schema） | src/contracts/growth_schema.py:55 | id/proposed_changes/confidence/evidence_ids/evaluator_meta/timestamp/status/schema_version；status 默认 "proposed"；ChangeItem(path/before/after/reason) | **内存态构造 pending proposal**（不落盘） |

## 3. 当前调用路径（生产现状）

```
RuntimeCore.process()
  └─ LifecycleExecutor.execute()  （17 阶段唯一调度点）
       └─ Stage 04 GROWTH_EVALUATION → RuntimeCore._stage_04_growth_evaluation
            · ctx._control_blocked → 跳过
            · _get_growth_integration_service()（懒装配：growth_history_store + self_model_store + config）
            · _collect_growth_candidate_experiences()
                 —— 仅 ExperienceJournal，type=="runtime_experience"，limit=3
            · service.accept_experience(record)  ← Phase 4.3 冻结唯一入口
                 └─ GrowthPipeline → ProposalManager.create_proposal
                      └─ ProposalStore（data/proposals/proposals.jsonl 落盘）
            · ctx.growth_proposals += pending proposals
            · ctx.lifecycle_trace["growth_activation"] = {...}
```

**Phase 4.3 红线（runtime_core.py:5793-5799，顾问任务卡冻结，P2.5 不得违反）：**

- 唯一入口：GrowthIntegrationService.accept_experience(record)
- 禁止接 GrowthAdapterImpl.evaluate（先改状态后补提案的违规模式）
- 经历必须来自 ExperienceJournal（真实持久化经历），禁止从当前 user_message 推断 / LLM 生成 / 构造 fake record
- 不直接调 GrowthEngine，不直接改 personality，不直接 apply proposal
- 全程 fail-soft；状态写入 ctx.lifecycle_trace["growth_activation"]

## 4. 数据流（as-is，全链路）

```
ExperienceJournal（data/experiences/，真实持久化经历）
  → _collect_growth_candidate_experiences（limit 3）
  → accept_experience → GrowthEvaluator（管线内部评估）
  → ProposalManager.create_proposal → ProposalStore（data/proposals/*.jsonl，落盘）
  → status=pending → 审核（approval_manager / 用户驱动）
  → apply_proposal → B.13 治理检查（is_self_model_mutation_gateway_enabled）
  → MutationGateway（五道检查 + 审计落账）
  → 变更执行 → 审计记录
```

本阶段新增的路径**完全独立于**上述落盘链：只读取运行时内存中的真实产物（stage 执行标记、
ReflectionRecord、已发布事件），产出内存态 pending proposal，不触碰 ExperienceJournal /
ProposalStore / MutationGateway。

## 5. 缺失连接点（审计发现）

| # | 缺口 | 现状 | 本阶段动作 |
|---|---|---|---|
| G1 | Runtime 事件 → Growth 评估 | B.15 cycle 事件只进 EventBus/Timeline，不喂 GrowthEvaluator | 新增只读 adapter 消费运行时轨迹 |
| G2 | 独立激活 flag | 无；Stage 04 无条件运行 | 新增 growth_loop_activation_enabled（默认 False） |
| G3 | Reflection 完成节点 → Growth | bridge 存在但 config-off；ReflectionRecord 不评估 | adapter 消费 ctx._b15_reflection_record（只读） |
| G4 | 提案审核 | proposal 默认 pending；审核链已存在 | 生成 pending proposal 即进入既有审核语义 |
| G5 | 成长评估审计可见性 | 仅 lifecycle_trace（内存） | 沿用 lifecycle_trace 键 growth_loop_activation（内存，不新增事件类型） |

## 6. Phase 1 设计：最小 Growth Activation

### 6.1 激活 flag（独立，默认 False）

```
cognitive_activation._growth_loop_activation_enabled = False
```

- **独立于** B.15 全部 flag：`apply_runtime_cognitive_activation()` **不**触碰它
  （B.15 最小组合保持 growth-free，满足「不开启无限自主成长」）。
- `reset_all_cognitive_activation_flags()` 一并复位（保证测试隔离与可回滚）。

### 6.2 接线点（唯一，可回滚）

`LifecycleExecutor.execute()` 末尾、`cycle_completed` 发布之后、`return ctx` 之前：

```python
run_growth_loop_adapter(core, ctx)
```

选择理由：

- 此刻 Reflection 已完成（Stage 11 早于此处），ctx._b15_reflection_record 可用 →
  满足任务书「Reflection 完成节点」。
- 17 阶段全部执行完毕，ctx._b15_stage_calls 完整 → 证据为**整轮真实轨迹**。
- 单点接线、flag=False 直接 return → **零行为差异**。
- 不选 Stage 04 内部接线：Stage 04 早于 Stage 11（顺序 04 < 11），此刻拿不到
  本轮的 ReflectionRecord；且 Stage 04 是 Phase 4.3 冻结路径，P2.5 不触碰。

### 6.3 adapter 逻辑（run_growth_loop_adapter，全部 fail-soft）

```
if not is_growth_loop_activation_enabled(): return

1. 证据收集（只读，全部来自本轮真实产物）：
   · ctx._b15_stage_calls（各阶段执行完成的标记，真实）
   · ctx._b15_reflection_record（ReflectionEngine 真实产物，存在时）
   · 不使用 user_message、不调 LLM、不读写 ExperienceJournal

2. 构造元事件字典（认知循环自身轨迹事件，与用户经历严格区分）：
   {
     "event_id":   "b15_gl_" + trace_id（确定性，可去重）
     "event_type": "runtime_cognitive_loop"（TYPE_IMPACT_BASE 未收录 → 默认 0.1）
     "importance": 0.6
     "evidence":   [{"text": "生命周期阶段X执行了", "ref": X}, ...]（真实执行事实）
     "source":     "runtime_cognitive_loop"
   }

3. GrowthEvaluator().evaluate(event_dict 副本) → 成长元数据（纯函数，无副作用）
   预测路径：evidence 含行为标记 → source_reliability=1.0 →
   confidence≈0.55 → impact=0.06 → growth_level="context" →
   domain="knowledge" → growth_allowed=True → target_candidates=[]（无维度映射）

4. proposal 生成（仅当 growth_allowed，仅内存态，禁止 apply）：
   GrowthProposal（canonical schema）：
     id="b15gl_"+event_id 后缀（确定性）
     source_event_id=event_id
     proposed_changes=[]   ← 元事件不伪造维度变更（诚实原则：
                              变更维度只能来自真实经历的意义解析，本路径没有）
     confidence/evidence_ids/evaluator_meta=评估元数据
     status="proposed"（默认 pending，进入既有审核语义）
   → ctx._growth_loop_proposal（对象）
   → 不落盘、不建 ProposalStore、不调 ProposalManager

5. 状态写 ctx.lifecycle_trace["growth_loop_activation"]（内存）：
   {"status": "proposal_created" | "no_growth" | "failed", ...}

6. 异常一律隔离（logger.debug），绝不影响主链
```

### 6.4 禁止清单（红线映射）

- 禁止自动 apply / accept：adapter 无任何变更调用；proposal 仅 "proposed"。
- 禁止绕过 MutationGateway：运行时 adapter **零次**调用 gateway；「MutationGateway 是唯一
  修改入口」由 Phase 3 测试演示（测试路径把生成的 proposal 交给 gateway.evaluate 走五道检查，
  断言决策与审计记录——测试专用，不进生产链）。
- 禁止 GrowthAdapterImpl.evaluate / GrowthEngine / ProposalStore / user_message 推断 /
  fake ExperienceJournal record：adapter 一律不触碰。
- 禁止 scheduler / autonomous action：本阶段无任何调度或主动行为。

### 6.5 预期行为差异

| flag | 行为 |
|---|---|
| False（默认） | run_growth_loop_adapter 直接 return；与旧版本字节级等价 |
| True | 每轮 cycle 多一次纯内存评估 + 至多一个 pending proposal 对象 + 一条 lifecycle_trace；零文件 I/O，零状态修改 |

## 7. Phase 2-4 计划概要

- **Phase 2**：cognitive_activation.py 新增 flag + run_growth_loop_adapter；
  lifecycle_executor.py 末尾单点调用。
- **Phase 3**：tests/test_b15_growth_loop.py（避开 conftest 单例 token：
  `GrowthEngine(` / `ProposalStore(` / `ProposalStorage(` / `GrowthState(`）：
  1) Growth 事件产生 Proposal；2) Proposal 经过治理链（测试路径 gateway.evaluate + InMemoryAuditWriter）；
  3) MutationGateway 是唯一修改入口（运行时零调用）；4) flag 关闭无行为变化；
  5) SelfModel 保护有效；6) Personality 变化可审计。
- **Phase 4**：A/B（A=growth activation off；B=evaluation on）比较：测试失败集合 /
  Proposal 数量 / Mutation 调用 / Personality 变化 / data 修改。

## 8. 架构合规自查（AGENTS.md §8）

- [x] 不新增模块（adapter 挂在既有 cognitive_activation 旁路体系）
- [x] 不破坏既有闭环（Stage 04 冻结路径零改动）
- [x] 不改变核心身份（无任何 personality 写入路径）
- [x] 数据来源可追溯（全部来自本轮内存真实产物，event_id/proposal_id 确定性）
- [x] 可回滚（flag 复位即回到旧行为；无落盘）
- [x] 人格变化必须经 GrowthProposal → 审核 → MutationGateway（本路径只到 proposal 生成即止）
