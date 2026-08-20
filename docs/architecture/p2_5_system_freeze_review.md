# P2.5 完成后架构冻结审查

> 审查性质：只读架构冻结审查（不修改代码、不新增模块）
> 审查对象：P2.5 Growth Loop 最小激活完成后的全系统现状
> 审查方法：逐条核实生产路由、调用链、flag 默认值、data/ 实况、治理路径
> 日期：2026-08-21

---

## 1. 当前真实系统架构图

```
                                    ┌─────────────────────────────────────────────┐
  用户消息 (QQ/HTTP)                  │  api_server.py /v1/chat/completions          │
      │                              │  runtime.enabled=true（config.yaml:125）      │
      ▼                              └──────────────────────┬──────────────────────┘
  RuntimePipeline (runtime_pipeline.py)                     │ 失败才回退
      │ runtime=RuntimeCore（_runtime_bridge 注入）           ▼
      ▼                              Orchestrator（legacy fallback：Step 14.6 治理链）
  RuntimeCore.process()
      ▼
  LifecycleExecutor.execute()  ── 17 阶段唯一调度点 ─────────────────────┐
      │                                                                  │
      ├─ 00-02 感知/记忆检索 ◄── memory.json + chroma_db（读）           │
      ├─ 03 情绪更新 ──────── ◄──► emotion_state.json（读写，B.7 网关默认关）
      ├─ 04 GROWTH_EVALUATION（生产已激活，Phase 4.3 红线冻结）            │
      │      ExperienceJournal(data/experience_journal.jsonl，读)         │
      │        → GrowthIntegrationService.accept_experience              │
      │        → GrowthEvaluator（纯函数）                                │
      │        → ProposalManager.create_proposal                         │
      │        → ProposalStore(data/proposals/proposals.jsonl，append)    │
      │        → ctx.growth_proposals（pending，绝不自动 apply）           │
      ├─ 05 人格上下文构建 ◄── personality_state.json（读）               │
      ├─ 06-08 感知（07/08 真 no-op）                                     │
      ├─ 09 自我模型构建 ──── SelfModelStore（读）                        │
      ├─ 10 自我模型演化（Phase 4.1b 既有接线）                            │
      ├─ 11 自我模型反思 ──── ReflectionEngine（B.15 adapter 默认关）      │
      ├─ 12 自我模型校验                                                 │
      ├─ 13 自我模型持久化 ─► data/self_model/（ctx._self_model_changed 时才写）
      │      + drain_approved_growth_proposals（P0-1 唯一生产 apply 入口） │
      │          admin /growth/review → status=approved（持久化队列）      │
      │          → PersonalityEvolutionPipeline.apply_approved_to_state   │
      │          → PersonalityState.apply_evolution                       │
      │          → save_personality_state() ─► data/personality_state.json│
      └─ 14-16 响应/关系/记录                                            │
             │
             └─► 回复 ← LLM（engine.py / DeepSeek）

  B.15 / P2.5 旁路 overlay（全部 flag 默认 False → 生产零行为）：
      execute() 末尾（cycle_completed 后）
        ├─ publish_cycle_event ─► RuntimeDomainEventBus
        │      └─ timeline_projection ─► CognitiveTimeline（纯内存，writer=None 零磁盘）
        └─ run_growth_loop_adapter（P2.5）
                └─ 本轮阶段轨迹+ReflectionRecord → GrowthEvaluator
                   → 内存态 GrowthProposal（proposed_changes=[]，status=proposed）
                   → ctx._growth_loop_proposal + lifecycle_trace（内存，零 I/O）

  治理层（三条并存，互不替代）：
      ① MutationGateway（src/governance/）── 五道检查 + 审计，域 adapter 默认全关：
           emotion(B.7) / self_model(B.13) / relationship(B.9)
      ② SelfModelGovernancePolicy（legacy orchestrator Step 14.6）── context≥0.50、
           preference≥0.65 自动 apply（仅旧链路回退时激活）
      ③ PersonalityEvolutionPipeline ── 审核→apply + Identity Continuity Check（post）

  数据流方向标注：
      ◄── 只读    ──► 只写    ◄──► 读写    无箭头 = 内存态（零 I/O）
```

**关键事实（已逐条核实）：**

- 生产聊天主链 = `api_server → RuntimePipeline → RuntimeCore.process → LifecycleExecutor.execute`（config `runtime.enabled: true`、`phase4_enabled: false`）。
- Orchestrator 是**失败回退**路径，不是默认主链，但其 Step 14.6 治理链在回退时仍可自动 apply（见风险 R2）。
- Stage 04 成长心跳**已在生产运行**：data/experience_journal.jsonl（54 行真实 runtime_experience）→ data/proposals/proposals.jsonl（258 行真实 pending 提案，尾部状态 `status=pending, approval_decision=deferred`——未被应用）。
- B.15 与 P2.5 全部模块 flag 默认 False；P2.5 链零 I/O、零 mutation、零网关调用（Phase 4 A/B 实测）。

## 2. 当前能力清单

### A. 已完成并验证

| 能力 | 位置 | 验证 |
|---|---|---|
| Identity Core（不可动态覆盖） | src/personality/identity_core.py | 长期稳定 |
| Memory 系统（json + chroma 向量检索） | src/memory/ | 生产读写 |
| Emotion 系统（状态/轨迹/衰减） | src/emotion/ | 生产读写（B.7 网关默认关） |
| Relationship 模型 | src/relationship/ | 生产读写（B.9 网关默认关） |
| SelfModel 存储/构建/持久化 | src/personality/ + src/self_model/ | 17 阶段链生产运行 |
| PersonalityState（apply_evolution + 落盘） | src/personality/personality_state.py | drain 链生产运行 |
| 17 阶段生命周期调度 | src/runtime/lifecycle_executor.py | 生产主链 |
| Stage 04 成长心跳（经历→评估→pending 提案落盘） | runtime_core.py:5788 | 生产运行，Phase 4.3 红线冻结 |
| approved 提案 drain（唯一生产 apply 入口） | runtime_core.py:6408 | 生产运行（config 开启，审核锚点 admin） |
| ReflectionEngine + ReflectionRecord | src/reflection/ | 已有引擎（B.15 adapter 默认关） |
| CognitiveTimeline（内存、幂等去重、writer 可注入） | src/runtime/cognitive_timeline.py | B.15 Phase 2 测试 |
| Timeline 投影（9 类事件映射） | src/runtime/timeline_projection.py | B.15 Phase 3 测试 |
| B.15 激活层（staging 组合 + 7 stage adapter） | src/runtime/cognitive_activation.py | Phase 4/5/6 A/B 全通过 |
| P2.5 Growth Loop adapter（只读评估→内存提案） | cognitive_activation.py | Phase 3 测试 9/9 + Phase 4 A/B |
| MutationGateway（五道检查 + 审计落账） | src/governance/mutation_gateway.py | B 系列测试 + P2.5 测试路径演示 |
| 域 mutation adapter ×3（emotion/self_model/relationship） | 各域 mutation_adapter.py | B.7/B.9/B.13 测试（默认关） |
| PersonalityEvolutionPipeline（审核→apply→identity check） | src/personality/evolution_pipeline.py | 生产 drain 链 |
| Admin 治理/审核面板（/growth/review 等） | src/admin/ | 生产 |

### B. 已实现但未启用（flag 默认 False / config 缺省）

- B.15 全组合：lifecycle_cycle_events / 7 个 stage adapter / timeline_recording / timeline_event_projection / staging（runtime_cognitive_activation_enabled）
- P2.5 Growth Loop：growth_loop_activation_enabled
- B.13 SelfModel mutation gateway、B.7 Emotion mutation gateway、B.9 Relationship mutation gateway（模块级 flag 全 False）
- ReflectionScheduler、ReflectionEvaluator、ReflectionGrowthBridge（config 缺省 → False）
- AutonomousScheduler、AutonomousDecisionLayer（config 缺省 → False）
- ApprovalManager、LifecycleManager（config 缺省 → False）
- Token 优化（config `token_opt.enabled: false`）
- Phase4 路由（config `runtime.phase4_enabled: false` → 走 RuntimePipeline）
- Identity Anchor（config `identity_anchor_enabled: false`）
- CognitiveTimeline 持久化 writer（默认 None = 纯内存）

### C. 存在组件但未接线

- GrowthRuntimeAdapter（工厂 `create_growth_runtime_adapter` 仓库内零调用点）
- GrowthAdapterImpl（已在 adapter_registry 注册，但 Stage 04 红线明确禁止接其 evaluate；主链未调用）
- EmotionGrowthService（无生产调用者；其 B.13 治理分支随网关 flag 关闭而不激活）
- goal / dream / proactive / behavior 等包（有组件、有 dashboard，未进主循环）
- 多角色认知 reviewer 模式（AGENTS.md §6 预留，未实现）

### D. 尚未开发

- Timeline 持久化与归档（writer 注入点已预留，无实现）
- Memory Consolidation 例行化（记忆摘要/合并进认知循环）
- Reflection Scheduler 激活方案（节流/限频/只读反思的策略层）
- Growth × ExperienceJournal 正式闭环（意义解析→审核可见化→applied 反馈回写）
- 权威状态自动快照/回滚点（当前只有 append-only 审计 + 覆盖写文件）
- 多模态 / 身体系统 / 主动认知行为（AGENTS.md §9 远期方向）

## 3. 当前所有 activation/config flag 状态

### 3.1 B.15 / P2.5 模块级 flag（全部默认 False）

| flag | 默认值 | 控制范围 | 允许生产开启？ |
|---|---|---|---|
| lifecycle_cycle_events_enabled | False | cycle_started/阶段/cycle_completed 事件发布 | 可（Phase 6 A/B 干净；需先开 staging 或独立开关） |
| _STAGE_ACTIVATION_FLAGS ×7（PERCEPTION_OBSERVATION/ANALYSIS、SELF_MODEL_BUILD/EVOLUTION/REFLECTION/VALIDATION/PERSISTENCE） | 全 False | Stage 07-13 旁路 adapter（07 只读观察、11 只读反思） | 可（只读，Phase 5/6 已验证） |
| runtime_cognitive_activation_enabled（staging） | False | 一键开最小组合（cycle events+timeline+projection+REFLECTION） | 可（灰度用；不含 growth） |
| timeline_recording_enabled | False | CognitiveTimeline.append 记录 | 可（纯内存） |
| timeline_event_projection_enabled | False | 域事件→Timeline 投影 | 可（纯内存） |
| growth_loop_activation_enabled（P2.5） | False | 循环轨迹→GrowthEvaluator→内存 pending 提案 | 可（零 I/O、零 mutation；Phase 4 A/B 通过） |

### 3.2 config.yaml 开关（api_server 启动读取）

| flag | 当前值 | 控制范围 | 生产开启？ |
|---|---|---|---|
| runtime.enabled | **true** | /v1/chat/completions 走 RuntimePipeline（主链） | 是（生产） |
| runtime.phase4_enabled | **false** | Phase4/QQ 走 RuntimeController | 否 |
| runtime.growth_enabled | **true** | Runtime 链是否做成长评估 | 是（生产） |
| runtime.growth_apply_drain_enabled | **true** | stage13+启动兜底消费 **approved** 提案 apply | 是（生产；apply 锚点是 admin 审核） |
| runtime.growth_apply_drain_limit | 5 | 每轮 drain 上限 | 是 |
| token_opt.enabled | false | 历史压缩/记忆摘要 | 否 |
| runtime.identity_anchor_enabled | false | 身份锚点 | 否 |
| initiative.enabled | **true** | 主动消息触达（astrbot/onebot 推送） | 是（非认知自主行为，见 R3） |
| reflection_scheduler_enabled | 缺省=False | 反思调度 | 否 |
| reflection_evaluator_enabled | 缺省=False | 反思评估层 | 否 |
| reflection_growth_bridge_enabled | 缺省=False（fallback=reflection_evaluator） | 反思→成长桥 | 否 |
| autonomous_scheduler_enabled / autonomous_decision_enabled | 缺省=False | 自主调度/决策 | 否 |
| approval_manager_enabled / lifecycle_manager_enabled / personality_event_bus_enabled | 缺省=False | 审核层/生命周期管理器/人格事件总线 | 否 |

### 3.3 域治理模块级 flag（全部默认 False）

| flag | 默认值 | 控制范围 |
|---|---|---|
| emotion_mutation_gateway_enabled（B.7） | False | 情绪变更走 MutationGateway 单入口 |
| self_model_mutation_gateway_enabled（B.13） | False | 自我模型变更（含情绪信念路径）走网关 |
| relationship_mutation_gateway_enabled / relationship_self_model_gateway_enabled（B.9） | False | 关系维度/关系→self model 变更走网关 |

## 4. 架构风险检查

### R1 是否存在绕过 MutationGateway 的人格修改路径？

**结论：不存在由 B.15/P2.5 新增的 mutation 路径；但存在三条 pre-existing 的既有人格写入路径，其中两条不经过 B.13 MutationGateway（因其默认关闭）：**

1. **drain_approved_growth_proposals**（唯一生产 apply 入口）：admin 审核 → `apply_approved_to_state` → `apply_evolution` → 落盘。不经过 B.13 网关，其治理锚点是「admin 审核 + PersonalityEvolutionPipeline 的 Identity Continuity Check（post）」+ evolution_history 审计。**风险：低**（审核制 + 审计 + 身份连续性检查齐备），但治理层与 B.13 网关是两套并行的账本。
2. **legacy Orchestrator Step 14.6**：`SelfModelGovernancePolicy` 对 growth_records 自动决策（context≥0.50 / preference≥0.65 → auto_apply），未经 admin、不经 B.13 网关。仅当主链失败回退且 `incremental_update` 产出 records 时激活。**风险：中**——这是全系统唯一真正的「自动 apply」路径，建议下一阶段处理（见 T5）。
3. **情绪信念路径（B.13 开启时）**：`emotion_growth_service` → `SelfModelMutationAdapter.route → Gateway`（NEED_REVIEW 复核、永不自动应用）。默认关闭时保留旧行为（内存模型+store.save 直写）。**风险：低（默认行为透明，旧生产字节不变）**。

### R2 是否存在 Growth 自动 apply？

- P2.5 adapter：无 apply（proposed_changes 恒空，status=proposed）。
- Stage 04：只生成 pending，落盘 proposals.jsonl；实测 258 行提案全部 pending/deferred，无一 applied。
- drain 链：只消费 status=**approved**（唯一来源是 admin /growth/review），**非自动**。
- **唯一例外**：legacy Step 14.6 policy 自动 apply（R1-2 同源）。生产主链（RuntimePipeline）下此路径不激活。
- **结论：主链无自动 apply；遗留回退链有 policy 自动 apply。**

### R3 是否存在 Scheduler 自主行为入口？

- reflection_scheduler / autonomous_scheduler / autonomous_decision_layer：config.yaml 未声明任何相关 key，代码 `config.get(..., False)` 全 False → **全部关闭**，runtime_core.py:725/773 的入口为死代码路径。
- `initiative.enabled: true`：这是**主动消息触达**（定时问候/推送，经 astrbot），非认知自主行为；其决策不经过认知循环/GrowthPipeline。审查备注：initiative 与认知系统的边界建议在下一阶段明确（是否要把主动消息触发纳入循环事件，见 T4 可选扩展）。

### R4 是否存在 data 写入风险？

- **B.15 / P2.5 新增代码：零磁盘 I/O**（timeline writer 默认 None；P2.5 纯内存）。Phase 4/6 A/B 实测 data/ 零新增文件、proposals 零变化。
- 既有生产写点（与本次激活无关，属冻结链）：
  - data/proposals/proposals.jsonl（258 行，append-only，**无归档/GC 策略** → 长期膨胀）
  - data/experience_journal.jsonl（54 行，append-only）
  - data/personality_state.json（覆盖写，仅 evolution/drain apply 时）
  - data/self_model/（覆盖写，仅 ctx._self_model_changed 时）
  - data/memory.json、chroma_db、emotion_state.json、runtime_context/、runtime_trace/、users/ 等
- **建议关注**：append-only 文件的容量治理；覆盖写文件的快照备份（见 T6）。

### R5 是否存在状态不可回滚路径？

- personality_state.json / self_model 为**覆盖写**：审计靠 evolution_history/ + personality_growth_history.json + proposals append-only 记录，但**无自动版本快照**，文件级回滚需人工备份。
- proposals.jsonl append-only：天然可追溯、可重建状态视图。
- B.15/P2.5 状态全部内存态：`reset_all_cognitive_activation_flags()` 即回滚，零残留。
- **结论：可追溯 ✅、可回滚 ⚠️（依赖审计文件，无自动快照）**。建议 T6。

## 5. 下一阶段建议（只提任务，不实现）

按收益/风险排序：

1. **T1 — Timeline 持久化**：为 CognitiveTimeline 实现可选 writer（JSONL 分片 + 容量上限 + 归档策略），新增独立 flag 默认关；先 A/B 再灰度。风险最低、直接补齐「长期连续性」的观测底座。
2. **T2 — Memory Consolidation 例行化**：把记忆摘要/合并做成循环内只读任务（默认关），输出 consolidation 提案走既有提案链（禁止直改 memory）。
3. **T3 — Reflection Scheduler 最小激活**：在严格节流（最小间隔+限频+只读）约束下激活反思调度，产出 ReflectionRecord 进 Timeline，禁止直连 growth apply。
4. **T4 — Growth × ExperienceJournal 正式闭环**：补齐「意义解析（meaning resolution）→ 审核可见化 → applied 反馈回写 journal」三段，仍以 admin 审核为唯一 apply 锚点，禁止自动 apply；可评估把 initiative 主动触达纳入循环事件源。
5. **T5 — 治理对齐（建议优先于 T4）**：把 legacy Step 14.6 的 policy 自动 apply 默认关闭或迁移到 B.13 MutationGateway 语义，消除「两套治理账本 + 唯一自动 apply」的历史遗留。
6. **T6 — 权威状态快照**：为 personality_state.json / self_model 增加轮换快照（如 apply 前写 `*.prev` 或时间戳副本），使覆盖写具备秒级回滚能力。

约束延续：所有任务默认关闭、独立 flag、不绕过 MutationGateway、不接 scheduler 自主行为、先 A/B 后灰度。
