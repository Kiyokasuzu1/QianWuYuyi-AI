# 羽依状态变化入口地图 —— Mutation Boundary Audit

> 任务：P2.3-B.1　日期：2026-08-19　分支：develop/v1.1　HEAD：225d040
> 性质：**只读审计**。本任务未修改 src/、data/、tests/ 任何文件（工作区前后 diff 为空）。
> 唯一产出：本文件。下一阶段：P2.3-B.2 Mutation Gateway 设计。

---

## 1. 结论摘要

1. **合规闭环只在一条链上完整存在**：`RuntimeCore Stage 4 → GrowthIntegrationService.accept_experience → ProposalManager（pending）→ ApprovalManager.govern_proposal → EvolutionPipeline（EP-1/EP-3 强制 approved）→ PersonalityState.apply_evolution → save`。除此之外的绝大多数写入口是直写。
2. **三处高危直写面**：① `Orchestrator.process` Step 14.5 直调 `GrowthPipeline.incremental_update`（内存 proposal 直通 apply + 8 个关系自存方法，绕过全部审核，与 Stage 4 正规链**双轨并行**）；② `PersonalityResolver.resolve` 每次解析即改 trait/self_model（高频、零 Proposal、零 audit log）；③ `RuntimeController._apply_delta_safely` 用 `p_rt_*`/`a_rt_*` 伪造审批 ID 直写 `apply_evolution`。
3. **审计基础设施约半数默认关闭**：ProposalManager 的 `_record_audit` 依赖注入 audit（runtime_core 默认装配不传 → no-op）；`personality_event_bus_enabled` 默认 False；v2 `MutationJournal`（ctx.mutations）在 pipeline/core 中**零写入**。
4. **双 Proposal 存储 + 双 growth 链并存**：canonical `ProposalStore`（JSONL）与 legacy `ProposalStorage`（JSON）各自流转；`GrowthLoop`、`RuntimeGrowthPipeline`、`GrowthIntegrationService.apply_proposal` 等治理文档承诺的链路在 src 内无生产驱动方（死链/半链）。
5. **memory/emotion 按规则不需 Proposal**（符合 AGENTS.md Rule 2/3），但除 orchestrator memory 链外多数写入无可追溯记录；memory 双主链并写且防护不对称。

---

## 2. 扫描方法与分类口径

- 关键词扫描：`save / update / add / append / delete / write / set_* / change / modify / apply / commit / evolve / delta / increment / record`（def 级），域目录 7 个：memory、emotion、growth、personality、relationship、self_model（SelfModelStore 实现在 src/personality/ 下）+ 跨域写面（orchestrator / runtime_core / runtime_pipeline / lifecycle_executor / events/handlers / initiative / reflection / dream / behavior / proactive / admin）。
- 对每个入口 grep 全 src/ 调用者（排除 tests/、data/、__pycache__）。
- **生产** = orchestrator / runtime_core / runtime_pipeline / lifecycle_executor / pipeline_server / api_server 调用链；**非生产** = admin API / 旧 CLI（terminal_chat→YuyiCore）/ 迁移脚本；**死入口** = src 内零调用者。
- 「是否需要 Proposal」依据 AGENTS.md Rule 1-3：personality / identity / self_model 的**人格语义变化**必须经 Proposal；memory / emotion / relationship 的例行写入不需 Proposal，但须可审计。
- 关键结论抽查实证：runtime_controller.py:494-496（伪造 ID）、personality_resolver.py:158/167（resolve 内直写）、orchestrator.py:1361-1365（governance auto_apply）、runtime_core.py:3263-3264（repository 直写）、orchestrator.py:2075（filepath 现场改写）。

---

## 3. Mutation Registry（Phase 2）

### 3.1 memory

| Mutation ID | 入口（file:line:function） | 当前路径 | 直接改状态 | 需 Proposal | Audit | 风险 |
|---|---|---|---|---|---|---|
| MEM-01 | src/memory/memory_store.py:113 `MemoryStore.add` | orchestrator.py:1246 Step 10 | 是 | 否（规则允许） | 是（record_audit_log + MemoryCreatedEvent + 权限门 can_modify_memory + sanitize + PollutionGuard） | 低 |
| MEM-02 | 同上 | runtime/interaction_recorder.py:125（pipeline_server→RuntimePipeline.run 链） | 是 | 否 | 间接（仅 EventBus→audit_event_handler；无权限门、无 sanitize 前置、无直写 audit） | 中 |
| MEM-03 | src/memory/vector.py:130 `VectorMemory.add_memory` | orchestrator.py:1261 Step 10（store.add 成功后） | 是 | 否 | 无独立（依赖 MEM-01 兜底） | 低 |
| MEM-04 | src/memory/memory_system.py:150 `MemorySystem.add` | core/yuyi_core.py:206/233（旧 CLI） | 是 | 否 | 否 | 低（非生产） |
| MEM-05 | src/memory/memory_context.py:60 `add_batch` | memory/context_builder.py:17（旧 CLI） | 是（内存工作区） | 否 | 否 | 低（非生产） |

### 3.2 emotion

| Mutation ID | 入口 | 当前路径 | 直接改状态 | 需 Proposal | Audit | 风险 |
|---|---|---|---|---|---|---|
| EMO-01 | src/emotion/emotion_manager.py:46 `process_event`（apply_delta + trace append + repo.save） | orchestrator.py:2040 `_process_emotion_pre`（can_modify_emotion 门） | 是 | 否 | **否**（pre 链无审计） | 中 |
| EMO-02 | 同上 | runtime/runtime_core.py:5157 `_stage_03_emotion_update` | 是 | 否 | **否**（无 audit、无事件，审计黑洞） | 中 |
| EMO-03 | src/emotion/emotion_repository.py:16 `save` | orchestrator.py:2075 `_process_emotion_post`（**现场改写 repo.filepath** 后直调，绕过 EmotionManager 封装） | 是 | 否 | 条件式（仅 dominant/intensity 变化才 record_audit_log + EmotionChangedEvent） | 中 |
| EMO-04 | src/emotion/emotion_manager.py:76 `update`（时间衰减落盘） | runtime/adapters/impl/emotion_adapter_impl.py:141（legacy runtime.py 链） | 是 | 否 | 否 | 低（legacy 链可能已被 delegate 模式旁路） |

### 3.3 relationship

| Mutation ID | 入口 | 当前路径 | 直接改状态 | 需 Proposal | Audit | 风险 |
|---|---|---|---|---|---|---|
| REL-01 | src/relationship/relationship_repository.py:130/160 `save_state` / `save_relationship_model` | runtime/runtime_core.py:3263-3264 `record_relationship_interaction`（Stage 3） | 是（repository 直写，绕过 service/manager 层） | 否 | 间接（写后 notify→域事件→audit handler） | 中 |
| REL-02 | src/relationship/relationship_repository.py:99 `save`（legacy profile） | orchestrator_hooks.py:155 `apply_legacy_relationship_profile_delta`（← orchestrator.py:2165 post） | 是 | 否（trust_delta≥0.10 事后补建 legacy proposal） | 是（`relationship.bypass_write`，唯一被白名单化的旁路） | 中 |
| REL-03 | src/personality/relationship_state.py:188-249 `update_bond/trust/familiarity/promise/history` + `add_milestone` + `add_important_event`（每方法末尾自存） | growth/growth_engine.py:362-387 `apply_relationship`（← pipeline.incremental_update ← orchestrator.py:1327） | 是 | 否 | **否** | **高**（与 GRO-01 同链） |
| REL-04 | src/relationship/relationship_memory.py:89/102 `append_record` / `append_event` | experience/experience_bridge.py:330（MEMORY_CREATED 事件处理器） | 是（事件库 append，红线保护不写 5 维值） | 否 | 是（`_emit_audit` + route audit） | 低 |
| REL-05 | src/relationship/relationship_proposal_store.py:85 `save` | relationship/relationship_candidate_bridge.py:111（MEMORY_CREATED 事件处理器；状态止于 PENDING_REVIEW） | 是（治理记录） | 是 | 否（无专属 audit，仅总线级） | 低 |
| REL-06 | src/relationship/relationship_core_store.py:71 `save` | relationship/relationship_activation.py:467（admin 治理 API） | 是 | 是（RelationshipProposalStore 记录） | 否 | 低（非生产） |

### 3.4 growth（Proposal 产地）

| Mutation ID | 入口 | 当前路径 | 直接改状态 | 需 Proposal | Audit | 风险 |
|---|---|---|---|---|---|---|
| GRO-01 | src/growth/pipeline.py:250 `incremental_update` | orchestrator.py:1327 Step 14.5（orchestrator 直调，绕开 runtime_core 生命周期与 ProposalManager/Approval） | 是（内存构造 proposal status="proposed" 即直通 apply） | **是但未过**（0.8 置信度/evidence/needs_review 门槛全部不适用） | **否** | **高（最大绕过面）** |
| GRO-02 | src/growth/growth_engine.py:336 `apply_relationship` | growth/pipeline.py:336-498（orchestrator 链） | 是（事件直通 → REL-03 八方法） | 否 | **否** | **高** |
| GRO-03 | src/growth/growth_engine.py:135 `apply` | runtime/adapters/impl/growth_adapter_impl.py:113 `evaluate`（legacy runtime.py `_invoke_growth_stage`） | 是 | 是但未过（「先改状态后补提案」，Stage 4 红线 runtime_core.py:5584 明令禁止，代码仍可达） | 否 | 中 |
| GRO-04 | src/growth/growth_state.py:297 `save` | growth_engine.py:177/308（apply / apply_proposal 内部） | 是 | 随调用方 | 否 | 低（随链） |
| GRO-05 | src/growth/proposal_manager.py:406 `apply_proposal` | growth/growth_integration.py:342（**src 内无生产调用者** → 闭环断链） | 是 | 是（要求 status=accepted） | 条件式（`_record_audit` 默认 no-op） | 中（断链） |
| GRO-06 | src/growth/proposal_store.py:129/159 `save` / `update`（canonical JSONL） | proposal_manager.py:213 及 accept/reject/apply 流转（Stage 4 正规链 accept_experience → create_proposal） | 是（proposal 状态） | 是 | 部分（ProposalManager `_record_audit` 默认 no-op） | 低 |
| GRO-07 | src/growth/proposal/storage.py:80 `save`（legacy JSON） | orchestrator.py:2249 `_create_growth_proposal` + events/handlers.py:88 `create_proposal_from_event`（订阅 PERSONALITY_CHANGED/RELATIONSHIP_CHANGED） | 是（proposal 状态） | 是 | orchestrator 链有（audit + GrowthProposalEvent）；事件链无 | 中（双存储双状态机） |
| GRO-08 | src/growth/approval_manager.py:188 `govern_proposal` | growth/growth_integration.py:829 accept_experience Step 8 | 是（治理状态流转，红线不触发 accept/apply） | 是 | 是（ApprovalManager 自记 decision） | 低 |

### 3.5 personality

| Mutation ID | 入口 | 当前路径 | 直接改状态 | 需 Proposal | Audit | 风险 |
|---|---|---|---|---|---|---|
| PER-01 | src/personality/personality_state.py:74 `apply_evolution` | personality/evolution_pipeline.py:170（accept_experience Step 9）+ personality/personality_evolution_pipeline.py:329（drain 链） | 是 | **经过**（EP-1 强制 status==approved + approval_decision；EP-3 校验 proposal_id+approval_id） | 内部 trail + save_personality_state（无 record_audit_log） | 低（合规路径） |
| PER-02 | 同上 | runtime/runtime_controller.py:504 `_apply_delta_safely` | 是 | **否——伪造** `p_rt_{uuid}` / `a_rt_{uuid}` 审批 ID，消息话题启发式即改 trait，且不落盘 | 否 | **高** |
| PER-03 | src/personality/personality_evolution.py:28 `update_trait` + personality_history.py:35 `record_change` | personality/personality_resolver.py:158/167 `resolve`（orchestrator 聊天主链 :1152/2405、growth/pipeline 多处、runtime_core.py:5758） | 是（每次解析即改 `_trait_states`） | **否**（legacy 成长累积直写） | 仅内存 snapshot | **高（高频静默直写面）** |
| PER-04 | src/personality/personality_adapter.py:466 `apply_proposal` | proposal_manager.py:427、runtime_core.py:1999（预览）、runtime_growth_pipeline.py:420——三调用点全部 `mark_approved=True`（自批），只写内存不落盘 | 是（内存） | **半途**（白名单+限流有；ApprovalManager 无） | envelope + limiter；多数调用点无 audit log | 中 |
| PER-05 | src/personality/personality_evolution_pipeline.py:213 `apply_approved_to_state` | runtime/runtime_core.py:6280 `drain_approved_growth_proposals`（Stage 13 + 启动兜底） | 是 | **经过**（admin 治理存储 APPROVED 转 canonical GrowthProposal） | PersonalityEvolutionRecord + audit trail + save | 低 |
| PER-06 | load_personality_state（into=_ps 恢复写） | runtime/runtime_core.py:1276 启动恢复 | 是（整状态改写，版本单调保护） | 否（恢复语义） | 否 | 中 |

### 3.6 self_model

| Mutation ID | 入口 | 当前路径 | 直接改状态 | 需 Proposal | Audit | 风险 |
|---|---|---|---|---|---|---|
| SM-01 | src/personality/self_model_store.py:149 `update` | growth/growth_integration.py:407 `_refresh_self_model`（Stage 4 链）+ personality_resolver.py:337 `resolve`（orchestrator 链） | 是（整模型重建，消费已生效成长） | 否（投影性质） | version 计数 + 原子写；**resolver 侧无 audit** | 中 |
| SM-02 | src/personality/self_model_store.py:174 `set_experience_context` | orchestrator.py:296 `__init__`（历史经历恢复） | 是 | 否 | 否（仅 status 计数） | 低 |
| SM-03 | src/personality/self_model_updater.py:228 `apply_proposal`（→ store.apply_change_proposal） | orchestrator.py:1365 Step 14.6（GovernancePolicy auto_apply，**不清 requires_approval、绕过 Adapter 安全闸**）+ runtime_core.py:2072 + self_model_updater_adapter.py:181（安全闸 requires_approval==False） | 是 | 半途（SelfModelChangeProposal + Governance 评估；orchestrator 路径绕过闸） | narrative 溯源字段（_source_growth_id 等）+ store 落盘 | 中 |
| SM-04 | src/personality/self_model_adapter.py:180 `save_state` | runtime/runtime_core.py:6171 Stage 13（仅 changed 时）+ self_model_bootstrap.py:140 + admin consumer | 是（持久化） | 不适用 | SelfModelPersistence JSONL（admin 有 Audited 包装） | 低 |
| SM-05 | src/personality/self_model_adapter.py:284 `apply_pcr` | runtime/pipeline/runtime_growth_pipeline.py:476（**src 内无 driver**）+ admin consumer | 是 | **半途**（白名单+confidence+limiter；**内部不验证批准**，信任 source_proposal_id） | 强（SelfHistory PCR_APPLIED + before/after snapshot + Reflection） | 中 |
| SM-06 | src/personality/self_model_adapter.py:684 `apply_external_change` | personality_evolution_pipeline.py:182（带 proposal_id，生产）+ personality_resolver.py:351（**proposal_id 为空**，orchestrator 链） | 是 | 半途（白名单+clamp；proposal_id 仅留痕不校验） | SelfHistory 事件 | 中（resolver 侧） |
| SM-07 | SelfModelUpdaterAdapter._sync_manager_understanding（self_model_updater_adapter.py:374-475）+ self_model_manager.py:277/476/597 | 直改 `manager.identity.stable_traits/understanding`、append growth/development history | 是（manager 旁路写，不经 Store/任何 audited 入口） | 否 | 否 | 中 |

---

## 4. 绕过路径分析（Phase 3）—— B 类债务清单

按「orchestrator 直接写 / manager 直接写 / repository 直接写 / event handler 直接写」四类绕过形态归纳：

| 债务 | 绕过形态 | 证据 | 影响 |
|---|---|---|---|
| **B1**：orchestrator Step 14.5 直通成长链 | orchestrator 直接写 | orchestrator.py:1327 → GrowthPipeline.incremental_update → 内存 proposal 直通 apply + apply_relationship → REL-03 八方法自存（GRO-01/GRO-02/REL-03） | 绕过 Proposal→审核→apply 闭环；与 Stage 4 正规链双轨并行，同一证据可能被两条链各自成长 |
| **B2**：PersonalityResolver.resolve 静默直写面 | manager 直接写 | personality_resolver.py:158/167/337/351（PER-03 + SM-01/06 resolver 侧） | 每次解析即改 trait/self_model，高频、零 Proposal、零 audit log；挂在聊天主链与 growth/pipeline 上 |
| **B3**：RuntimeController 伪造审批直写 | orchestrator 直接写 | runtime_controller.py:494-504 `p_rt_*`/`a_rt_*` 假 ID（PER-02） | 语法满足 EP-3、语义绕过 Approval 的人格写入口；不落盘（内存态漂移） |
| **B4**：双 Proposal 存储 + 多状态机 | 存储层分叉 | ProposalStore（JSONL, GRO-06）vs ProposalStorage（JSON, GRO-07）vs governance 存储（drain 消费源） | 无统一治理视图；drain（PER-05）只消费 governance 库，legacy 库自流转 |
| **B5**：自批入口群 | 调用点自批 | PersonalityAdapter.apply_proposal 三调用点全部 mark_approved=True（PER-04） | TraitStateUpdater 的 approved 校验形同虚设（目前仅写内存，风险受限） |
| **B6**：auto_apply 绕过安全闸 | orchestrator 直接写 | orchestrator.py:1365 直调 Updater，不清 requires_approval（SM-03） | 与 SelfModelUpdaterAdapter「仅已批准可写」闸门语义冲突 |
| **B7**：情绪写审计黑洞 + 不对称 | repository 直接写 | runtime_core.py:5157 无 audit 无事件（EMO-02）；orchestrator.py:2040 pre 无审计（EMO-01）；orchestrator.py:2075 filepath 现场改写（EMO-03） | 情绪变化不可追溯；共享持久化对象被临时改指向，存在 filepath 残留风险 |
| **B8**：memory 双主链并写防护不对称 | 双链并写 | orchestrator.py:1246（门+审计+sanitize）vs interaction_recorder.py:125（无门、无 sanitize 前置、审计靠 EventBus 间接）（MEM-01/02） | 同一对话可能写两条 user 记忆；publish_event 失败时写入无审计 |
| **B9**：relationship repository 直写无审计 | repository 直接写 | runtime_core.py:3263-3264（REL-01） | 关系变化仅靠域事件间接进 audit，主写入点无直写审计 |
| **B10**：审计默认关闭 | 配置默认 | ProposalManager._record_audit 默认 no-op（runtime_core 默认装配不注入 audit）；personality_event_bus_enabled 默认 False | 承诺的审计链路多数实际不落记录 |
| **B11**：MutationJournal 零写入 | 基础设施未接线 | request_context.py:375 定义；pipeline/core 全 src 无写入 ctx.mutations 的调用 | v2 契约的 mutation 审计面形同虚设 |
| **B12**：self_model manager 旁路写 | manager 直接写 | self_model_updater_adapter.py:374-475；self_model_manager.py:277/476/597（SM-07） | 不经 Store/adapter 审计面的理解态直改 |
| **B13**：死入口库存 | 死代码 | apply_relations、PersonalityStateUpdater.apply、set_personality_evolution_view、update_from_growth(_records)、GrowthEngine.apply_batch、RelationshipState.update_activity、RelationshipRepository.save_cognitive_profile/save_all_v10/v11、GrowthLoop 全链、RuntimeGrowthPipeline、GrowthIntegrationService.apply_proposal（GRO-05 断链）、ProposalManager.apply_proposal 间接触达 | 入口表冗余；治理文档承诺的链路实际未接线，形成「看起来有门」的盲区 |
| **B14**：潜伏 bug | 签名不匹配 | emotion_growth_service.py:94/97 `self.self_model_store.save(model)` 传参，save() 无参 → 运行时 TypeError（该链 src 内无驱动方，暂未触发） | fallback 路径一旦接线即崩溃 |

---

## 5. 未来统一路径设计（Phase 4）

### 5.1 目标路径

```
事件来源（MemoryCreated / EmotionChanged / RelationshipChanged / 对话行为）
    ↓
MutationRequest（v2 ctx.mutations 目标形态：target 模块 + form + payload + evidence）
    ↓
Proposal（GrowthProposal / SelfModelChangeProposal，附 evidence 门槛 + 置信度门槛）
    ↓
Approval（ApprovalPolicy.evaluate：identity_anchor / evidence / conflict /
        interest_transition；ApprovalManager.govern_proposal；红线不触发 apply）
    ↓
Apply Adapter（域专属 Apply Adapter：PersonalityAdapter / SelfModelUpdaterAdapter /
        RelationshipAdapter；统一校验「已批准才可写」）
    ↓
Audit（record_audit_log + 域事件总线 + MutationJournal + 域内演化历史）
```

已有且**合规的组件**直接复用：`accept_experience`（Stage 4 正规链，PER-01 证据）、`ApprovalManager`/`ApprovalPolicy`（src/approval/）、`EvolutionPipeline`（EP-1 强制 approved + EP-3 校验）、`SelfModelUpdaterAdapter.apply_suggestion`（requires_approval 安全闸）、`audit_event_handler`、各域 EvolutionRecord/SelfHistory 历史载体。

### 5.2 模块迁移优先级

| 优先级 | 域 | 动作 |
|---|---|---|
| **P0（人格红线，最先）** | personality / self_model | ① 关闭 PER-03 直写：resolver 改为只读解析（trait 变化一律走 Proposal 链）；② 关闭 PER-02 伪造 ID（或正式接入 ApprovalQueue）；③ 统一 SM-03：orchestrator auto_apply 改走 Adapter 安全闸并清 requires_approval；④ 收口 SM-07 manager 旁路写；⑤ B5 自批点改接真实审批结果 |
| **P1（闭环接续）** | growth | ① 关闭 B1：orchestrator Step 14.5 直通链切到 accept_experience（Stage 4 正规链成为唯一 growth 入口，删除双轨）；② B4 存储归一：canonical ProposalStore 唯一权威，legacy ProposalStorage 迁移/冻结；③ GRO-05 断链补接或显式下线 |
| **P2（审计补齐）** | emotion / relationship | ① B7：stage 03 与 pre 链补 audit + 域事件；filepath 现场改写改为显式 per-user 仓库注入；② B9：record_relationship_interaction 补直写 audit；③ B8：InteractionRecorder 补权限门 + sanitize + 直写 audit（与 orchestrator 侧对称）；④ B10：audit 注入默认开启 |
| **P3（基础设施）** | runtime（契约面） | ① B11：MutationJournal 接线（Stage 写口统一 append mutation 记录）；② B13：死入口清理/下线（缩小入口表）；③ B14：修复 save(model) 签名 bug；④ REL-04/05 事件处理器维持 append-only 红线，纳入 Gateway 白名单 |

memory 域**保持现状**（AGENTS.md Rule 2 允许直写），仅要求 P2 的审计对称性补齐。

### 5.3 迁移中的不变式（冻结）

1. Identity Core / 核心价值观 / 不可变原则永远不可被 Mutation 触碰（identity 只读）。
2. 「事件 → 理解 → 评估 → Proposal → 审核 → 应用」六步顺序不可逆序、不可跳步；PER-01 是唯一合规 apply 范本。
3. 事件处理器（EventBus 回调）永远只允许 append 观察记录或产 Proposal，禁止直改 5 维关系值 / trait / 模型字段（REL-04 红线先例）。
4. 每个 Mutation 必须可回滚（before/after + 落盘历史）；audit 失败时写入不得静默通过（或至少告警落盘）。

---

## 6. 附录

### 6.1 死入口清单（src 内零调用者）

memory: `MemoryStore.add_many` / `delete`；emotion: `EmotionPatternRepository.append/save_all`、`record_emotion_event`（runtime_core）、`notify_emotion_changed`；growth: `GrowthEngine.apply_batch`、`GrowthState.add_identity/set_behavior`、`GrowthIntegrationService.apply_proposal`、`ProposalStorage.delete`、`src/runtime/growth/*` 包（无 import 者）、`GrowthLoop` 全链（仅 admin API）；personality: `apply_relations`、`PersonalityStateUpdater.apply`、`set_personality_evolution_view`；self_model: `update_from_growth` / `update_from_growth_records`；relationship: `save_cognitive_profile` / `save_all_v10` / `save_all_v11`、`update_activity`（仅类内衰减）。

### 6.2 证据索引（抽查实证）

| 事实 | 证据 |
|---|---|
| 伪造审批 ID | runtime_controller.py:494-496（`p_rt_{turn_uuid}`/`a_rt_{turn_uuid}` → apply_evolution） |
| resolver 直写 | personality_resolver.py:158（update_trait）、:167（record_change）、:337（store.update）、:351（apply_external_change 无 proposal_id） |
| orchestrator 直通成长链 | orchestrator.py:1327（incremental_update）、:1361-1365（governance auto_apply） |
| orchestrator 直写面 | orchestrator.py:296（set_experience_context）、:1246（memory add）、:1261（vector）、:2040/:2075（emotion pre/post） |
| runtime_core 直写面 | runtime_core.py:3263-3264（relationship save）、:5157（emotion process_event）、:6280（drain） |
| 事件处理器写面 | events/handlers.py:70/88/98 → relationship_candidate_bridge.py:111、experience_bridge.py:330、growth/proposal/storage.py:80 |
| audit 默认关闭 | proposal_manager.py `_record_audit`（runtime_core.py:5668 默认装配不传 audit） |
| 潜伏 bug | emotion_growth_service.py:94/97 `save(model)` vs self_model_store.py:314 无参签名 |

### 6.3 本任务边界声明

未修改 src/、data/、tests/ 任何文件；未修改 memory/emotion/growth/personality/relationship/runtime 任何行为。所有行号以 225d040 + 当前未提交工作区为准（工作区含 P2.3-A 系列未提交改动，与本审计无关）。
