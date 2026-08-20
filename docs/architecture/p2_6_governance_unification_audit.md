# P2.6 Phase 0 治理统一审计

> 性质：只读审计 + 方案设计（禁止实现代码、不关闭生产功能、不迁移实现）
> 范围：personality / self_model / emotion / relationship / growth 五域全部写入路径
> 背景：架构冻结审查发现治理分叉——新链（Proposal→Approval→MutationGateway→Pipeline→Apply）
>       与旧链（Legacy Orchestrator Step 14.6 → SelfModelGovernancePolicy → policy auto_apply）并存
> 日期：2026-08-21

---

## 0. 结论摘要

1. 全系统共有 **11 条写入路径**，其中 **3 条属于旧链（Legacy Orchestrator）**，
   默认配置下**全部不经 MutationGateway、无需人工审核、可自动触发**。
2. 旧链并非完全休眠：`runtime.enabled=true` 时生产主链走 RuntimePipeline→RuntimeCore，
   **但 RuntimePipeline 有 per-request 回退**（RuntimeCore 抛错 → orchestrator.process），
   且 api_server 存在 pipeline 构造失败后的直连路径——回退一旦触发，旧链即恢复
   「每轮自动成长 + policy 自动 apply」。
3. 旧链的自动变化是**三层的**：GrowthState 数值（data/growth_state.json 覆盖写）→
   growth_history 每轮 append → SelfModel store（policy auto_apply）→ **下一轮
   PersonalityResolver.resolve() 的人格向量随之改变**。personality_state.json 本身不被旧链写
   （仅 drain 审核链写），但人格的实际表达（resolve 结果）已被改变。
4. 统一方案：新增 `governance_unification_enabled`（默认 False）四阶段灰度迁移，
   Phase D 之后 MutationGateway 成为人格变化唯一入口；approved drain 链保持不动。

## 1. 全量写入路径扫描

| # | 路径（调用者 → 中间层 → 最终写入） | 最终写入函数/落盘 | MutationGateway | 人工审核 | 可自动触发 | 状态 |
|---|---|---|---|---|---|---|
| P1 | admin /growth/review → GovernanceProposal(status=approved) → RuntimeCore.drain_approved_growth_proposals（stage13 + 启动兜底，runtime_core.py:6408）→ PersonalityEvolutionPipeline.apply_approved_to_state → PersonalityState.apply_evolution | save_personality_state() → data/personality_state.json（覆盖写） | **否**（不在 B.13 链内；治理锚点=admin 审核+Identity Continuity Check） | **是**（approve 唯一来源 admin） | 半自动（drain 每轮消费已审核队列） | 生产主链 ✅ |
| P2 | legacy orchestrator.process → Step 14.5 `_growth_pipeline.incremental_update(user_message)`（orchestrator.py:1327）→ GrowthEvaluator → （B.5 网关**关=默认**）GrowthEngine.apply_proposal（growth/pipeline.py:585） | GrowthState.update_metrics + state.save() → data/growth_state.json；growth_history append（resolver 共享实例） | **否**（B.5 `is_growth_mutation_gateway_enabled` 默认 False；开则走网关） | **否** | **是**（每条消息） | 旧链 ⚠️ |
| P2b | 同 P2 的 fallback：proposal 未生成/失败时 GrowthEngine.apply(event)（pipeline.py:630）→ apply_relationship + influence | GrowthState 写入 + relationship_state 写入 | **否** | **否** | **是** | 旧链 ⚠️ |
| P3 | legacy orchestrator.process → Step 14.6（orchestrator.py:1344-1375）→ SelfModelGovernancePolicy.evaluate(record) → auto_apply（context≥0.50 / preference≥0.65）→ SelfModelUpdater.apply_proposal → _apply_to_store | self_model_store 写入 → data/self_model/ | **否** | **部分**（trait≥0.80 走 approval_required，但队列是 orchestrator 内存态，重启即失） | **是**（policy 阈值自动） | 旧链 ⚠️ |
| P4 | 主链 Stage 05 PersonalityResolver.resolve()（personality_resolver.py:127）→ 读 GrowthState + growth_history 派生 PersonalityVector；resolver 内 `self_model_store.update(growth_history)`（line 348-359，should_update 门槛） | data/self_model/（update 时）；人格向量仅内存派生 | **否** | **否** | **是**（每轮 resolve；派生只读 + should_update 写 self model） | 生产主链 ⚠️ |
| P5 | 主链 Stage 13 `_stage_13_self_model_persistence`（runtime_core.py:6341）→ self_model_adapter.save_state（仅 ctx._self_model_changed） | data/self_model/（覆盖写） | **否** | **否** | **是**（变化即存） | 生产主链 |
| P6 | 主链 Stage 03 EmotionManager.process_event（emotion_manager.py:57-78）→（B.7 网关**关=默认**）state.apply_delta + repository.save | data/emotion_state.json（情绪域） | 可选（B.7 开→ACCEPT 才写） | **否** | **是** | 生产主链（域内，不影响人格数值） |
| P7 | EmotionGrowthService（情绪信念→self_model） | 旧直写 store.save；B.13 开→gateway NEED_REVIEW | 可选 | **否** | **否**（无生产调用者，未接线） | 休眠 |
| P8 | 主链 Relationship 更新（B.9 网关**关=默认**）→ relationship_model update+save | data/relationship_state.json（关系域） | 可选（B.9 开→逐维度网关） | **否** | **是** | 生产主链（域内） |
| P9 | GrowthIntegrationService.apply_proposal / ProposalManager.apply_proposal → PersonalityAdapter.apply_proposal（growth_integration.py:342 / proposal_manager.py:427） | TraitState / personality adapter 写入 | **否**（apply 链路不经网关） | **是**（admin API 人工调用；auto_accept=False 冻结） | 否 | 人工通道 |
| P10 | ApprovalManager（config `approval_manager_enabled` 缺省 False）→ adapter.accept/apply_proposal | personality adapter 写入 | **否** | **是**（审核层语义） | **否**（未启用） | 未启用 |
| P11 | P2.5 growth loop adapter（cognitive_activation.py）→ GrowthEvaluator → 内存 GrowthProposal | **零写入**（内存态，proposed_changes=[]） | n/a（零 mutation） | n/a | 是（flag 开时每轮，但零写入） | 已冻结 ✅ |

**扫描结论**：MutationGateway 目前在任何「默认开启」的写入路径中都不处于必经位置——
它是 opt-in 治理层（B.5/B.7/B.9/B.13 四个域 flag 全默认 False）。真正需要统一的
人格/self-model 写入入口是 **P2/P2b/P3（旧链三层）+ P4/P5（主链 resolver/stage13 侧写）**。

## 2. Legacy Orchestrator Step 14.6 重点分析

### 2.1 触发条件

全部满足才执行（orchestrator.py:1344-1348）：

1. `self._self_model_updater is not None`——构造期无条件 try 初始化（orchestrator.py:422-447），
   无 config 门控；仅导入/构造失败才为 None。
2. `self._governance_policy is not None`——同上。
3. `_growth_result.get("growth_records")` 非空——取决于 Step 14.5 `incremental_update`
   是否从当前消息提取出有效事件并通过评估（成长资格）。
4. `can_modify_personality(_request_identity)` 通过——请求方身份门。

前提是 `orchestrator.process()` 被调用（见 2.3）。

### 2.2 数据来源

- 唯一输入：**当前轮 user_message 文本**（`incremental_update(user_message)`）。
- 管线：EventExtractor.extract_from_text → Normalizer → Validator → EventHistoryMatcher →
  GrowthEvaluator.evaluate（可选 deep_resolve_meaning 语义理解）。
- 产出：GrowthRecord 列表 → 供 GovernancePolicy 决策。
- 即：**从即时消息直接推断 → 评估 → 自动决策 → 自动写入**，不经过 ExperienceJournal、
  不经过任何持久化审核队列。

### 2.3 是否可能影响 Runtime 主链

- 主链内部**不调用** orchestrator.process（runtime_core 中 18 处 orchestrator 引用均为
  状态引用/引擎引用，无 per-turn process 调用）。
- 但旧链有**两个真实激活面**：
  1. RuntimePipeline 的 per-request fallback（runtime_pipeline.py 追踪字段
     `orchestrator_pipeline_fallback`）：RuntimeCore.process 抛错时当轮即回退 orchestrator。
  2. pipeline 构造失败 / 未注入 runtime 时的直连路径（runtime_pipeline 追踪字段
     `orchestrator_direct` / `orchestrator_invoked_outside_pipeline`，代码注释标 DANGER）。
- QQ（astrbot 插件）与 HTTP 共用 /v1/chat/completions → 主链，不直达旧链。
- 结论：**主链健康时不激活；主链异常即自动回退到旧链，旧链立刻恢复自动成长**。
  这是一个「治理随故障路径退化」的结构性问题。

### 2.4 是否存在自动人格变化

**是，三层：**

| 层 | 写入 | 是否可逆 | 审核 |
|---|---|---|---|
| GrowthState 数值（P2/P2b） | data/growth_state.json 覆盖写 | 无快照 | 无 |
| growth_history（P2） | append + resolver 共享 | append-only | 无 |
| SelfModel store（P3） | policy auto_apply（context≥0.50 即自动） | 无快照 | 阈值替代审核 |

叠加效果：下一轮 `PersonalityResolver.resolve()` 读取被修改的 GrowthState/growth_history，
返回的人格向量即已变化，直接进入 prompt 人格上下文——**无需写 personality_state.json
即可改变羽依的实际表达人格**。

### 2.5 当前生产风险

- **风险等级：中高（结构性），当前暴露面：低（主链健康时旧链不激活）。**
- 风险点：
  1. 旧链默认「无网关、无人工审核、无审计落账」三层自动写入。
  2. trait 级 approval_required 队列是 orchestrator 进程内存态，重启即丢（提案悬空）。
  3. 回退是自动的：一次 RuntimeCore 异常 → 当轮消息即走旧链自动成长。
  4. 两套治理账本并存：P1（admin 审核账本）与 P2/P3（policy 自动账本）互不可见，
     审计时无法从单一入口还原「人格为什么变了」。

## 3. 治理统一方案设计

### 3.1 目标与权威域定义

目标：**MutationGateway 成为人格变化唯一入口。**

「人格变化」权威域（统一后全部纳入网关）：

- personality_state.json（P1 链）
- data/self_model/（P3/P4/P5 链）
- data/growth_state.json + growth_history（P2 链——影响 resolve 的数值源头）

不改动：emotion_state（P6，情绪域自有 B.7 网关）、relationship_state（P8，关系域自有 B.9
网关）——保持各域网关独立，统一只针对「人格权威域」。

### 3.2 方案：统一治理开关（默认关闭，灰度迁移）

新增 config：`governance_unification_enabled`（默认 **False**，缺省不写 config.yaml）。

**开启前（默认）**：所有旧路径字节不变（P2/P2b/P3 行为与现状逐行一致）——满足「历史兼容」。

**开启后（Phase B 生效）**：

1. **P2/P2b（incremental_update 旧直写分支）**：
   `growth_engine.apply_proposal/apply(event)` 直写分支强制替换为
   「生成 GrowthProposal（status=pending）→ MutationRequest → MutationGateway」；
   ACCEPT 才经既有 Apply Adapter 执行；NEED_REVIEW/DEFER 入治理存储；REJECT 留痕不写。
   （B.5 网关已实现该路由逻辑 `_route_proposal_through_gateway`——统一开关即把
   `is_growth_mutation_gateway_enabled()` 短路为该开关，**复用已有实现，不迁移代码**。）
2. **P3（Step 14.6 auto_apply 分支）**：`SelfModelGovernancePolicy` 的 auto_apply 阈值分支
   强制降级为 `approval_required`——proposal 落入**持久化**治理存储（不再内存队列），
   由 admin 审核 + P1 drain 链消费。**默认关闭自动 apply。**
3. **P4/P5（主链 resolver/stage13 侧写）**：本阶段**不改动**（保持 RuntimePipeline 零影响）；
   resolver 派生属只读，`self_model_store.update(should_update)` 与 stage13 save_state
   列为 Phase D 的收口项（设计要点：改为「变更集 → 网关 → save」，或保留现状并在审计中
   标注为「经网关语义豁免的缓存型写入」——待 Phase D 决策，本阶段不实现）。
4. **P1（approved drain）**：**保持不动**。approved 即人工审核产物，与「网关唯一入口」语义
   相容（审核锚点 admin）；可选增强（默认不做）：drain apply 前叠加网关 audit 记录。
5. **P9/P10**：不动（人工通道 / 未启用）。

### 3.3 约束满足检查

| 要求 | 满足方式 |
|---|---|
| 保留历史兼容 | 开关默认 False → 旧行为字节不变 |
| 不破坏 RuntimePipeline | 主链无 P2/P3 路径；P4/P5 本阶段零改动；开关只影响 legacy/fallback 路径 |
| 不影响 approved drain | P1 链不触碰；approved 提案仍由 drain 消费 |
| 默认关闭自动 apply | 开关开启后 P3 auto_apply 降级为 approval_required；P2 直写改网关路由 |
| flag 灰度迁移 | 四阶段（见 §4），每阶段独立可回滚 |

## 4. 迁移阶段设计

### Phase A — 新增治理开关 + 审计探针（只观察）

- 新增 `governance_unification_enabled`（默认 False）。
- 新增**只读审计探针**：旧链每次 P2/P2b/P3 写入时记录结构化日志/计数器
  （路径、dimension、delta、policy decision），不改任何行为。
- 交付物：探针数据基线（证明「默认关闭=零行为差异」）。

### Phase B — Legacy path 改为生成 Proposal（开关开启时）

- 开启开关后：P2/P2b → 生成 pending proposal + 网关路由（复用 B.5 实现）；
  P3 auto_apply → approval_required + 持久化治理存储。
- 默认仍关闭；开启仅限灰度环境。
- 交付物：行为对照（开关 on/off 的提案数、写入数差异报告）。

### Phase C — A/B 验证

- 复用 B.15/P2.5 A/B 方法论（临时插件、同电池、四指标）：
  1. 测试失败集合（新增=0 / 消失=0）
  2. Proposal 数量（A=旧直写 0 提案；B=提案进入网关，0 直写）
  3. Mutation 调用（B 态网关调用数 > 0 且全部有审计记录；A 态无）
  4. Personality 变化（B 态未经 ACCEPT 无变化；A 态维持旧行为）
  5. data 修改（B 态 growth_state.json / self_model 在未 ACCEPT 时零变化）
- 含**回退演练**：开关开→主链故障→fallback 旧链，验证 B 态下 fallback 不再自动成长。

### Phase D — 旧 auto_apply 删除

- 前置条件：Phase C 通过 + 灰度观察期无回归。
- 删除 P2/P2b 的 legacy 直写分支与 P3 的 auto_apply 阈值分支；
  MutationGateway 成为人格权威域唯一变更入口；P4/P5 收口（§3.2-3 待决项）落定。
- 保留 `governance_unification_enabled` 作为过渡开关直至确认删除后再移除。

## 5. 风险与注意事项

- 迁移期**双账本并存**是刻意为之（灰度需要），审计需同时对照两个账本。
- Phase B 开启后 legacy 路径提案量可能显著上升（历史每轮直写转为提案），
  治理存储容量与去重策略需在 Phase B 验证中观察。
- trait 级 proposal 从内存队列改持久化后，原「重启即丢」行为变化——这是修复而非回归，
  但需在 A/B 中显式验证。
- 任何阶段不得改动：STAGE_TO_METHOD_NAME 17 阶段契约、Phase 4.3 红线
  （stage 04 唯一入口 accept_experience）、P1 drain 的 approved 语义。
