# Relationship 域状态变化边界审计报告（P2.3-B.8，只读审计）

> 阶段性质：**只读审计**。本阶段不修改 `src/`、`data/`、`tests/`，不写 adapter、不写 gateway、不改 runtime、不改 relationship 代码。
> 唯一产出：本文档（`docs/governance/relationship_mutation_boundary_audit.md`）。
> 目的：为 B.9（Relationship Mutation Gateway 迁移）建立实施依据。
>
> 审计日期：2026-08-20
> 审计对象：`src/relationship/` 全量 + `src/runtime/runtime_core.py` / `src/orchestrator.py` / `src/growth/` / `src/personality/` / `src/events/` / `src/admin/` 中的 relationship 状态写入口。

---

## 1. Phase 0 — 基线快照

| 项目 | 值 |
| --- | --- |
| branch | `develop/v1.1` |
| HEAD | `225d0408e89b475605d62b0435f85a9954ffb024` |
| `src/relationship/` 文件数 | 24 个 .py |
| `data/relationship_state.json` | 存在（v0.6 长期状态） |
| `data/relationship_core/` | 存在（proposals / core / activation JSONL；`data/users/366648462/`、`data/users/yuyi/` 存在） |
| B.3 Governance 骨架 | 已就绪：MutationGateway（CHECK_ORDER: identity→boundary→evidence→conflict→audit，first-fail-stops）、MutationRequest（9 字段 frozen）、DecisionVerdict、AuditWriter、`DOMAIN_GATES` 含 `relationship=can_modify_relationship`、`BoundaryCheck.DOMAIN_NAMESPACES` 含 `relationship.` 且已预置规则「relationship: 直改 trust/bond → NEED_REVIEW（必须进入关系提案复核队列）」 |
| B.4（personality） | 已迁移（`src/personality/mutation_adapter.py` + 报告） |
| B.5（growth） | 已迁移（`src/growth/mutation_adapter.py` + `pipeline._route_proposal_through_gateway` + 报告） |
| B.6（emotion） | 已审计（emotion mutation boundary audit 报告） |
| B.7（emotion migration） | 已完成（`src/emotion/mutation_adapter.py` + Stage_03 身份门 + 报告 `p26b7_emotion_mutation_migration_report.md`） |
| 本阶段修改 | **零**（见 §7 只读验证） |

---

## 2. Phase 1 — Relationship 状态模型测绘

### 2.1 状态权威与落盘矩阵（关键结论：存在三套并行状态权威）

| 权威 | 对象 / 文件 | 落盘位置 | 可变 | 治理现状 |
| --- | --- | --- | --- | --- |
| **v3.5.27（运行时每轮互动）** | `src/relationship/relationship_state.py` → `RelationshipState`（familiarity/trust/collaboration/interaction_frequency/communication_style/relationship_stage，0-1 clamp） | `data/users/<uid>/relationship_state.json`（`RelationshipRepository.save_state`，per-user 实例） | 是（mutable dataclass） | **无 Gateway/Proposal/Approval**（R-01/R-02/R-03） |
| **v0.6（长期关系，Growth 驱动）** | `src/personality/relationship_state.py` → `RelationshipState`（bond_strength/trust/familiarity/promise_level/shared_history/activity_level/milestones/important_events；成熟度约束：`max_trust=0.2+familiarity*0.8`、`max_bond=0.3+fam*0.4+history*0.3`；每个 `update_*` 立即原子落盘） | `data/relationship_state.json` | 是 | 治理完全依赖调用方（R-04/R-06/R-15） |
| **Legacy profile（Phase 7，休眠）** | `rel_profile` dict（trust/familiarity/events）经唯一旁路入口 `apply_legacy_relationship_profile_delta` 写入 | `data/relationships/<uid>.json` | 是 | 有 identity 门 + audit，但**当前主链不触发**（R-07/R-08） |
| **RelationshipModel（数据日志）** | `src/relationship/relationship_model.py`（interaction_history≤300 / trust_changes≤200 / emotional_patterns / shared_experiences / milestones≤100） | `data/users/<uid>/relationship_model.json` | 是 | 数据日志（非数值权威），随 R-01 一起被直写 |
| **RelationshipCore（治理子域）** | `src/relationship/relationship_core.py`（relationship_id/source_user_id/type/events/agreements/boundaries/anchor_memory_ids/visibility） | `data/relationship_core/relationship_core.jsonl`（append-only，重复 relationship_id 拒绝，损坏→`*.corrupt.*` 备份） | 是 | **A 类**：入库必须经 5 态提案 + 人工 approve + 显式激活（R-11/R-12/R-13） |
| **RelationshipProposal / ActivationRecord** | `relationship_proposal.py`（6 态，approve 仅 pending_review+人工 reviewer；rejected 终态）、`relationship_activation.py`（PREPARED→COMPLETED 事务日志，禁止 LLM/系统调用） | `data/relationship_core/relationship_proposals.jsonl` / `activation_records.jsonl`（append-only，latest-wins） | 是（受状态机约束） | **A 类**：人工治理端点唯一入口 |

**Attachment / Affinity / Trust / Bond 对象检查结论**：全仓不存在独立的 `Attachment`、`Affinity` 数据类。`bond` 只存在于 v0.6 `bond_strength` 与 RelationshipCore 的 agreements/boundaries；`trust` 存在于 v3.5.27 `RelationshipState.trust` 与 v0.6 `trust`；`attachment_level` 仅在 `_apply_relationship_state`（self-model 遗留读取键）中出现。`RelationshipProfile`（Phase 10.1）、`RelationshipCognitiveProfile`、`RelationshipMemory`（R2.5.2-B EventStore）、`RelationshipContext`（聚合视图）均为只读数据类或视图对象，无写入口（§2.3 写扫描已确认）。

### 2.2 写操作全仓扫描结果

`src/relationship/` 下只有 4 个文件含落盘写操作：`relationship_repository.py`（save_state/save_relationship_model/save_cognitive_profile + Phase 7 legacy `save(RelationshipInfluenceProfile)`）、`relationship_core_store.py`、`relationship_proposal_store.py`、`relationship_activation.py`。其余 20 个文件为状态对象 / 提取器 / 评估器 / 只读适配器。

`RelationshipInfluenceProfile`（Phase 7 legacy，含 `PersonalityInfluence` 列表）全仓无生产消费者（仅 docstring 引用）→ C 类遗留。

### 2.3 Mutation Registry（RID 登记表）

| RID | 文件:行号:function | 修改目标 | 调用链 | 直接写 | 经过 Proposal | 经过 Approval | 经过 Audit | 风险 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R-01 | `src/relationship/relationship_model.py:103-118` `RelationshipIntelligenceEngine.process_interaction` | v3.5.27 `RelationshipState`（trust/familiarity/collaboration/interaction_frequency/relationship_stage）+ `RelationshipModel` | RuntimeCore.process Stage_03 → `_relationship_update`（runtime_core.py:5211）→ `record_relationship_interaction`（runtime_core.py:3236）→ 本函数 | **是**（内存直改，R-02 落盘） | 否 | 否 | 否 | **高** |
| R-02 | `src/runtime/runtime_core.py:3263-3264` `record_relationship_interaction` | `data/users/<uid>/relationship_state.json` + `relationship_model.json` | 同上（链上唯一持久化点） | 是（repository 直写） | 否 | 否 | 否（无任何审计日志） | **高** |
| R-03 | `src/runtime/runtime_core.py:5211-5260` `_relationship_update` | Stage_03 写路径触发（仅 `event_type in (None,"user_input")` 且 user_message 非空）；evidence 降级锚定到**在途 experience_id**（尚未落库） | RuntimeCore.process Stage_03 | — | 否 | 否 | 否 | 高（**无身份门**，sandbox/未知身份同样触发；与 orchestrator P2.1.3-R、B.7 emotion Stage_03 门不同） |
| R-04 | `src/growth/growth_engine.py:336-389` `apply_relationship` | v0.6 `RelationshipState`（trust/bond/familiarity/promise/shared_history/milestones/important_events，delta=0.02~0.10 × importance） | GrowthPipeline.incremental_update ← Orchestrator Step 14.5（orchestrator.py:1327）；pipeline 内 615（提案已应用后）/633（legacy apply fallback）/786（batch 路径） | 是（经 v0.6 `update_*`，每次立即落盘） | **部分**（615 上游提案已批准；633/786 无） | 部分 | 否 | 中 |
| R-05 | `src/growth/pipeline.py:615,633,786` | R-04 的调用点（B.5 gateway 只裁决 GrowthState 提案，不覆盖 apply_relationship 的关系增量本身） | 同上 | — | 见 R-04 | 见 R-04 | 否 | 中 |
| R-06 | `src/personality/relationship_evolution.py:160-227` `RelationshipEvolution.evaluate/_apply_to_state` | v0.6 `RelationshipState`（读 GrowthHistory 信号，MIN_EVIDENCE=2，单次限幅 trust/fam 0.03、bond 0.02、history 0.03） | **生产无调用方**（全仓无引用，预留设计） | 是（若有调用） | 上游记录已治理 | 否 | 否 | 低（C 类休眠） |
| R-07 | `src/orchestrator.py:2131-2225` `Orchestrator._process_relationship_post` | legacy `rel_profile` dict → `data/relationships/<uid>.json` | Orchestrator.process Step 12（orchestrator.py:1287）← `assembled_context`（**rel_repo / rel_profile 从未被 assemble_context 提供 → 实际休眠**） | 是（经 R-08 唯一入口） | 否（trust_delta≥0.10 才建 GrowthProposal，而 evaluator 恒拒 type="interaction" → delta 恒 -0.02，阈值永不可达） | 否 | 是（relationship.bypass_write + relationship.changed） | 低（休眠；有身份门 P2.1.3-R） |
| R-08 | `src/orchestrator_hooks.py:120-194` `apply_legacy_relationship_profile_delta` | 同上（生产代码唯一允许的 legacy profile 直写点，clamp + 审计 + provenance，AST 测试强制无其他旁路） | R-07；模块级 `_process_relationship_post`（197-251）标注 LEGACY 无调用方 | 是 | 否 | 否 | 是 | 低（休眠） |
| R-09 | `src/runtime/runtime_core.py:3285-3310` `_forward_relationship_event_to_growth` + `src/growth/relationship_evidence_adapter.py` | Growth 证据/提案（GrowthIntegrationService.process_event → GrowthProposal(pending) → Approval；relationship_context 域 proposed_changes 恒空、delta=0，纯证据记录） | R-02 之后（fail-soft） | 否（不写关系状态） | **是** | 是 | 提案系统自带 | 低（**A 类**） |
| R-10 | `src/events/handlers.py:51,59` | RELATIONSHIP_CHANGED → `audit_event_handler` + `create_proposal_from_event`（`src/growth/proposal/reviewer.py:213`）→ GrowthProposal(RELATIONSHIP) | EventBus | 否 | **是** | 是 | 是 | 低（**A 类**） |
| R-11 | `src/relationship/relationship_candidate_bridge.py:62-126` `on_memory_created` | RelationshipProposal（candidate→evaluating→pending_review，**系统侧永不超过 pending_review**）→ append-only store | EventBus MEMORY_CREATED（handlers.py:67-70） | 否（只写提案） | **是**（提案生成） | 是（人工 approve 端点） | 提案系统自带 | 低（**A 类**） |
| R-12 | `src/admin/api/routes.py:3507-3690` | 人工治理端点：list / create / **approve（到达 accepted 唯一入口）** / reject（终态）；`AuditLogger` HUMAN 记录 | Admin HTTP | 否（状态机约束） | **是** | **是（人工）** | 是 | 低（**A 类**） |
| R-13 | `src/admin/api/routes.py:3717-3793` + `src/relationship/relationship_activation.py` | RelationshipActivationService.activate：PREPARED→COMPLETED 事务日志 + core 落盘 + 锚点播种；**禁止 LLM/系统调用** | Admin HTTP（人工治理端点） | 是（仅人工触发） | **是** | **是（人工）** | 是（事务日志） | 低（**A 类**） |
| R-14 | `src/personality/self_model_manager.py:665-705` `_apply_relationship_snapshot` | `SelfIdentity.preferences` 直写 3 条 `domain="relationship"` Preference（bond_strength/familiarity_level/trust_level；confidence 恒 0.5、evidence_count 递增） | runtime_core.refresh_self_model_from_runtime（2269）← Stage_09/Stage_14、schedule_reflection、reflection scheduler（auto_refresh_self_model） | **是**（不经 GovernancePolicy / apply_change_proposal） | 否 | 否 | 否 | 中（**绕过 Orchestrator Step 14.6 的 growth-record 治理链**） |
| R-15 | `src/personality/relationship_state.py:188-251` v0.6 `update_*` 系列 | v0.6 状态对象：每个 update 立即原子落盘；对象内部**无治理**，治理完全依赖调用方 | R-04 / R-06（多实例同文件，per-path RLock 串行化） | 是 | 依赖调用方 | 依赖调用方 | 否 | 中（基础设施级） |

---

## 3. Phase 2 — 调用链分析

### 3.1 用户消息如何影响 relationship（三条路径）

```
用户消息
 ├─ [路径A 活跃] RuntimeCore.process Stage_03（runtime_core.py:5175）
 │    └─ _relationship_update → record_relationship_interaction
 │         └─ RelationshipIntelligenceEngine.process_interaction
 │              ├─ RelationshipEventExtractor（单条消息关键词：1 个"相信/信任/放心/可靠/稳定"即触发 trust_building，置信 0.7~0.9）
 │              ├─ RelationshipEvaluator（6 检查，MIN_EVIDENCE_COUNT=1 → 单条消息即可通过）
 │              ├─ **直接改 state**：trust +0.06 / familiarity +0.02 / collaboration +0.08 / boundary_respect +0.05 / preference_learning +0.01/0.06
 │              ├─ interaction_frequency **无条件 +0.03**（与事件类型无关）
 │              └─ relationship_stage 自动推导（_infer_stage）
 │         └─ repository.save_state + save_relationship_model（落盘，无审计）
 │         └─ 转发 RelationshipEvent → Growth 证据链（R-09，fail-soft）
 ├─ [路径B 活跃] Orchestrator.process Step 14.5 GrowthPipeline.incremental_update（orchestrator.py:1327）
 │    └─ 自由文本事件提取 → evaluator → 提案路径（B.5 gateway）或 legacy apply
 │         └─ apply_relationship（R-04）→ v0.6 state 落盘 data/relationship_state.json
 │              （提案路径：growth 变更已过 Proposal/Approval，关系增量为下游执行；
 │               legacy fallback：无 Proposal/Approval，仅成长评估器把关）
 └─ [路径C 休眠] Orchestrator Step 12 _process_relationship_post（orchestrator.py:1287）
      └─ assembled_context 不提供 relationship_repo / relationship_profile → 恒早退
           （legacy profile 旁路入口保留：身份门 + 审计 + AST 保护，但不活跃）
```

**注意**：同一轮用户消息可同时走路径 A 与路径 B，即 v3.5.27 与 v0.6 两套信任值**双倍累积**。两套状态由 `RelationshipSnapshotBuilder`（`src/relationship/snapshot_builder.py`，只读）在消费侧合并：v3.5.27 为 current、v0.6 为 long_term。

### 3.2 系统事件是否可以影响 relationship

| 事件源 | 链路 | 治理状态 |
| --- | --- | --- |
| memory（MEMORY_CREATED） | → RelationshipCandidateBridge → RelationshipCoreEvaluator（纯规则、确定性、不调 LLM）→ RelationshipProposal（≤pending_review） | A 类（R-11，绝不直写关系数值） |
| memory（记忆落库） | → R2.5.2-B RelationshipMemory EventStore（结构式 observed 事件，禁止数值写入） | A 类（observed-only 契约） |
| growth（GrowthPipeline 事件） | → apply_relationship → v0.6 state（R-04/R-05） | 提案路径半治理 / legacy fallback 无治理（B 类） |
| growth（GrowthHistory 记录） | → RelationshipEvolution（R-06） | 设计存在但**无生产接线**（C 类） |
| emotion | emotion_tag → RelationshipModel.emotional_patterns（数据日志，非数值维度） | 日志级，随 R-01 直写 |
| achievement | **未发现任何 relationship 写入口** | — |
| runtime（Stage_03 / Stage_09 / Stage_14 / reflection） | 每轮 relationship 更新（R-01~R-03）；self-model 刷新读 long_term → 直写 self-model preference（R-14） | B 类 |
| RELATIONSHIP_CHANGED 事件 | → audit + create_proposal_from_event（R-10） | A 类（治理闭环） |

### 3.3 LLM 输出是否可以直接改变 relationship

**结论：否，当前不存在 LLM → relationship 数值的直接通路。** 逐项确认：

- `RelationshipEventExtractor.extract` 只消费 `user_message`；LLM 的 reply 不进任何 mutation 入口。
- `RelationshipEvent` 契约为 `status="observed"` 冻结 11 键，「绝对不把数值写入 bond/trust/familiarity」；`build_relationship_event_from_record` 只做结构翻译。
- `RelationshipCore` docstring 硬约束：「禁止『LLM 认为重要 → 直接构造落库』，入库必须经过人工审核生命周期」；`RelationshipCoreEvaluator` 纯规则不调 LLM。
- 但需注意 R-14 反向耦合：relationship 数值（long_term）会经 self-model 刷新直写自我认知，而 self-model 后续会影响表达层——即「LLM 输出 ← self-model ← relationship 数值」的读链存在，只是写链上 LLM 不在起点。

### 3.4 handler 直写盘点

全仓实际存在 3 个 relationship 状态直写 handler/入口：

1. `RelationshipIntelligenceEngine.process_interaction`（R-01）——活跃，无身份门、无审计。
2. `GrowthEngine.apply_relationship`（R-04）——活跃（growth 链），无审计，legacy fallback 分支无提案。
3. `apply_legacy_relationship_profile_delta`（R-08）——休眠，有身份门与审计（relationship.bypass_write），AST 测试强制唯一入口。

### 3.5 是否存在无 evidence 的 trust/bond 永久提升

**结论：存在，两处。**

1. **R-01（最严重）**：`MIN_EVIDENCE_COUNT=1`，evidence 可为**在途 experience_id**（runtime_core.py:5244-5255 注释自认「当轮 experience 通常仍在构建…降级锚定到在途 experience_id…（evaluator MIN_EVIDENCE_COUNT=1 要求非空）」——即 evidence 非空即可通过，不要求已落库记忆）。单条含「相信」的消息即可使 trust **永久 +0.06 并落盘**。`interaction_frequency` 更是无条件 +0.03。这是本审计发现的中心 B 类缺口。
2. **R-04 legacy fallback 分支**：`apply_relationship` 的 delta 只由事件 importance 驱动（0.02~0.10 档），无 relationship 专属证据数量门槛；仅当走 615 提案路径时上游才过治理。

### 3.6 relationship 是否可以绕过 Proposal 修改 personality / self_model

- **personality：否（当前）。** 未发现 relationship 直写 personality 的路径。`PersonalityResolver` 只**读** relationship 信任值（personality_resolver.py:117-135：`growth_trust*0.4 + rel_trust*0.6` 组合后影响解析，属只读耦合）。跨域写向仅有一条被治理的桥：RelationshipEvent → Growth 证据适配器（R-09，relationship_context 域 delta 恒 0）+ RELATIONSHIP_CHANGED → GrowthProposal（R-10）。
- **self_model：是。** `SelfModelManager._apply_relationship_snapshot`（R-14）把 `bond_strength/familiarity_level/trust_level` 三条 relationship 数值直写进 `SelfIdentity.preferences`，不经 `GovernancePolicy.evaluate` / `apply_change_proposal`（对比 Orchestrator Step 14.6 对 growth records 的治理链）。调用点在 RuntimeCore Stage_09/Stage_14 与 reflection scheduler，属活跃路径。此为第二大 B 类缺口。

---

## 4. Phase 3 — 治理缺口分类

### A 类（符合治理链，保持不动）

| 项 | 依据 |
| --- | --- |
| RelationshipCore 子域全套（core/proposal/activation） | 5 态提案状态机（candidate→evaluating→pending_review 系统上限）、人工 approve 唯一到 accepted、rejected 终态、激活为独立人工步骤、事务日志、append-only 存储、损坏备份 |
| R-09 relationship→growth 证据适配器 | 全链 Normalizer→Validator→Matcher→GrowthEvaluator→GrowthIntegrationService→GrowthProposal(pending)→Approval；relationship_context 域 delta=0 纯证据 |
| R-10 RELATIONSHIP_CHANGED→提案闭环 | 事件→审计→GrowthProposal |
| R-11 候选桥接 | 系统侧永不超过 pending_review |
| R-12/R-13 人工治理端点 | approve/reject/activate 全部 HUMAN + 审计 |
| RelationshipCoreAdapter / RelationshipRuntimeAdapter | 只读硬约束（绝不写 store / state / 其他域） |
| RelationshipEvent 契约 | observed-only，禁止数值写入 |

### B 类（绕过 Gateway / Proposal / Audit——B.9 迁移对象）

| 优先级 | 项 | 缺口 |
| --- | --- | --- |
| **B-1** | R-01/R-02/R-03：`process_interaction` 直改 v3.5.27 状态并落盘 | 无 Gateway / Proposal / Approval / Audit；无身份门；evidence 可锚定在途 experience；单条消息即触发永久 trust 提升 |
| B-2 | R-14：self-model preference 直写 | 绕过 growth-record 治理链（GovernancePolicy / apply_change_proposal），confidence 恒 0.5 |
| B-3 | R-04/R-05：`apply_relationship` legacy fallback 分支（pipeline.py:633/786） | 无提案即更新 v0.6 trust/bond；B.5 gateway 不覆盖关系增量 |
| B-4 | R-15：v0.6 状态对象无内部治理 | update 即落盘；治理完全依赖调用方（作为基础设施列入 B.9 统一由网关裁决） |

### C 类（死代码 / legacy）

| 项 | 说明 |
| --- | --- |
| R-06 `RelationshipEvolution` | 设计完整（GrowthHistory 输入、MIN_EVIDENCE=2、限幅）但全仓无生产调用方 |
| R-07/R-08 legacy profile 旁路 | 有身份门 + 审计 + AST 唯一入口保护，但 assembled_context 从不提供 rel_repo/rel_profile → 休眠；模块级 `_process_relationship_post` 标注 LEGACY 无调用方 |
| `RelationshipInfluenceProfile`（Phase 7） | 无生产消费者；`RelationshipRepository.save()` legacy 入口无活跃调用 |
| `relationship_profile.py` Phase 10.1 / `relationship_cognitive_profile.py` | 只读数据类，写入口不存在 |

---

## 5. Phase 4 — B.9 设计建议（只写文档，不实现）

### 5.1 目标治理链（与任务书一致）

```
RelationshipEvent（extractor 产出，observed）
        ↓
RelationshipMutationAdapter（src/relationship/mutation_adapter.py，镜像 B.5/B.7 模式）
        ↓
MutationRequest（target_domain="relationship"，
                 target_path 建议：relationship.state.trust /
                 relationship.state.bond / relationship.state.familiarity /
                 relationship.state.collaboration / relationship.self_model.*）
        ↓
MutationGateway（CHECK_ORDER：identity→boundary→evidence→conflict→audit，first-fail-stops）
        ├─ ACCEPT      → RelationshipApplyAdapter（apply_delta + save，唯一写入口）
        ├─ REJECT      → 不改状态
        ├─ NEED_REVIEW → 进入关系提案复核队列（RelationshipProposal 已有 5 态状态机可复用）
        └─ DEFER       → 延迟队列
```

B.3 已预置的复用件（B.9 无需新建）：`DOMAIN_GATES["relationship"]=can_modify_relationship`；`BoundaryCheck` 的 `relationship.` 命名空间与规则「直改 trust/bond → NEED_REVIEW（必须进入关系提案复核队列）」；`EvidenceCheck` 5 规则（<3 证据→NEED_REVIEW、|delta|>0.01→NEED_REVIEW 等，对 relationship 的 0.02~0.08 增量天然生效）；AuditCheck（request_id/trace_id 缺失→DEFER）。

### 5.2 允许自动变化 vs 必须治理（任务书冻结清单）

**允许自动变化（ACCEPT，但仍须走网关 + 审计）**：
- 临时互动反馈：`interaction_frequency` +0.03（session 级互动反馈）
- 短期 mood-like relationship signal：emotional_patterns 数据日志
- interaction_history 追加（上限 300，纯日志）
- `relationship_stage` 的短期推导信号（建议降级为只读展示，不直接作为格化状态落盘）

**必须治理（NEED_REVIEW / 进入关系提案复核队列）**：
- **trust 永久变化**（v3.5.27 `state.trust` 与 v0.6 `trust` 双侧；当前 R-01 单消息 +0.06、R-04 importance 驱动增量都命中此条）
- **bond 等级提升**（v0.6 `bond_strength`）
- **attachment 建立**（RelationshipCore activation——已治理，保持现状即可）
- **familiarity / collaboration 永久增量**（建议纳入，与 trust 同风险级）
- **relationship → personality**（当前只有只读耦合；若未来引入影响，必须走 GrowthProposal，禁止适配器直改）
- **relationship → self_model**（R-14：改为经 GovernancePolicy 或 `apply_change_proposal`；或把 relationship preference 挂到 R-09 的证据链上以提案形式进入）

### 5.3 建议迁移顺序（B.9 实施蓝图）

1. **B.9.1** 身份门接入 RuntimeCore Stage_03（对标 B.7 emotion Stage_03：`can_modify_relationship(resolve_identity(uid))`，fail-open 保旧行为）——先封 R-03 的无身份门缺口。
2. **B.9.2** `RelationshipMutationAdapter` 接线 `record_relationship_interaction`：flag `relationship_mutation_gateway_enabled=False` 默认关闭，Flag=True 时 `process_interaction` 的计划增量改为 MutationRequest 流，ACCEPT 才 apply+save（镜像 B.7 `_apply_accepted_emotion`）。
3. **B.9.3** self-model preference 治理（R-14）：relationship 数值进 SelfIdentity 改经治理链（或最低限度：加审计 + 来源标记 + 阈值）。
4. **B.9.4** GrowthPipeline 的 `apply_relationship`（R-04/R-05）纳入网关：legacy fallback 分支的 relationship 增量同样走 NEED_REVIEW 复核队列。
5. **B.9.5** legacy 退役计划（文档化即可）：R-06/R-07/R-08/RelationshipInfluenceProfile 标记退役，不删除、不加新调用。

### 5.4 与 B.3–B.7 衔接

- 复用 B.3 的 MutationGateway / MutationRequest / 检查器（零新增模块，满足 AGENTS.md Rule 4 的四个问题：已有网关无法完成——不，恰恰是已有网关可以完成；接入生命周期——Stage_03 + relationship 提案复核队列；数据流——Event→Request→Verdict→Apply；测试——镜像 `tests/test_emotion_mutation_gateway.py` 的 8 类用例）。
- 镜像 B.7 的成熟模式：模块 flag、A/B 回归（新增失败=0、data/ 零修改）、7 节迁移报告。
- 沿用 B.7 原则：保持旧行为兼容（flag 默认关）、不删除旧 API、不改变 relationship 数据格式（state/model JSON 结构不动）、不改变 prompt 表达层行为（`_build_relationship_prompt_context` 只读路径不动）、所有修改可回滚。

### 5.5 兼容与回滚原则

- flag 默认 `False` → 行为与今日完全一致；`True` 才走网关。
- 新增文件仅 `src/relationship/mutation_adapter.py`（+ 可选 apply adapter）；不动 `relationship_state.py` / `relationship_model.py` 数据契约。
- 每步 A/B 回归；备份回滚（B.7 已验证流程）。

---

## 6. Phase 5 — 只读验证

| 检查项 | 结果 |
| --- | --- |
| `src/` 修改 | **无新增修改**（git status 中所有 M/?? 条目均为 B.4/B.5/B.7 遗留，无一在 `src/relationship/` 下） |
| `data/` 修改 | **无**（`git status -- data/` 为空；审计期间未运行任何会写 data/ 的进程） |
| `tests/` 修改 | **无新增**（?? 条目为 B.5/B.7 新增测试文件，本阶段未触碰） |
| 本阶段唯一产物 | 本文档 |
| 审计期间代码读取 | 全程 Read/grep/sed 只读命令，无任何 Edit/Write 落在 src//data//tests/ |

---

## 7. 附录 — 关键代码锚点索引

| 锚点 | 位置 |
| --- | --- |
| process_interaction 直改 | `src/relationship/relationship_model.py:103-118,231-248` |
| _plan_deltas 增量表 | `src/relationship/relationship_model.py:231-238` |
| _infer_stage 阈值 | `src/relationship/relationship_model.py:240-248` |
| 提取器关键词规则 | `src/relationship/relationship_event_extractor.py:14-42,87-90` |
| 评估器 6 检查 / MIN_EVIDENCE_COUNT=1 | `src/relationship/relationship_evaluator.py` |
| Stage_03 接线 | `src/runtime/runtime_core.py:5175` |
| record_relationship_interaction | `src/runtime/runtime_core.py:3236-3280` |
| _relationship_update（写+读） | `src/runtime/runtime_core.py:5211-5293` |
| 证据降级到在途 experience_id | `src/runtime/runtime_core.py:5240-5255` |
| growth 证据转发 | `src/runtime/runtime_core.py:3285-3310` |
| apply_relationship | `src/growth/growth_engine.py:336-389` |
| pipeline 三个调用点 | `src/growth/pipeline.py:615,633,786` |
| 提案 reviewer 关系分支 | `src/growth/proposal/reviewer.py:151-197` |
| RelationshipEvolution（休眠） | `src/personality/relationship_evolution.py:160-227` |
| v0.6 状态 + 成熟度约束 | `src/personality/relationship_state.py:138-159,188-251` |
| legacy 唯一旁路入口 | `src/orchestrator_hooks.py:120-194` |
| Orchestrator legacy 后处理（休眠） | `src/orchestrator.py:2131-2225` |
| GrowthPipeline 触发 | `src/orchestrator.py:1319-1329` |
| self-model 直写 | `src/personality/self_model_manager.py:665-705` |
| refresh_self_model_from_runtime | `src/runtime/runtime_core.py:2269-2299` |
| 候选桥接（pending_review 上限） | `src/relationship/relationship_candidate_bridge.py:62-126` |
| 人工治理端点 | `src/admin/api/routes.py:3493-3793` |
| 只读适配器（双硬约束） | `src/runtime/adapters/impl/relationship_core_adapter.py`、`src/runtime/adapters/impl/relationship_runtime_adapter.py` |

**审计结论一句话**：Relationship 域的治理子域（RelationshipCore/Proposal/Activation）与跨域出站（relationship→growth 证据链）已达标（A 类）；但数值权威（v3.5.27 + v0.6 两套 trust/bond）的**入站写路径是直写**——`RelationshipIntelligenceEngine.process_interaction` 在无身份门、无提案、无审批、无审计的条件下，仅凭单条关键词消息即可永久提升 trust 并落盘，另有 self-model preference 直写一处；这正是 B.9 迁移的靶点。
