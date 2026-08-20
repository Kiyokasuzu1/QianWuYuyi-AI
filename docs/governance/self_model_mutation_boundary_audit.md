# P2.3-B.12 Self Model Mutation Boundary Audit — 边界审计报告

> 记录时间：2026-08-20
> 性质：**全程只读审计**。本任务未修改 src/、data/、tests/ 任何文件，未接入新 adapter，未启用任何 governance flag。
> 用途：为 B.13 Self Model Mutation Migration 提供唯一事实依据。
> 前置依据：mutation_boundary_audit.md、mutation_gateway_design.md、mutation_gateway_implementation_report.md、p24b4/p25b5/p26b7/p27b9 迁移报告、p2_3_b11_governance_runtime_report.md

---

## Phase 0 — 基线

| 项目 | 值 |
| --- | --- |
| branch | `develop/v1.1` |
| HEAD | `225d0408e89b475605d62b0435f85a9954ffb024` |
| git status | 251 项工作区变更（B.5/B.7/B.9/B.10/B.11 未提交改动 + 既有 in-flight 变更；与 B.11 完成态一致，无 B.12 新增） |
| data/ 状态 | 零修改（`git status --short -- data/` 为空；data/governance/ 不存在） |
| Self Model 相关文件 | src/personality/ 下 self_model_*.py 共 **21 个** + self_observation.py（B.11）；src/runtime/self_model/ 目录 27 个文件；src/contracts/self_model_schema.py |
| Governance Runtime | B.11 完成态（Gateway + ProposalStore + ProposalManager + Inspector + MutationAdapterBase + GovernanceConfig） |
| mutation flags | growth=False / emotion=False / relationship=False / relationship_self_model=False / **personality=False**（src/personality/mutation_adapter.py:50） |
| governance_mode | `legacy` |

双权威架构（Phase 4.0.1 确立，本次审计确认仍然成立）：
- **Authority**：`src/personality/self_model_store.py`（SelfModelStore，dict 模型，持久化 data/self_model.json）
- **Compatibility Projection**：`src/personality/self_model_manager.py`（SelfModelManager，SelfIdentity 结构，无独立持久化，经 live-sync 从 Store 单向恢复）
- 另有 `src/runtime/self_model/persistence/self_model_store.py`（future snapshot infrastructure，非 Authority）。

---

## Phase 1 — Self Model State Registry（状态写入登记表）

写入入口编号 SM-01 ~ SM-22。**persistent** = 落盘 data/self_model.json 或等价；**user scoped** = 是否按用户隔离（SelfModelStore 是全局单例，**均不按用户隔离**）。

| ID | file:line : function | target object / field | write mechanism | caller（生产路径） | prod | persist | proposal | approval | audit | risk |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SM-01 | self_model_store.py:149 `update()` | _current_model 全模型重建（traits/narratives/understanding） | builder.build() 整体重建 + 落盘 | growth_integration.py:407（`_on_self_model_updated`）；personality_resolver.py:359（legacy 分支） | ✅ | ✅ | ❌ | ❌（resolver 侧有 B.4 开关，growth_integration 侧**无任何治理分支**） | ❌ | **高** |
| SM-02 | self_model_store.py:441 `apply_change_proposal()` | growth_narratives 追加 / self_understanding 指标 | 增量 change_type 分发 + 落盘 | self_model_updater.py:389（`_apply_to_store`）；orchestrator.py:1341-1385（Phase 4.0.3 硬契约链） | ✅ | ✅ | ✅ SelfModelChangeProposal | ⚠️ Phase 4.0.3 Policy（context/preference 级 AUTO_APPLY = **自动批准**） | ⚠️ 仅内存 `_applied_proposals` | 中 |
| SM-03 | self_model_store.py:174 `set_experience_context()` | _experience_context + model["experience_context"] | dict copy 注入（非人格字段） | orchestrator.py:296 | ✅ | 随 update 落盘 | ❌ | ❌ | ❌ | 低 |
| SM-04 | self_model_store.py:212 `set_growth_history_view()` | model["growth_history"] 视图 | deepcopy 视图注入 | growth_integration.py:383 | ✅ | 随 update 落盘 | ❌ | ❌ | ❌ | 低 |
| SM-05 | self_model_store.py:247 `set_personality_evolution_view()` | model["personality_evolution_history"] 视图 | deepcopy 视图注入 | PersonalityStateUpdater（演化管线） | ✅ | 随 update 落盘 | ❌ | ❌ | ❌ | 低 |
| SM-06 | self_model_store.py:314 `save()` | 全模型持久化 | 手动落盘触发 | **emotion_growth_service.py:94/97（legacy 直写）** | ✅ | ✅ | ❌ | ❌ | ❌ | **高** |
| SM-07 | self_model_store.py:413 `_ensure_base_model()` | 空模型自动初始化（identity_name="浅雾羽依" 等身份字段） | apply_change_proposal 触发自动建基 | SM-02 链（空 Store 时） | ✅ | ✅ | ❌ | ❌ | ❌ | 中 |
| SM-08 | self_model_manager.py:106 `refresh()` | SelfIdentity 全量刷新（traits/prefs/patterns/contradictions/understanding） | 聚合重建（projection） | runtime_core.py:2225 `refresh_self_model` / :2269 `refresh_self_model_from_runtime` | ✅ | ❌（内存） | ❌ | ❌ | 快照历史（内存 100 条） | 中 |
| SM-09 | self_model_manager.py:526-534 `_apply_growth_records()` 内 core_values 权重直改 | SelfIdentity.core_values[].weight（±delta，钳 0.3~1.0） | identity/milestone 级 GrowthRecord **直接修改价值观权重** | SM-08 链（growth_records 输入） | ✅ | ❌（projection） | ❌ | ❌（无 Phase 4.0.3 评估——GovernancePolicy identity 级= DENY，但此路径**不经过该 Policy**） | ❌ | **高（P0 红线）** |
| SM-10 | self_model_manager.py:665 `_apply_relationship_snapshot()` | SelfIdentity.preferences（relationship::bond/familiarity/trust） | 快照→偏好直写；**B.9 开关开启时改经 RelationshipMutationAdapter.route_self_model_influence（强制 NEED_REVIEW）** | runtime_core Stage9/Stage14 refresh 链 | ✅ | ❌ | flag on: ✅ | flag on: ✅ Gateway；flag off: ❌ | flag on: ✅ InMemory | 中（已治理但 flag 默认关） |
| SM-11 | self_model_manager.py:740 `_apply_relationship_state()` | SelfIdentity.preferences（relationship::attachment/familiarity/closeness） | legacy dict 路径直写——**B.9 未覆盖此分支**（只治理了 snapshot 路径） | refresh(relationship_state=...) 旧调用方 | ⚠️ 视调用方 | ❌ | ❌ | ❌ | ❌ | **中高（漏网）** |
| SM-12 | self_model_manager.py:171 `load_full()` | SelfIdentity 全量替换 | dict→dataclass 恢复 | 测试/迁移（docstring 声明） | ❌ | ❌ | ❌ | ❌ | ❌ | 低 |
| SM-13 | self_model_manager.py:223 `restore_from_persisted()` | growth/development_history、四理解指标、who_i_am 前置条目 | 幂等合并（rid 去重，fail-soft） | runtime_core.py:3588 `_restore_self_model_authority`（启动恢复）+ Store live-sync（:353 `_notify_live_sync`） | ✅ | ❌ | ❌ | ❌（恢复语义，非变更） | ❌ | 低 |
| SM-14 | emotion_growth_service.py:41 `analyze_and_merge()` | SelfModelV3.beliefs（EmotionBelief 合并）+ save | 模式分析→信念提取→merge→**store.save() 直写**；Phase 6.2 注入 adapter 时走 apply_external_change | EmotionManager 计数器自动触发（后台） | ✅ | ✅ | ❌ | ❌ | adapter 路径有 actor；legacy 无 | **高（B 类主债）** |
| SM-15 | runtime_core.py:2146 `accept_self_model_suggestion()` | 经 Updater.apply_suggestion 写 Manager + Store | ApprovalQueue.approve→apply；**但"不在队列中→直接标记已批准（兼容旧行为）"分支 = 伪审批旁路** | 外部显式调用（admin/测试） | ⚠️ | 视路径 | ✅ | ⚠️ **fallback 伪审批** | 部分 | **高（P0 伪审批）** |
| SM-16 | personality_resolver.py:349/555 `_propose_self_model_mutation()` | SelfModel 重建意图 → MutationRequest（target_domain="self_model"） | B.4 开关开启时经 PersonalityMutationAdapter→Gateway；**flag off 时走 SM-01 legacy update** | PersonalityResolver.update 主链 | ✅ | 视 verdict | flag on: ✅ | flag on: ✅ Gateway | flag on: ✅ | 中（已治理但 flag 默认关） |
| SM-17 | identity_anchor.py:525 `apply_change_proposal()` | IdentityAnchor.weight / constraints | 要求 proposal.approved=True（外部显式）；漂移钳制 ±WEIGHT_DRIFT_MAX_ALLOWED | 外部审批后 | ⚠️ | 锚点持久化 | ✅ AnchorChangeProposal | ✅（显式 approved 标志，**无鉴权**） | version+last_modified | 中 |
| SM-18 | runtime/self_model/persistence/{self_model_store.py:191 save, snapshot_manager.py:138 save_snapshot, evolution_history_store.py:145 append} | snapshot 基础设施（非 Authority） | JSONL/快照追加 | Phase 6.3 bootstrap 体系 | ⚠️ | ✅（data/self_model/） | ❌ | ❌ | change_log.py | 低 |
| SM-19 | runtime_core.py:3588 `_restore_self_model_authority()` | Manager 内存态（经 SM-13） | 启动期恢复 + live-sync 绑定 | RuntimeCore 启动 | ✅ | ❌ | ❌ | ❌（恢复） | ❌ | 低 |
| SM-20 | runtime_core.py:3624 `_init_self_model_bootstrap()` | SelfModelAdapter beliefs/history/reflections 启动注入 | SelfModelBootstrap.bootstrap(adapter) 批量加载 | RuntimeCore 启动（Phase 6.3 默认开启） | ✅ | ❌（内存+change_log） | ❌ | ❌（**启动直接注入，设计上绕过治理**） | change_log | 中 |
| SM-21 | self_model_adapter.py:684 `apply_external_change()` / :284 `apply_pcr()` | Manager + Store（"唯一写入口"，Phase 6.2 Authority Closure） | 统一写门面（带 actor 字段） | emotion_growth_service（adapter 注入时）、personality/growth/memory runtime adapters | ✅ | 视内部路径 | ⚠️ 无统一 Proposal | ❌ | ⚠️ change_log/audit 部分 | 中 |
| SM-22 | PersonalityEvolutionPipeline（runtime_core.py:608 `auto_apply_self_model=False`） | 演化→SelfModel 应用 | 配置开关（默认关） | 配置驱动 | ⚠️ 默认关 | 视配置 | ✅ | ⚠️ 配置级 | ✅ 演化历史 | 低 |

补充事实：
- RuntimeController（runtime_controller.py:495 `_apply_delta_safely`）**不直写 SelfModel**——只写 PersonalityState trait delta；flag on 经 B.4 Gateway，flag off 伪造 `p_rt_*/a_rt_*`（该伪审批已由 B.4 build_request 防线拒绝进入治理链）。对 SelfModel 的影响是间接的（PersonalityState → resolver → SM-01）。
- Admin API（admin/api/routes.py:3170-3260）selfmodel 端点全部只读（status/identity/beliefs/belief-why）；admin 侧存在 `runtime_selfmodel_bridge_test.py` 的 ApprovalGate（fail-closed：缺 approval_status → 拒绝）——这是消费侧闸门，非写入口。
- orchestrator.py:281 附近 `refresh_self_model_from_runtime(snapshot=...)` 的 RelationshipSnapshot **不进 ctx**（B.9 报告确认），Stage9/14 经 SM-08→SM-10。

---

## Phase 2 — 十个重点方向扫描结果

1. **SelfModelStore.apply_change_proposal()**：SM-02。调用方唯一合法链 = orchestrator Phase 4.0.3（Policy evaluate → AUTO_APPLY/ApprovalQueue）；Policy 为 Phase 4.0.3 自有规则，**非 B.3 MutationGateway 五道检查**。
2. **SelfModelStore.update()**：SM-01，两个生产调用方（growth_integration **无治理分支**；personality_resolver 有 B.4 分支但 flag 默认 off）。
3. **SelfModelManager 写入口**：SM-08/09/10/11/12/13（refresh 聚合族）。
4. **PersonalityResolver 对 SelfModel**：SM-16（B.4 已建治理路径，默认关）+ resolver.py:375 起 SelfModelAdapter.apply_external_change("personality")。
5. **Growth → SelfModel**：SM-01（growth_integration.py:407 直写）+ orchestrator Step 14.6 治理链（SM-02）+ growth_self_model_adapter.py（runtime 侧，经 SM-21 门面）。
6. **Emotion → SelfModel**：SM-14（analyze_and_merge，计数器自动触发，legacy 直写 save）+ emotion_self_model_bridge.py（merge 到 SelfModelV3 内存对象）。**无 B.7 开关覆盖**——B.7 只治理 EmotionManager.process_event 的情绪状态 delta，不治理情绪信念进 SelfModel。
7. **Relationship → SelfModel**：SM-10（snapshot 路径，B.9 已治理，flag off）+ SM-11（state dict 路径，**未治理**）。
8. **RuntimeCore → SelfModel**：SM-15（伪审批 fallback）/ SM-19 / SM-20 / refresh 族 SM-08 / pending suggestions 队列（runtime_core.py:2047-2092，PCR 来源）。
9. **Admin / Governance API → SelfModel**：HTTP 端点全只读；`accept_self_model_suggestion`（SM-15）是程序化审批入口。
10. **startup/bootstrap 直接注入**：SM-19（authority 恢复）、SM-20（Phase 6.3 bootstrap 默认开启，beliefs/history/reflections 批量注入，设计上绕过治理）。

---

## Phase 3 — 治理链 A/B/C 分类

### A 类（经 Proposal/Approval/治理链；注：**当前没有任何 self_model 写路径经过 B.3 MutationGateway 生产运行**——所有治理 flag 关闭，A 类全部处于休眠或仅 Phase 4.0.3 旧治理）

| 路径 | 治理形态 | 状态 |
| --- | --- | --- |
| orchestrator Step 14.6：GrowthRecord → GovernancePolicy → Proposal → AUTO_APPLY/ApprovalQueue → apply | Phase 4.0.3 契约（非 Gateway 五道） | **生产运行中**（唯一活跃治理链） |
| SM-16 resolver 重建意图 → PersonalityMutationAdapter → Gateway | B.3 Gateway（五道） | 休眠（personality flag=False） |
| SM-10 snapshot → relationship adapter → Gateway（强制 NEED_REVIEW） | B.3 Gateway | 休眠（relationship_self_model flag=False） |
| SM-17 锚点提案（approved=True + 漂移钳制） | 显式标志审批（无鉴权） | 半活跃 |

### B 类（直接写 / 伪审批 / bypass / 无证据 / 无审计）——B.13 的迁移目标

| # | 路径 | 违反点 | 严重度 |
| --- | --- | --- | --- |
| B-1 | SM-01 growth_integration.py:407 `store.update()` | Growth→SelfModel 全模型重建，无 proposal/无 approval/无 audit，**且无任何治理开关分支**（B.4 只覆盖 resolver 侧） | P0 |
| B-2 | SM-14 emotion_growth_service.analyze_and_merge → store.save() | 后台自动触发的信念合并直写；无 Gateway、无 Proposal（adapter 路径有 actor 但无审批） | P0 |
| B-3 | SM-15 accept_self_model_suggestion 伪审批 fallback | "不在 ApprovalQueue → 直接标记已批准"绕过审批（Phase 4.0.3-C 残留兼容） | P0 |
| B-4 | SM-09 core_values.weight 直改 | identity/milestone GrowthRecord 直接修改核心价值观权重；Phase 4.0.3 Policy 对 identity 级=DENY 的意图被此路径架空（该路径不经过 Policy） | P0（红线） |
| B-5 | SM-11 `_apply_relationship_state()` legacy dict 路径 | B.9 治理只覆盖 snapshot 参数分支，relationship_state dict 分支仍直写 preferences | P1 |
| B-6 | SM-02 的 AUTO_APPLY 语义 | context≥0.5 / preference≥0.65 自动应用=自动批准，无人工复核（对比 B.11 生命周期：approve 必须显式 reviewer） | P1 |
| B-7 | SM-20 bootstrap 启动注入 | 启动批量注入 beliefs/history，设计上绕过治理（恢复语义 vs 注入语义边界模糊） | P2 |
| B-8 | SM-06 store.save() 公开手动落盘 | 任何持有 store 引用的模块可绕过所有增量接口直接持久化 | P2 |
| B-9 | SM-07 _ensure_base_model | 空 Store 时静默注入身份基线（常量，但未经治理） | P2 |
| B-10 | SM-02/SM-08 全部路径审计仅内存（_applied_proposals / _snapshots） | 无持久化 audit trail（MutationAuditRecord/B.10 ProposalStore 未接入） | P1 |

### C 类（死代码 / legacy / 测试专用）

- self_model_updater.py:396 `_apply_to_store_legacy()`（旧 Store 兼容回退，注释声明未来可移除）
- SM-12 `load_full()`（测试/迁移）
- self_model_builder.py（DEPRECATED，经 BuilderAdapter 桥接保留兼容）
- SelfModelManager 整体定位为 compatibility projection（Phase 4.0.1 声明，未来可能合并进 Store 体系）
- runtime/self_model/persistence/ 快照体系（future snapshot infrastructure，非 Authority，旁路存在）

### B.4/B.9 遗留路径复核（任务书点名确认）

| 遗留 | 现状 |
| --- | --- |
| relationship → self_model | snapshot 路径已治理（B.9，flag off 休眠）；**SM-11 dict 路径漏网仍在**（B-5） |
| personality resolver → self_model | B.4 分支已建（flag off 休眠）；**growth_integration 侧同源 update() 无分支**（B-1） |
| runtime controller → self_model | 不直写 SelfModel（只写 PersonalityState）；伪审批 p_rt_*/a_rt_* 已被 B.4 防线拦截，**无新增绕过** |
| growth → self_model | orchestrator 主链有 Phase 4.0.3 治理；growth_integration 事件回调路径直写（B-1）+ 情绪信念路径完全无治理（B-2） |

---

## Phase 4 — Self Model 红线与 Mutation Classification

### 红线区域分析

**Identity（禁止自动修改）**
- `identity_name`（"浅雾羽依"）/ `identity_summary`：SM-07 硬编码常量注入（内容安全，机制越权）；BoundaryCheck FORBIDDEN_IDENTITY_PATH_PREFIXES 已覆盖 identity.core./origin./manifesto. 前缀硬拒。
- **core_values 权重**：SM-09 可被 identity/milestone GR 直改——**这是本次审计发现的最大红线违规**（价值观权重属于身份锚点邻域，AGENTS.md Priority 1）。
- IdentityAnchor（identity_anchor.py）：SM-17 有显式 approved + 漂移上限，但审批无鉴权（任何人可设 approved=True）。
- creator/origin/manifesto：无任何写入口（只读于 identity_core），安全。

**Stable Personality（需长期证据）**
- stable_traits：SM-08 从 TraitState 聚合（TraitState 自身变更另有演化管线治理）；SelfModel 侧为投影消费，风险中等。
- Phase 4.0.3 Policy 对 trait 级要求 confidence≥0.80 + APPROVAL_REQUIRED——语义正确但仅覆盖 SM-02 链。

**Dynamic Self Knowledge（允许渐进更新）**
- preferences（growth 来源）、behavioral_patterns、growth_narratives、self_understanding 指标（+0.05 上限）：渐进、有界，适合 AUTO。

**Beliefs（需 evidence/confidence/provenance/矛盾处理）**
- SelfBelief / EmotionBelief：SM-14 情绪信念合并**无 evidence 门槛、无 provenance 强制、无矛盾处理**（bridge.merge 语义未审计到矛盾检查）；contracts 的 SelfContradiction 结构只覆盖 trait 矛盾。

### Self Model Mutation Classification（B.13 冻结建议）

| 分类 | target_path | 理由 |
| --- | --- | --- |
| **REJECT**（永远禁止） | identity.core.* / identity.origin. / manifesto.*（已有 BoundaryCheck 硬拒）；core_values 的**新增/删除**；identity_name/identity_summary 写入（SM-7 场景改为治理化 bootstrap 常量注入白名单）；锚点新增/删除 | 身份连续性 Priority 1；任何自动路径不得触碰 |
| **REVIEW**（必须复核） | core_values.weight 变更（≥3 独立证据 + identity/milestone 级提案）；stable_traits.*（trait 级，conf≥0.80）；relationship→self_model 跨域全部（B.9 既有语义：强制 NEED_REVIEW）；emotion 信念新增（beliefs.*，conf<0.65 或无 provenance）；IdentityAnchor.weight（保留 SM-17 钳制 + 加鉴权）；self_model.update 全模型重建（SM-01 场景） | 长期证据要求 / 跨域 / 情绪状态污染风险 |
| **AUTO**（证据门槛内自动） | growth_narratives 追加（context 级，conf≥0.5，维持 Phase 4.0.3 语义）；self_understanding 指标（≤0.05 有界增量）；preferences（preference 级，conf≥0.65，限 growth 域来源）；experience_context / growth_history 视图 / evolution 视图（非人格字段）；behavioral_patterns 频次累积（有既有上限） | 渐进、有界、可逆、非身份邻域 |

---

## Phase 5 — SelfObservation 对接分析（只设计）

现状：SelfObservation（B.11）→ `to_proposal_seed()` 只产出纯 dict 种子（source_observation_id/domain/evidence_refs/confidence/observed_at/seed_version），无消费者。

目标链（任务书冻结）：`SelfObservation → GrowthProposal → MutationRequest → Governance Review → SelfModel Apply`。

接入判定设计（按 observation.domain + 目标字段）：

| observation 特征 | 判定 |
| --- | --- |
| domain ∈ {growth, self_model} 且 evidence_refs ≥ 2 且 confidence ≥ 0.65 | 可进入 Growth 提案管线（转换位置：Phase 6 蓝图第 7 项），按目标字段走 AUTO/REVIEW |
| 目标为 core_values / stable_traits / 跨域（relationship→self_model 等） | 必须 REVIEW（进 B.11 ProposalManager PENDING_REVIEW，人工 reviewer） |
| domain ∈ {memory} 或 evidence_refs < 1 或 confidence < 0.5 | **永远不能改变 SelfModel**（observation 只留存，不转换） |
| 任何指向 identity 锚点邻域的观察（who I am 类文本→core_values 映射） | REJECT（转换器必须在构建阶段丢弃，理由记入 audit） |

---

## Phase 6 — B.13 实施蓝图（只设计；默认 flag=False）

1. **SelfModelMutationAdapter**（新增 `src/personality/self_model_mutation_adapter.py`，复刻 B.9 模式）：build_request 域前缀守卫（self_model./selfmodel.）；route() ACCEPT→apply_route（指向 SelfModelApplyAdapter）/REJECT→丢弃/NEED_REVIEW→Gateway save_pending_proposal（B.10 自动落账）+ B.11 ProposalManager.register/DEFER→延迟槽位；LEGACY_MUTATION_SOURCES 登记 B-1/B-2/B-4/B-5。
2. **SelfModelApplyAdapter**（新增）：唯一域状态执行件，包装 store.update/apply_change_proposal/偏好写入；强制 Phase 4 分类表；mark_applied 回告 ProposalManager。
3. **self_model mutation flag**：`_self_model_mutation_gateway_enabled = False`（默认关，照抄 B.9 双开关模式；growth_integration 与 emotion_growth_service 各挂一个迁移分支）。
4. **Governance 接入点**：SM-01（growth_integration:407 + resolver:359 legacy 分支）、SM-14（analyze_and_merge）、SM-15（伪审批 fallback 收敛为：不在队列→NEED_REVIEW 入 ProposalManager，不再直接批准）。
5. **Audit 接入点**：SM-02/SM-08 的内存审计接 MutationAuditRecord（B.10）+ GovernanceProposalStore 落账；emotion legacy save 收编进 adapter route。
6. **Identity hard boundary**：core_values.weight 直改（SM-09）改经 REVIEW 提案；_ensure_base_model 常量注入白名单化；IdentityAnchor.approved 加 reviewer 鉴权（对齐 B.11 approve(reviewer=...) 形状）。
7. **Observation → Proposal 转换位置**：新增 `src/personality/self_observation_to_proposal.py`（纯函数转换器，Phase 5 判定表），输出 GrowthProposal 种子进 growth 管线——不改 SelfObservation 本体。
8. **rollback**：与 B.4/B.7/B.9 同构——flag 关闭即回 legacy 字节级行为；新增文件删除即净；ProposalStore/lifecycle 均为治理目录专属文件。

实施顺序建议：① P0 伪审批收敛（SM-15）→ ② B-4 core_values 红线（SM-09 经 REVIEW）→ ③ B-1 growth_integration 分支 → ④ B-2 emotion 收编 → ⑤ B-5 补漏 → ⑥ audit 落账 → ⑦ Observation 转换器。每步 A/B 回归（B.10/B.11 方法论）。

---

## Phase 7 — 最终验证

- `git rev-parse HEAD` = `225d0408e89b475605d62b0435f85a9954ffb024`（不变）
- `git diff HEAD --name-only -- src/ tests/ data/` = 14 个既有文件（B.12 开始前已采集，与本审计开始时**完全一致**，全部为 B.4-B.11 及更早的未提交改动；B.12 零新增）
- `git status --short -- data/` 为空；tests/ 无新增无修改
- 本审计唯一文件系统产出：本文档

## 审计结论摘要

1. **Self Model 写入口数量**：22 个登记项（SM-01~SM-22），其中生产活跃持久化写入 8 条链（SM-01/02/04/05/06/07/14/18），内存投影写入 6 条（SM-08/09/10/11/13/15）。
2. **A/B/C 分类**：A 类 4 条（仅 1 条活跃 = Phase 4.0.3 旧治理，3 条 Gateway 治理休眠）；B 类 10 项债务（P0×4：growth_integration 直写 / emotion 直写 / 伪审批 fallback / core_values 红线违规）；C 类 5 处。
3. **P0 红线**：core_values.weight 可被 identity/milestone GrowthRecord 直接修改（SM-09），架空了 Phase 4.0.3 identity=DENY 的意图。
4. **B 类债务**：见 Phase 3 B-1~B-10。
5. **B.13 实施顺序**：伪审批收敛 → core_values 红线 → growth_integration → emotion → relationship 补漏 → audit 落账 → Observation 转换器。
6. **SelfObservation 后续接入**：Phase 5 判定表（三档：可进 Growth / 必须 REVIEW / 永不改变 SelfModel）。
