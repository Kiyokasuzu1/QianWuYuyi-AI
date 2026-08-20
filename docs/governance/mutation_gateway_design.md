# Mutation Gateway 设计（mutation_gateway_design）

> 状态：**只读设计文档——未修改任何生产代码，未接线，未创建新的 mutation 写入口**
> 日期：2026-08-19　分支：develop/v1.1　HEAD：225d040
> 依据：P2.3-B.1 `docs/governance/mutation_boundary_audit.md`（40 条活跃写入口 + B1-B14 债务清单）
> 上游契约：`docs/governance/design_committee_future.md`（委员会接口预留）、`docs/architecture/runtime_context_contract.md`（§3.6/§3.7 变更与审计契约）
> 约束：AGENTS.md Priority 3（人格变化必须 事件→理解→评估→GrowthProposal→审核→应用）、Rule 3（模块禁止越权写他人状态）、Rule 4（新增模块四问）

---

## 0. 结论摘要

1. **Gateway 的定位是"决策层"，不是"写入口"**。它接收 `MutationRequest`，经过五道检查后输出 `MutationDecision`（ACCEPT / REJECT / NEED_REVIEW / DEFER）；**Apply 一律交给现有执行组件**（EvolutionPipeline / TraitStateUpdater / SelfModelUpdaterAdapter / 各领域 adapter）。Gateway 自身不持有任何 repo/store 写句柄，因此不构成新的 mutation 写入口。
2. **"MutationRequest" 尚无类实现**：全仓库无 `MutationRequest` 类；最接近的实体是 `MutationRecord`（src/runtime/request_context.py:329，form="mutation_request"），它只有 8 个字段且仅作为 journal 记录形态存在。本设计将其扩展为 Gateway 的 9 字段输入契约，并保持 `MutationRecord` 作为落账载体不变。
3. **现有组件已覆盖"批准→执行→应用"三层**：ApprovalManager + ApprovalPolicy（治理决策）、EvolutionPipeline + TraitStateUpdater（人格执行）、SelfModelUpdaterAdapter + SelfModelApprovalQueue（SelfModel 应用）全部在生产路径上运行。Gateway 不需要重造这三层，只需要在其**前方**补一道统一决策闸门，并在其**后方**把 MutationJournal / AuditTrail 接上线（当前零写入，B11）。
4. **审计载体已存在但零写入**：`AuditTrail` / `AuditLink` / `MutationJournal` 在 RuntimeContext v2 中字段完整（含 request_id/trace_id 关联键），但全仓库无任何生产写入点。Gateway 的 Audit Check 是补上这段"最后一厘米"的挂接面。
5. **迁移分四级**：P0 堵住 personality/self_model 直写红线（B2/B3/B5/B6/B12）→ P1 合并 growth 双轨与双 Proposal 存储（B1/B4）→ P2 补齐 emotion/relationship/memory 审计并默认开启（B7/B8/B9/B10）→ P3 MutationJournal 全接线 + 死入口清理（B11/B13/B14）。

---

## 1. 设计基线（Phase 0）

| 项 | 值 |
|---|---|
| 分支 / HEAD | develop/v1.1 / 225d040 |
| 工作区基线 | 与 B.1 审计结束态一致（`git status --porcelain` 快照比对通过） |
| 上游输入 1 | `docs/governance/mutation_boundary_audit.md`（40 条活跃写入口、6 域 Mutation Registry、B1-B14） |
| 上游输入 2 | `docs/governance/design_committee_future.md`（ReviewRequest/CommitteeDecision 接口预留；唯一变更链 Proposal→Review→Approval→Apply→Audit） |
| 上游输入 3 | `docs/architecture/runtime_context_contract.md`（§3.6 三种变更形式 mutation_request/event/proposal；§3.7 审计链 request→event→proposal→apply→audit；Apply 四前置条件） |

本设计冻结以下"不变量"（与 B.1 §5 四条迁移不变式一致）：

- I-1：人格/自我模型写入口**只收不增**——Gateway 不得成为第 N+1 个写入口；
- I-2：现有 Approval / Proposal / Audit 组件**签名与语义不变**——Gateway 以调用方身份复用，不要求它们改接口；
- I-3：六域直写禁令（B.1 §3 各域表"禁止"列）在 Gateway 实施前**依然有效**——Gateway 是收口手段，不是直写的豁免证明；
- I-4：RuntimeContext v2 契约字段（MUTATION_TARGETS / MUTATION_FORMS / MutationRecord / MutationJournal / AuditTrail）**作为 Gateway 的输入输出载体**，不新增第四种变更形式。

---

## 2. 现有治理组件能力地图（Phase 1）

| # | 组件 | 位置 | 输入 | 输出 | 当前调用者 | 生产使用 | 可复用性 |
|---|---|---|---|---|---|---|---|
| 1 | MutationRequest | **无类实现**。仅 `MUTATION_FORMS` 常量含 `"mutation_request"` 字符串（request_context.py:82） | — | — | — | 否 | 需新建（Gateway 输入契约本体；Rule 4 四问见 §7） |
| 2 | MutationRecord / MutationJournal | src/runtime/request_context.py:329 / 375 | MutationRecord（mutation_id/target/form/action/payload/evidence_refs/requester_identity/created_at，target∈六域、form∈三形式校验） | 不可变追加 journal（`add()` 返回新实例） | **零生产调用方**（B11：ctx.mutations 从不写入） | 否（契约载体已就绪） | **高**——直接作为 Gateway 落账载体，扩展字段不破坏 from_dict/to_dict |
| 3 | ProposalStore | src/growth/proposal_store.py:75 | GrowthProposal（to_dict 后追加） | JSONL append-only + 内存索引；save/update/load/list/count/exists_similar（source_event_id+fingerprint 去重） | ProposalManager(:75)、growth_runtime_adapter.py:287、feedback_engine.py:93 | 是（data/proposals/proposals.jsonl） | **高**——持久化 + 去重能力完整；注意 VALID_STATUSES 不含 schema 默认 `"proposed"`（save 时被改写为 pending，词表需统一，见 §5 P1） |
| 4 | ProposalManager | src/growth/proposal_manager.py:52 | source_event + proposed_changes + confidence + evidence_ids + evaluator_meta | create（低置信度拒绝/去重/auto_accept 默认关）、accept、reject、apply（要求 accepted）、mark_needs_review、update_proposal_status_and_meta（from_states 保护，只改 status+meta 不触发 apply） | growth_integration.py:96（GrowthIntegrationService 持有） | 是（Stage 4 链） | 部分——create/查询/状态迁移可复用；**accept/apply 旧链是 B1 双轨之一**，Gateway 后应只保留治理用途 |
| 5 | ApprovalManager / ApprovalDecisionStore | src/approval/approval_manager.py:84 / 51 | proposal_id（内部经 get_proposal 取 GrowthProposal） | govern_proposal → {governed, decision_id, decision(approved/rejected/deferred), proposal_status, reasons, isolated}；决策存内存 store（**重启丢失**） | **唯一生产调用方**：growth_integration.py:829 | 是（Stage 4 治理链） | **高**——Gateway 的 NEED_REVIEW/DEFER 直接映射到它的 deferred + status_hint=under_review；决策落盘缺口属 P2（G1） |
| 6 | ApprovalPolicy | src/approval/approval_policy.py:81 | ProposalSnapshot（只读） | PolicyRecommendation（decision + reasons + decision_confidence + status_hint） | ApprovalManager（唯一） | 是 | **高**——纯函数三层保护（Identity Anchor 硬拒 identity.core./identity.origin./manifesto. → Evidence 缺证 deferred → Conflict 倒转 deferred+under_review）就是 Gateway 五检查中三道的现成实现 |
| 7 | EvolutionPipeline | src/personality/evolution_pipeline.py:53 | proposal + approval_decision | execute → {executed, evolution_record_id, applied_traits, blocked_identity, identity_continuity_ok}；EP-1 双 approved 门槛 / EP-2 幂等 / EP-3 proposal_id+approval_id / EP-4 identity 硬拒 | growth_integration.py:881（唯一） | 是（Stage 4 应用链） | **高**——Gateway ACCEPT(personality) 后唯一合法 Apply 执行器，零改动复用 |
| 8 | TraitStateUpdater | src/personality/trait_state_updater.py:20 | EvolutionRecord + trait_states | apply → 更新后 trait_states（approved 校验 / \|Δ\|≤0.15 钳制 / record_id 去重 / 历史 100 上限） | personality_adapter.py:588、personality_evolution_pipeline.py:146 | 是 | **高**——纯函数，Gateway 不替代它 |
| 9 | SelfModelUpdaterAdapter / SelfModelApprovalQueue | src/runtime/self_model/self_model_updater_adapter.py:147；src/personality/self_model_governance.py:185 | SelfModelChangeProposal（requires_approval 闸：未过 ApprovalQueue.approve() 拒绝写入） | 经 Updater.apply_proposal → SelfModelStore（GovernancePolicy.evaluate 内嵌） | runtime_core.py:3379 | 是 | **高**——SelfModel 域 Apply 的唯一合法闸；但 B12 的 manager 旁路写（:374-475）需 P0 收口 |
| 10 | AuditTrail / AuditLink / record_audit_log | src/runtime/request_context.py:410-469；src/audit/record.py:48 | chain/audit_refs/request_id/trace_id 关联键 | 审计链节点 {step, at, event_ref, mutation_ref, audit_ref} | **AuditTrail 零写入**；record_audit_log 由 memory 主链等调用（但无 trace_id） | 载体未用 / 旧审计在用 | **高**——Gateway Audit Check 的落账目标；trace_id 关联是契约 §3.7 已承诺的扩展 |

**能力地图三结论**：

- **C1（缺入口，不缺机器）**：唯一的空白是"统一决策入口"（MutationRequest→MutationDecision）；批准、执行、应用、持久化、审计载体全部已有。因此 Gateway 是**最小新增**：一个纯决策函数 + 一个请求/决策数据契约，无新存储、无新总线、无新执行器。
- **C2（两个词表冲突）**：proposal status 存在两套词表——schema 默认 `"proposed"` vs ProposalStore 白名单 `{pending, accepted, rejected, applied, approved}` vs 治理层 `{approved, rejected, deferred, under_review}`。Gateway 的 MutationDecision 四态必须**只映射到治理层词表**，并冻结一张对照表（§3.3），禁止 Gateway 再引入第三套词。
- **C3（审计载体与决策载体割裂）**：ApprovalDecisionStore 是内存态（重启丢）、AuditTrail 零写入、MutationJournal 零写入。Gateway 设计把三者串起来：每笔决策同时 ① 写 ApprovalDecisionStore（现状）② 追加 MutationJournal.add（新）③ 追加 AuditTrail chain（新）——后两者是 v2 契约已声明的字段，接入不是新增数据结构。

---

## 3. Mutation Gateway 契约（Phase 2）

### 3.1 定位与边界

```
调用方（event handler / 治理链 / 委员会未来）
    ↓ MutationRequest
MutationGateway.evaluate(request)          ← 唯一新增实体：纯决策函数
    ├─ 1. Identity Check    → 权限门（can_modify_*，五扇门语义）
    ├─ 2. Boundary Check    → 路径合法性（六域命名空间 + 身份锚点前缀）
    ├─ 3. Evidence Check    → 证据充分性（复用 ApprovalPolicy 阈值）
    ├─ 4. Conflict Check    → 冲突与重复（复用 ApprovalPolicy 冲突层 + exists_similar）
    └─ 5. Audit Check       → 链路完整性（mutation_id/trace 关联 + journal/audit 追加）
    ↓ MutationDecision
调用方按 decision.prescription 分派：
    ACCEPT      → 现有执行组件（EvolutionPipeline / SelfModelUpdaterAdapter / 各域 adapter）
    NEED_REVIEW → ProposalManager.mark_needs_review / 治理队列（RelationshipProposalStore PENDING_REVIEW 等）
    DEFER       → ProposalManager.update_proposal_status_and_meta(deferred)
    REJECT      → 落账 + 终止
```

硬边界：

- Gateway **只读**六域状态快照（`context_snapshot` / `StateSnapshots`），**绝不持有**任何 store/repo/manager 写句柄；
- Gateway **不执行** Apply——`MutationDecision.prescription` 只给出现有执行器的调用指引；
- Gateway **不新增**事件/总线——source_event 是输入，不是 Gateway 的发布物；
- Gateway **兼容**现有组件：Identity/Boundary/Evidence/Conflict 四道内部优先复用 `resolve_identity`、`can_modify_*`、`ApprovalPolicy`、`ProposalStore.exists_similar`，不重写它们的规则。

### 3.2 输入：MutationRequest

| 字段 | 类型 | 语义 | 校验 |
|---|---|---|---|
| `mutation_id` | str | 变更请求唯一 ID（发起方生成，建议 `mut_{uuid12}` 同 MutationRecord 风格） | 非空；全程唯一；成为 MutationRecord.mutation_id 与 AuditLink.mutation_ref |
| `source_event` | Dict | 触发事件引用（event_id/type/summary/timestamp；事件语义对齐 GrowthEvent） | 非空 dict；`source_event.event_id` 非空（对齐 GrowthProposal.source_event_id） |
| `actor_identity` | str | 发起方身份（resolve_identity 输出：user / sandbox / admin；或系统组件名） | 非空；Identity Check 输入 |
| `target_domain` | str | 六域枚举：memory / emotion / relationship / growth / personality / self_model | 必须 ∈ MUTATION_TARGETS |
| `target_path` | str | 修改目标路径（如 `personality.traits.warmth` / `relationship.trust` / `memory.importance`） | 非空；Boundary Check 输入 |
| `proposed_change` | Dict | 拟议变更 {before, after, delta, reason, form}；form ∈ MUTATION_FORMS | before/after 可 JSON 化；Boundary/Conflict 输入 |
| `evidence` | List[Dict] | 证据列表，每项 {type, ref, summary, source_time}（对齐 design_committee_future §2.1） | Evidence Check 输入；允许为空但会触发 DEFER/REJECT |
| `context_snapshot` | Dict | 决策时点的只读状态快照（对齐 v2 `snapshot()` / StateSnapshots 五键） | 允许省略 → 从 RuntimeContext v2 参数注入 |
| `risk_level` | str | 发起方自评风险：low / medium / high（非决策依据，仅供排序与复核队列优先级） | 缺失默认 medium |

与 `MutationRecord` 的关系：**Gateway 不替换 MutationRecord**。ACCEPT/NEED_REVIEW/DEFER 决策落账时，Gateway 把 request 投影为一条 `MutationRecord(form=proposed_change.form, target=target_domain, action=proposed_change.action, payload=proposed_change, evidence_refs=evidence 的 ref 列表, requester_identity=actor_identity)` 追加进 MutationJournal——即 v2 契约 §3.6 的 `mutations` journal 是 Gateway 的输出落点之一，Gateway 输入是它的超集。

### 3.3 输出：MutationDecision

| 字段 | 值 |
|---|---|
| `verdict` | 枚举四态：`ACCEPT` / `REJECT` / `NEED_REVIEW` / `DEFER` |
| `decision_id` | 决策唯一 ID（对齐 ApprovalDecision.id 风格） |
| `mutation_id` | 回填输入 mutation_id（关联键） |
| `reasons` | 通过/未通过各检查的理由标签列表（复用 DecisionReasonTag 词汇） |
| `checks` | 五道检查逐项结果 {identity, boundary, evidence, conflict, audit} → pass/fail + 详情 |
| `prescription` | ACCEPT 时：{executor, apply_args}（executor 只能指向 §2 表格中已存在的执行组件）；NEED_REVIEW 时：{queue, review_reason}；DEFER 时：{resume_condition} |
| `risk_level` | 回填 + 复核后等级 |
| `audit_ref` | 本次决策写入的审计引用（AuditLink 序列） |

**四态语义与现有词表对照（冻结表，Gateway 不得再引入新词）**：

| Gateway verdict | 含义 | 映射到现有 proposal status | 触发条件（概述） |
|---|---|---|---|
| ACCEPT | 全部检查通过，允许执行 | `approved`（ApprovalManager 决策词） | 五道检查全 pass |
| REJECT | 硬性违规，不可修复，终止 | `rejected` | Identity 越权 / Boundary 身份锚点 / Evidence 伪造 / Audit 断链且无法补 |
| NEED_REVIEW | 允许进入人工/多角色复核队列，不自动生效 | `under_review`（status_hint 词，B.1 已冻结语义） | Boundary 跨域或高风险路径 / Conflict 倒转 / risk_level=high |
| DEFER | 暂缓观察，条件成熟再议 | `deferred` | Evidence 不足 / 单次事件证据 / gradual_transition 观察期 |

### 3.4 五道检查

#### 3.4.1 Identity Check（谁触发 / 是否允许修改）

- 输入：`actor_identity` + `target_domain`。
- 规则：`actor_identity` 经 `resolve_identity` 归一（无法解析 → fail-closed 沙盒 `_unknown_sender`）；按五扇门语义（src/security/permission.py `can_modify_*`）判断该身份对 `target_domain` 的写权限。
- 判定：无权限 → **REJECT**（reason=`unauthorized_actor`）；沙盒身份请求 personality/self_model 域 → **REJECT**；admin 通道不受 user 门限制但**必须走审计**。
- 复用：permission.py 纯函数；不修改五扇门语义（P2.1.3 现状冻结）。

#### 3.4.2 Boundary Check（修改目标是否合法）

- 输入：`target_domain` + `target_path` + `proposed_change`。
- 规则（三层）：
  1. `target_path` 必须落在 `target_domain` 的命名空间内（§4 六域表"合法路径前缀"列）；
  2. 身份锚点前缀硬拒：`identity.core.` / `identity.origin.` / `manifesto.`（复用 `FORBIDDEN_IDENTITY_PATH_PREFIXES`）→ 无条件 **REJECT**（reason=`identity_violation_rejected`）；
  3. 域内红线：§4 各域"禁止"列（如 relationship.trust/bond 直改、核心记忆删除）→ 违规即 **REJECT**，红线项同时进入"禁止事项"静态扫描清单（§6）。
- 复用：ApprovalPolicy Layer 1 的路径前缀规则；六域表是 B.1 Registry"禁止"列的契约化。

#### 3.4.3 Evidence Check（是否有事实依据 / 是否只是单次事件）

- 输入：`evidence` + `proposed_change` + `risk_level`。
- 规则（复用 ApprovalPolicy Layer 2 阈值，默认值冻结）：
  - 证据去重计数 ≥ `min_evidence_count`（3），否则 DEFER（reason=`insufficient_evidence_deferred`）；
  - 证据来源可信度按 `SOURCE_RELIABILITY`（user_behavior=1.0 > user_statement=0.8 > llm_inference=0.4 > context_guess=0.2）加权；`llm_inference` 或 `context_guess` 证据**不能单独构成依据**（配合 §6 第 5 条禁止项）；
  - 单次事件（occurrence_count=1）且 risk_level≠low → DEFER（reason=`time_span_too_short_deferred`）；
  - `proposed_change.delta` 超域上限（复用 growth_schema `MAX_SINGLE_EVENT_DELTA=0.01` / 域内上限）→ NEED_REVIEW 或 DEFER，不得 ACCEPT。
- 判定：全过 → pass；否则 **DEFER**（可补证据后重提）。

#### 3.4.4 Conflict Check（是否和已有人格冲突 / 是否重复）

- 输入：`target_path` + `proposed_change` + `context_snapshot`。
- 规则：
  1. 倒转检测（复用 ApprovalPolicy Layer 3）：before/after 跨越 0.5 中点且 |Δ|≥0.15 → **NEED_REVIEW**（reason=`conflict_with_existing_trait_deferred` + status_hint=under_review）；
  2. 与快照现值冲突：`context_snapshot` 对应域当前值 → 同向叠加超 `MAX_GROWTH_PER_DIMENSION=0.15`（年度）→ DEFER；
  3. 重复检测：`ProposalStore.exists_similar(source_event_id, compute_fingerprint(proposed_changes))` 命中 → **DEFER**（reason=`duplicate_proposal`，携带 existing_id，对齐 B.1 去重语义）。
- 判定：全过 → pass。

#### 3.4.5 Audit Check（是否生成完整链路）

- 输入：`mutation_id` + `source_event` + 决策上下文（request_id/trace_id，来自 RuntimeContext v2 `request` 层）。
- 规则（对应契约 §3.7 的链路要求）：
  1. `mutation_id` 合法且未重复落账（journal 中不存在同 id）；
  2. 链路关联键齐全：request_id + trace_id（缺失时 Gateway 生成并回填，不静默留空——对齐契约"所有审计写入必须携带 request_id + trace_id"）；
  3. 落账动作（Gateway 内部执行，属于决策输出而非域状态写入）：
     - 向 `context_snapshot` 来源的 v2 RuntimeContext 追加 `MutationJournal.add(MutationRecord(...))`（不可变追加，原对象不变）；
     - 追加 `AuditTrail.chain` 节点 `AuditLink(step=gateway_evaluated, event_ref, mutation_ref, audit_ref)`；
     - 决策自身写入 `ApprovalDecisionStore`（现状）与持久化审计（`record_audit_log`，带 trace_id——对齐契约 §3.7 承诺的向后兼容追加）。
  4. 落账失败（journal/audit 不可写）→ 判定 fail：若为决策查询可重试 → **DEFER**；若为身份/边界已违规但仍尝试执行 → **REJECT**。
- 判定：落账成功 → pass。这是 B10（审计默认关闭）与 B11（journal 零写入）的**契约级收口点**：Gateway 存在的前提是 audit 通道开启，audit 关闭时 Gateway 对 personality/self_model 域一律返回 REJECT（fail-closed），对低危域返回 DEFER。

### 3.5 决策流程（伪代码，冻结顺序）

```
evaluate(request, ctx_v2=None):
    result = {checks: {}}
    1. identity  = check_identity(request)          # fail → REJECT
    2. boundary  = check_boundary(request)          # 锚点/红线 → REJECT；跨域高风险 → NEED_REVIEW
    3. evidence  = check_evidence(request)          # fail → DEFER
    4. conflict  = check_conflict(request)          # 倒转 → NEED_REVIEW；重复 → DEFER
    5. audit     = check_and_append_audit(request)  # fail → DEFER/REJECT（见 3.4.5）
    verdict = ACCEPT 若五道全 pass
             elif 任意 REJECT 条件 → REJECT
             elif NEED_REVIEW 条件 → NEED_REVIEW
             else → DEFER
    return MutationDecision(...)
```

顺序冻结理由：身份与边界是硬闸（先于证据/冲突，防止"证据充分但目标非法"的请求浪费评估）；审计最后执行保证只有**将产生决策**的请求才落账（避免日志噪音）。

---

## 4. 六领域接入规则（Phase 3）

| 领域 | 允许（经 Gateway） | 禁止（Boundary Check 红线 → REJECT） | 输出路径（verdict → 现有组件） |
|---|---|---|---|
| **Memory** | 追加记忆（append，对齐 MEM-01/02 主链）；importance 更新（MEM-04，必须携带证据且低 delta） | 删除核心记忆（核心记忆定义：被 IdentitySnapshot 锚点引用或 `core=True` 标记，MEM-03/05 覆盖）；绕过 sanitize 的原始写入；importance 直改无证据 | ACCEPT → 现有 memory 主链（InteractionRecorder 门内）；importance 变更 → DEFER 至治理链 |
| **Emotion** | 临时情绪状态变更（EMO-01..04：dominant/intensity/原因/衰减，生命周期内自愈） | 永久人格改变（情绪不得写 personality/self_model 域——Rule 3 的域间红线）；绕过 Emotion Gate 的直写 | ACCEPT → 现有情绪链（orchestrator pre/post + 门）；NEED_REVIEW → 情绪审计队列（B7 补齐） |
| **Relationship** | 关系事件提案（REL-01/02：记忆事件 → RelationshipCandidate → PENDING_REVIEW） | 直接修改 trust/bond（REL-03..06 的 8 个 self-saving 方法与 repository 直写） | 关系事件 → proposal（RelationshipProposalStore PENDING_REVIEW，现状即目标路径）；trust/bond 变更必须 NEED_REVIEW 及以上 |
| **Growth** | 经历巩固（GRO-01..08：ExperienceJournal append → accept_experience → ProposalManager） | 绕过 Proposal 直通 apply（B1：orchestrator Step 14.5 → GrowthPipeline.incremental_update 内存直通）；双轨并存 | ACCEPT → GrowthIntegrationService.accept_experience（Stage 4 唯一链，B.1 红线已冻结）；直通路径判定 REJECT |
| **Personality** | **必须 Proposal → Review → Apply**（PER-01..06 全走 Governance 链） | 任何绕过链的直写：PersonalityResolver 静默直写（B2）、RuntimeController 伪审批（B3）、mark_approved=True 自批（B5）、auto_apply 绕闸（B6） | ACCEPT → 治理链（ProposalManager.create → ApprovalManager.govern → EvolutionPipeline.execute）；直写请求 REJECT |
| **Self Model** | **必须 Proposal → Approval → Update**（SM-01..07：SelfModelApprovalQueue.approve → Updater.apply_proposal → Store） | manager 旁路写（B12）；requires_approval=True 未过闸的写入（现状已有闸，Gateway 复核） | ACCEPT → SelfModelUpdaterAdapter.apply_suggestion（闸内）；旁路路径 REJECT |

跨域规则（补充 §6 第 4 条）：一次 MutationRequest 只允许一个 `target_domain`；一个事件需要影响多域时，必须拆成多条 request 各自决策（禁止"同步修改多个领域"的原子写，防止一个 handler 同时获得多域放行）。

---

## 5. 迁移路线（Phase 4）

| 优先级 | 目标 | 覆盖债务 | 具体动作（实施阶段执行，本设计只定路线） | 不变量保持 |
|---|---|---|---|---|
| **P0** | 人格/自我模型红线收口 | B2、B3、B5、B6、B12 | ① PersonalityResolver 只读化：resolve 只输出快照与候选，更新/记录一律改发 MutationRequest（Gateway → 治理链）；② RuntimeController `_apply_delta_safely` 去除 `p_rt_/a_rt_` 伪审批直写（runtime_controller.py:494-496），改为写入 pending 队列；③ PersonalityAdapter 三处 `mark_approved=True` 调用点改为经 ApprovalManager 决策（保留旧签名，内部改道——Priority 4 适配原则）；④ orchestrator.py:1365 auto_apply 改经 SelfModelUpdaterAdapter 闸（不清 requires_approval）；⑤ self_model_manager 旁路写收口为 Updater 通道 | I-1/I-2：全部为"调用点改道"，不新增写入口、不改组件签名 |
| **P1** | Growth 双轨合并 + Proposal 存储统一 | B1、B4 | ① orchestrator Step 14.5 直通链改为发 MutationRequest（growth 域）→ GrowthIntegrationService.accept_experience（废弃 GrowthPipeline.incremental_update 内存直通）；② 三存储（ProposalStore JSONL / ProposalStorage JSON / governance 存储）选定 ProposalStore 为唯一主存储，其余降为只读兼容投影（不删除——I-2）；③ status 词表统一：schema 默认 `proposed` 入库前归一为 `pending`（在 save 层或 create 层归一，白名单不动） | 旧存储文件不迁移不删除；双写期允许只读镜像 |
| **P2** | Emotion/Relationship/Memory 审计补齐 + 审计默认开启 | B7、B8、B9、B10 | ① Stage 03 情绪链补 audit（runtime_core.py:5157、orchestrator.py:2040/2075）；② memory 双主链并写改为单链 + audit 全覆盖（orchestrator.py:1246 与 interaction_recorder.py:125 合一）；③ runtime_core.py:3263-3264 relationship repository 直写改为经 RelationshipProposalStore；④ ProposalManager._record_audit 默认 no-op → 默认 record_audit_log（含 trace_id 追加字段）；⑤ personality_event_bus_enabled 默认 True | audit 记录格式向后兼容（追加字段不破坏存量 286 条） |
| **P3** | MutationJournal 全接线 + 库存清理 | B11、B13、B14 | ① 所有经 Gateway 的决策向 ctx.mutations / audit.chain 落账（§3.4.5 动作生产化）；② ~20 条死入口（B.1 §6.1）逐一核销（仅注释标记/台账登记，不删除代码——I-2）；③ 修复 B14 潜伏 bug（emotion_growth_service.py:94/97 `save(model)` → `save()`） | journal 是 v2 契约已有字段，接线非新增结构 |

每级迁移完成判定（验收指标）：该级涉及域的所有写入口在 Mutation Registry（B.1 §3）中"是否经过 Proposal/Audit"列全部翻转为合规；对应 B 编号债务在 B.1 §4 表中标记 closed 并引用本设计与实施记录。

---

## 6. 禁止事项（Phase 5）

Gateway 契约层面的五条禁令，每条给出违规形态、Gateway 拦截点与静态检测手段：

| # | 禁止事项 | 违规形态（对应债务） | Gateway 拦截 | 静态扫描检测 |
|---|---|---|---|---|
| 1 | 任意模块直接修改 personality | PersonalityResolver 直写（B2）、RuntimeController 伪审批（B3）、mark_approved 自批（B5） | Boundary Check：personality 域目标路径非治理链来源 → REJECT | 扫描 `update_trait / apply_evolution / setattr(.*trait` 调用点是否在 EvolutionPipeline/Adapter 白名单内 |
| 2 | 任意模块绕过 Proposal | orchestrator 14.5 直通（B1）、auto_apply 绕闸（B6） | Growth/Personality 域请求若无 proposal 关联（source_event→proposal_id 链缺失）→ REJECT | 扫描 `incremental_update / apply_proposal / apply_evolution` 的调用栈是否含 ProposalManager/ApprovalManager |
| 3 | 任意模块伪造 approval | 伪 ID `p_rt_{turn_uuid}/a_rt_{turn_uuid}`（B3） | Identity Check + Audit Check：decision 必须携带 ApprovalDecisionStore 中可查的 decision_id；伪造 ID 查无 → REJECT | 扫描 approval_id/decision_id 的生成来源（须为 build_approval_decision/ApprovalQueue.approve 产出） |
| 4 | 任意事件处理器同步修改多个领域 | 一 handler 内多域直写（B.1 跨域盘点） | 契约：一 request 一 domain（§4 跨域规则）；多域请求 → REJECT 并拆单 | 扫描 handler 函数体内是否同时出现 ≥2 域的写调用 |
| 5 | LLM 直接生成 mutation | llm_inference 来源证据单独成立（SOURCE_RELIABILITY=0.4） | Evidence Check：`llm_inference/context_guess` 证据不能单独满足阈值（§3.4.3）；actor_identity 为 LLM → fail-closed | 扫描 mutation 构建点是否在 LLM 响应处理路径（engine.generate 输出直连域写入） |

禁令的地位：这五条是 Gateway 的**验收红线**——实施阶段的任何代码审查以本表为准；B.1 的 B 类债务本质上都是这五条禁令的既往违规，Gateway 落地前 P0 动作（§5）先行堵口。

---

## 7. 设计边界与下一步

**本阶段（P2.3-B.2）已做**：设计基线（§1）、能力地图（§2）、Gateway 契约（§3）、六域规则（§4）、迁移路线（§5）、禁止事项（§6）。**唯一产出**是本文件；未修改 src/、data/、tests/ 任何文件；未创建代码实体；未接线任何业务模块。

**AGENTS.md Rule 4 四问（MutationRequest/MutationGateway 立项预答）**：

1. 为何已有模块无法完成？——现有 ApprovalManager/ApprovalPolicy 只服务 GrowthProposal 单一载体与 Stage 4 单一链，六域其余写入口（40 条活跃入口中的 ~30 条）没有统一的"身份/边界/证据/冲突/审计"前置判断；能力地图 C1 证实缺的是统一决策入口，不是机器。
2. 如何接入现有生命周期？——作为纯决策函数挂在调用方与既有执行组件之间（§3.1 图）；人格/自我模型域走既有 Governance 链，其他域保持现有链但前置本决策；不进入 RuntimeCore 业务阶段（17 阶段零改动）。
3. 数据如何流动？——MutationRequest 输入 → 五道检查（读 v2 快照与既有 store）→ MutationDecision 输出 → 落账 MutationJournal + AuditTrail（v2 契约已有字段）+ 分派既有执行组件；无新存储文件。
4. 如何测试？——纯函数决策可单元测试（五道检查 × 四态 × 六域矩阵）；落账副作用通过注入的 journal/audit 桩验证；实施阶段配合 B.1 Registry 全表回归。

**实施前置依赖**：P2.3-A 系列已交付 RuntimeContext v2 与 normalizer（context_snapshot 来源已就绪）；P0 动作（§5）须在 Gateway 上线前完成（先堵口后上闸）；audit 通道开启是 Gateway 对高危域生效的前提（B10）。

**建议下一阶段**：P2.3-B.3 Mutation Gateway 实施立项（P0 先行），或按用户任务书继续 B 系列后续批次。实施时以本文件 §3 契约为验收基准，§5 迁移顺序为里程碑，§6 禁令为审查红线。

---

## 8. P2.3-B.3 落地记录（基础实施）

> 日期：2026-08-19。落地范围：契约 + Gateway 骨架 + 五道检查 + AuditWriter 接口 + 41 项测试。
> 详细差异与迁移入口见 `docs/governance/mutation_gateway_implementation_report.md`。

- 新增 `src/governance/`（mutation_contract / mutation_gateway / audit_writer / checks 五文件）：全部为**新增文件**，既有 src 文件 0 修改；Gateway 无生产调用方，未接管任何 mutation 写路径。
- 契约落地：MutationRequest 9 字段 / MutationDecision 6 字段 / CheckResult / DecisionVerdict 四态；frozen + JSON 安全强制（manager/repository/client 实例构造即拒绝）；target_domain 复用 `MUTATION_TARGETS`。
- 五道检查顺序与首停按 §3.5 冻结；检查层复用 §2 能力地图组件（五扇门 / FORBIDDEN_IDENTITY_PATH_PREFIXES / approval_policy 阈值常量 / compute_fingerprint+exists_similar / growth_schema 常量），未重写任何既有规则。
- 与本设计的六处落地差异（D1-D6）已在报告 §3 登记：prescription 字段暂缺（任务书字段集）、evidence 不足按任务书测试 #8 取 NEED_REVIEW（原设计 DEFER）、actor 不做二次解析、审计链路缺失 fail-closed 不回填、倒转阈值在单事件幅度上限后的可达性待 B.4 裁决、relationship trust/bond 取 NEED_REVIEW。
- 下一步：P2.3-B.4 人格红线迁移（入口装配器 + prescription 引入 + audit 落账接线，见报告 §4）。

---

## 附录：证据索引

| 事实 | 证据 |
|---|---|
| MutationRequest 无类实现 | `grep -rn "MutationRequest" src/ tests/` 零命中；MUTATION_FORMS 含字符串常量（request_context.py:82） |
| MutationRecord/MutationJournal/AuditTrail 载体 | request_context.py:329-404 / 410-469（六域三形式校验、不可变追加、trace 关联键） |
| journal/audit 零生产写入 | `grep mutations.add / AuditLink(` 生产代码零命中（B11） |
| ProposalStore 能力与词表 | src/growth/proposal_store.py:69-72（VALID_STATUSES）、:217 exists_similar；schema 默认 status="proposed"（src/contracts/growth_schema.py:65） |
| ProposalManager 入口群 | src/growth/proposal_manager.py:96/260/348/406/497/520（含 apply 时 mark_approved=True :430 = B5） |
| ApprovalManager 唯一生产调用方 | growth_integration.py:829；govern_proposal 定义 approval_manager.py:103 |
| ApprovalPolicy 三层保护 | approval_policy.py:105/136/152/187；FORBIDDEN_IDENTITY_PATH_PREFIXES :34 |
| EvolutionPipeline EP-1..4 | evolution_pipeline.py:119-178；唯一调用方 growth_integration.py:881 |
| TraitStateUpdater 能力 | trait_state_updater.py:26-80（approved 校验/钳制/去重）；调用方 personality_adapter.py:588、personality_evolution_pipeline.py:146 |
| SelfModelUpdaterAdapter 安全闸 | self_model_updater_adapter.py:147-190（requires_approval 拒绝）；调用方 runtime_core.py:3379 |
| SelfModelApprovalQueue 内存态 | self_model_governance.py:185-244 |
| 双 Proposal 存储 | src/growth/proposal_store.py（JSONL）/ src/growth/proposal/storage.py:43（ProposalStorage）/ GovernanceProvider（src/admin/governance_provider.py:78） |
| B1-B14 债务与六域写入口 | docs/governance/mutation_boundary_audit.md §3/§4（40 条活跃 + ~20 条死入口） |
| 权限门与身份 | src/security/permission.py（can_modify_*）；src/security/identity.py:188 resolve_identity |
| 审计现状 | src/audit/record.py:48；audit_logs.jsonl 无 trace_id（契约 §3.7 已承诺追加） |
| 委员会接口 | design_committee_future.md §2.1/§2.2（ReviewRequest/CommitteeDecision 与唯一变更链） |
