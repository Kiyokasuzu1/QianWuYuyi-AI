# P2.3-B.5 Growth Legacy Mutation Registry（只读审计产物）

> 阶段：Phase 1（只读审计）
> 分支：develop/v1.1 @ 225d040
> 审计日期：2026-08-19
> 本文件是 B.5 迁移的事实基线：列出 Growth 领域所有绕过治理链的 mutation 入口、
> Proposal 双轨现状、Growth Apply Boundary 与 Gateway 接入事实。

---

## 1. P1 红线① — GrowthPipeline.incremental_update 直接 apply

| # | 位置 | 入口 | 行为 | 绕过点 |
|---|------|------|------|--------|
| G-01 | src/growth/pipeline.py:306 | `incremental_update()` → `self.growth_engine.apply_proposal(proposal)` | `_build_proposal_from_evaluated()` 产出 `status="proposed"` 的提案后**零审核直接执行**，写入 GrowthState 并落盘 | 无 Gateway / 无 Approval / 无 audit 链路 |
| G-02 | src/growth/pipeline.py:342 | `incremental_update()` fallback 分支 → `self.growth_engine.apply(event)` | proposal 未生成或未 applied 时走 GROWTH_MAP 直写路径 | 连 Proposal 都没有，纯事件直改状态 |
| G-03 | src/growth/pipeline.py:493 | `run_full_consolidation()` → `self.growth_engine.apply(event)` | 批量整合路径同样直接 apply | 同 G-02 |
| G-04 | src/orchestrator.py:1327 | Step 14.5 `self._growth_pipeline.incremental_update(user_message)` | 每轮聊天自动触发上述直写链（仅受 P2.1.3 sandbox 门保护） | 生产主链路，无治理 |

**B.5 处理计划**：G-01/G-02 在 `growth_mutation_gateway_enabled=True` 时改经 MutationGateway（Phase 3）；G-03 本阶段保留（任务书 Phase 3 仅覆盖 incremental_update），registry 中登记为**保留的 legacy 入口**；G-04 不改（触发点，不是 mutation 本体）。

## 2. P1 红线② — proposal 绕过审核直接进入状态更新

| # | 位置 | 入口 | 行为 | 绕过点 |
|---|------|------|------|--------|
| G-05 | src/growth/growth_engine.py:232 | `GrowthEngine.apply_proposal(proposal)` | "纯执行方法"：**不检查 proposal.status 是否为 approved**。仅拒绝"无 evidence 且 confidence<0.8"。然后 `state.update_metrics()` + `state.save()` 直接落盘 | 任何 status（含 "proposed"）的提案都可执行 |
| G-06 | src/growth/growth_engine.py:135 | `GrowthEngine.apply(event)` | GROWTH_MAP/REPEAT_BONUS 直写 GrowthState（`_apply_metrics` → `update_metrics` → `save()`），无 proposal | 事件直接修改成长状态 |
| G-07 | src/growth/proposal_manager.py:427-431 | `ProposalManager.apply_proposal()` → `personality_adapter.apply_proposal(proposal, actor=..., mark_approved=True)` | **自批准**：apply 时由 adapter 生成 `record["approved"]=True` + `decision_reason="approved_by:{actor}_via_adapter"`，actor 默认 "system"，无独立审批环节 | mark_approved 硬编码 True |
| G-08 | src/growth/growth_loop.py:265 | `GrowthLoop._propose()` → `proposal.status = GrowthStatus.PENDING_APPROVAL` | 自管状态机（第 4 套状态词表），直接写 proposal.status | GrowthLoop 不在生产主链（仅 runtime/integration 骨架引用），登记为保留 legacy |
| G-09 | src/growth/proposal/reviewer.py:107/134/163 | `ProposalReviewer` 直接置 `proposal.status = APPROVED / REJECTED / APPLIED` | 评审器直写状态字段 | 与 ProposalStore/ProposalManager 状态机平行 |

**B.5 处理计划**：G-05/G-06 在网关开启后被 GrowthMutationAdapter 的 apply_route 包住（ACCEPT 才允许进入；G-05 保留为"现有 Apply Adapter"的成长域执行件）；G-07 保留旧 API（不删），registry 登记；G-08/G-09 保留（兼容层，不在 B.5 迁移范围）。

## 3. P1 红线③ — ProposalStore 双轨

### 3.1 存储双轨

| | Track A（治理轨） | Track B（兼容轨） |
|---|---|---|
| 文件 | src/growth/proposal_store.py | src/growth/proposal/storage.py |
| 存储 | JSONL append-only `data/proposals/proposals.jsonl`（latest-wins 索引） | JSON 单文件 `data/growth/proposals/proposals.json`（atomic_write_json） |
| schema | contracts.growth_schema.GrowthProposal（canonical） | growth/proposal/proposal.py GrowthProposal（**Phase 3.6.4 起 deprecated**） |
| 状态词表 | VALID_STATUSES = {pending, accepted, rejected, applied, approved} | PROPOSAL_STATUS = {pending, approved, rejected, applied, cancelled} |
| 消费方 | ProposalManager（create/accept/reject/apply） | ProposalReviewer、Admin GovernanceProvider 输入兼容层 |

### 3.2 状态词表（5 套并存）

1. contracts/growth_schema 文档词表：proposed / accepted / rejected / cancelled / expired（默认 `"proposed"`）
2. ProposalStore.VALID_STATUSES：pending / accepted / rejected / applied / **approved**
3. proposal/constants.PROPOSAL_STATUS：pending / approved / rejected / applied / **cancelled**
4. GrowthLoop.GrowthStatus（Enum）：PROPOSED / PENDING_APPROVAL / APPROVED / APPLIED / REJECTED（值为 snake_case）
5. ProposalManager.update_proposal_status_and_meta（R2.5.3 Approval 写入）：approved / rejected / deferred / under_review（经 from_states 保护）

### 3.3 治理链外事实

- AUTO_APPROVE_THRESHOLD（proposal/constants.py）存在但仅被 ProposalReviewer.should_create_proposal 使用（决定是否建提案），ProposalManager 的 auto_accept 默认 `False`（安全）。
- ProposalManager.create_proposal 有 confidence(0.8)/evidence/dedupe 三道安全规则，但**accept/apply 不检查 MutationGateway**——B.1 治理链与 B.3 Gateway 尚未对接。

**B.5 处理计划（Phase 4）**：不删除任何旧 API；新增 `to_mutation_proposal()`（Track B legacy dict → canonical + 治理链接键）与 `attach_governance_linkage()`（mutation_id/request_id/trace_id/evidence 注入 evaluator_meta._governance + 顶层键）；所有**新产生**的 proposal 经迁移层携带治理链接键。状态映射表（只读转换，不改存储语义）：

| legacy | canonical | 说明 |
|---|---|---|
| pending | pending | 一致 |
| approved | approved | 一致 |
| rejected | rejected | 一致 |
| applied | applied | 一致 |
| cancelled | rejected | 最接近的终态 |

## 4. P1 红线④ — Growth → Personality 更新缺少统一 MutationRequest

| # | 位置 | 行为 |
|---|------|------|
| G-10 | src/growth/growth_engine.py:307 | apply_proposal → `GrowthState.update_metrics()` + `save()`：成长域状态直写（无 MutationRequest） |
| G-11 | src/growth/pipeline.py:107 | `PersonalityResolver(state=self.growth_engine.state, ...)`：GrowthState 作为人格 trait 漂移来源，Resolver 消费成长增量（该漂移路径 B.4 已加门，`personality_mutation_gateway_enabled`） |
| G-12 | src/personality/personality_adapter.py:466 | `PersonalityAdapter.apply_proposal()`：TraitStateUpdater 内存应用 trait（Growth→Personality 的现有 Apply Adapter，无 MutationRequest） |
| G-13 | src/personality/trait_state_updater.py | TraitStateUpdater：EvolutionRecord → trait_states 内存变更的执行件 |

**Growth Apply Boundary（B.5 建立）**：
- 成长域状态（GrowthState.metrics）的执行件 = `GrowthEngine.apply_proposal()`（G-05，纯执行，保留）。
- 人格域状态（trait）的执行件 = `PersonalityAdapter.apply_proposal()` / `TraitStateUpdater`（G-12/G-13，保留）。
- **新规则**：Growth 侧在网关开启时只产出 MutationRequest；ACCEPT 后由 apply_route 进入现有执行件；Growth 不得越过 Request 直接调用人格执行件。

## 5. MutationGateway 接入事实（B.3 骨架）

- `MutationGateway(identity_check=, boundary_check=, evidence_check=, conflict_check=, audit_check=, audit_writer=)` 五道全可注入；执行顺序冻结 identity→boundary→evidence→conflict→audit；首失败即停。
- **IdentityCheck**：`PRIVILEGED_ACTORS = {admin, system, auto_accept, gateway}`，`SANDBOX_ACTORS = {sandbox, _unknown_sender, unknown}`，growth 域门 = `can_trigger_growth`（probe permission 非 "user" 一律 fail-closed REJECT）。
  **⚠ B.5 接入缺口**：任务书要求 `actor_identity="growth_system"`，但 "growth_system" 不在任何白名单 → 会被 fail-closed REJECT。处理：在 PRIVILEGED_ACTORS 追加 "growth_system"（内部子系统身份，requires_audit=True 审计强制不变；白名单扩展≠Gateway 绕过）。
- **BoundaryCheck**：growth 域命名空间 = `("growth.",)`；`proposed_change["bypass_proposal"] is True` → REJECT。→ Growth 请求 target_path 必须形如 `growth.metrics.<dim>`。
- **EvidenceCheck**：去重 ref ≥3；证据类型不能全部 ∈ {llm_inference, context_guess}；occurrence<2 且 risk≠low → NEED_REVIEW；confidence<0.75 → NEED_REVIEW；|delta|>0.01 → NEED_REVIEW。
- **ConflictCheck(proposal_store=)**：注入 `exists_similar(source_event_id, fingerprint)` 协作件后启用重复提案检测（duplicate → DEFER）；倒转冲突 → NEED_REVIEW；年度上限 → DEFER。
- **AuditCheck(journal=)**：`context_snapshot` 缺 request_id/trace_id → DEFER。
- **MutationRequest**：9 字段冻结 dataclass，容器字段 JSON 安全校验（拒绝携带写句柄）；target_domain ∈ MUTATION_TARGETS 六域。

## 6. 迁移开关现状

- B.4：`personality_mutation_gateway_enabled`（src/personality/mutation_adapter.py，默认 False）。
- B.5 新增：`growth_mutation_gateway_enabled`（src/growth/mutation_adapter.py，默认 False）。
- 两开关互相独立：成长域请求 target_domain="growth" 与人格域请求分别走各自的网关接线，互不覆盖。
