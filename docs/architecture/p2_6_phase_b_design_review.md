# P2.6 Phase B 开始前设计审查：Legacy Path Proposal 化最小方案

> 性质：代码只读分析 + 实施方案设计（禁止修改代码、开启 flag、迁移旧链）
> 日期：2026-08-21
> 上游依据：p2_6_governance_unification_audit.md §3/§4（Phase A 已交付探针与占位开关）

## 0. 结论摘要

1. Phase B 不需要任何新模块、新 store、新 proposal 模型——B.5 网关路由、MutationGateway、双 proposal 存储、admin 审核 UI、drain、Type A/B 适配器**全部已存在**，Phase B 的工作量是「3 个文件的条件分支扩展 + 1 个 flag-gated 应用消费镜像」。
2. 关键发现：系统里存在**两套 GrowthProposal 模型与两个 store**（§1.4），且已有官方 Type A/B 适配器（`src/runtime/adapters/growth_proposal_adapter.py`）负责互转。Phase B 不新建第三套，按用途各用其长：P2/P2b 走 B.5 现成的 A-store 链路；P3 走 admin+drain 现成的 B-store 链路。
3. P3 的「existing drain」有一个语义错位需要正视：drain 只消费 `proposal_type=personality` 且写入目标是 PersonalityState（personality_state.json），而 Step 14.6 的变更目标是 SelfModel store（成长叙事 + self_understanding）。设计给出最小解：P3 提案以 `type=self_model` 落 B-store（复用 store/状态机/admin UI），并新增一个**镜像 drain 结构**的 flag-gated 消费器（不修改现有 drain 语义）。
4. 红线全部可满足：flag 默认 False 时三处改动逐字节等价现状；RuntimePipeline/Stage 04/drain 语义/legacy auto_apply 代码全部不动。

## 1. Proposal 体系重扫

### 1.1 组件现状

| 组件 | 位置 | 状态 |
|---|---|---|
| GrowthProposal（契约模型 A） | src/contracts/growth_schema.py | 字段冻结（Phase 3.6.5）：id/proposed_changes/confidence/evidence_ids/evaluator_meta/timestamp/status(proposed|accepted|rejected|cancelled|expired)/schema_version |
| GrowthProposal（治理模型 B） | src/growth/proposal/proposal.py | proposal_id/proposal_type(personality|relationship|identity|self_model)/status(pending|approved|rejected|applied|cancelled)/before_state/after_state/affected_dimensions/reviewer_id/metadata/expires_at |
| ProposalStore（A-store） | src/growth/proposal_store.py | JSONL append-only，data/proposals/proposals.jsonl；VALID_STATUSES 含 approved；latest-wins；B.5 park 与 ProposalManager 使用 |
| ProposalStorage（B-store） | src/growth/proposal/storage.py | JSON 全量原子写，data/growth/proposals/proposals.json；get_proposal_storage() 单例；admin governance_provider 与 drain 使用 |
| ProposalManager | src/growth/proposal_manager.py | create_proposal（置信度门槛 0.8、去重、需 evidence）、apply_proposal（line 406-427，auto_accept 默认关） |
| MutationRequest / MutationDecision / DecisionVerdict | src/governance/mutation_contract.py | 9 字段冻结 / 6 字段冻结 / 四态 ACCEPT|REJECT|NEED_REVIEW|DEFER |
| MutationGateway | src/governance/mutation_gateway.py | 五道检查首败即停；NEED_REVIEW 自动 record_pending 落账（B.10）；零执行副作用 |
| PersonalityEvolutionPipeline | src/personality/personality_evolution_pipeline.py | apply_approved_to_state → PersonalityState.apply_evolution + save_personality_state；drain 唯一执行件 |
| Type A/B 适配器 | src/runtime/adapters/growth_proposal_adapter.py | detect_type / to_type_b / to_type_a / build_approval_input，两套模型互转的官方桥 |
| B.5 路由 | src/growth/pipeline.py:363 `_route_proposal_through_gateway` | 逐 ChangeItem → MutationRequest → Gateway → ACCEPT 即经现有 Apply Adapter 执行；NEED_REVIEW/DEFER park 落盘；返回 apply_proposal 兼容 dict |

### 1.2 复用性回答

1. **可直接复用**：B.5 路由全套（`_route_proposal_through_gateway` / `_park_proposal_for_review` / `_build_evidence_from_event` / growth mutation_adapter.build_request+route）、MutationGateway + 五道检查 + NEED_REVIEW 落账、A-store、B-store + `PROPOSAL_TYPE.SELF_MODEL`、admin review_proposal（按 id 操作，无类型过滤）、personality drain、Type A/B 适配器、SelfModelUpdater.create_proposal_from_growth / apply_proposal、Phase A GovernanceAuditProbe。
2. **字段不足**：仅一处——治理模型 B 无携带 SelfModelChangeProposal 的专用字段；用其已存在的 `metadata` 字典承载 `SelfModelChangeProposal.to_dict()`（全 JSON 安全）即可，**无需改任何模型**。
3. **新增 adapter**：不需要。
4. **重复 proposal 系统**：**存在**（A/B 两模型 + 两 store），但属于既有架构且已有官方桥；Phase B 不新建第二/第三套，也不合并（合并违反 Priority 4 小范围修改原则）。

### 1.3 各链路的写入者/消费者（本次扫描确认）

- **B-store 写入者**：orchestrator `_create_growth_proposal`（relationship 信任超阈值提案，line 2227-2253）、admin review_proposal（状态翻转）、Type A/B 镜像同步。
- **B-store 消费者**：admin governance_provider（review/dashboard）、`drain_approved_growth_proposals`（runtime_core.py:6390，仅 `proposal_type=personality`）。
- **A-store 写入者**：ProposalManager.create_proposal（Stage 04 accept_experience）、B.5 `_park_proposal_for_review`（NEED_REVIEW/DEFER）、gateway B.10 record_pending 协作件。
- **A-store 消费者**：无 drain 消费者——parked 提案的 approved 流转依赖 Type A/B 镜像/审核桥（既有设计）。

## 2. P2/P2b 迁移设计（growth 域）

### 2.1 现状路径

```
incremental_update(user_message)          # 唯一生产调用方：orchestrator Step 14.5 (orchestrator.py:1327)
  ├─ evaluator.evaluate(...)
  ├─ growth_allowed=True → _build_proposal_from_evaluated
  │    ├─ B.5 flag on  → _route_proposal_through_gateway (gateway_handled=True)
  │    └─ B.5 flag off → growth_engine.apply_proposal(proposal)   ← P2 直写（Phase A 探针已挂）
  ├─ growth_allowed=False 或 proposal 构建失败或 apply 拒绝
  │    └─ (622) B.5 on 且 gateway_handled → 跳过
  │    └─ 否则 → growth_engine.apply(event)                        ← P2b 直写（line 630）
  └─ run_full_consolidation → growth_engine.apply(event)           ← P2b 第二站点（line 781，无生产调用方）
```

### 2.2 截断位置与提案创建位置

**截断点选在 pipeline.py（调用方），不改 growth_engine.py。** 理由：引擎是纯执行件（含网关 ACCEPT 的合法执行件用途），提案构建、证据、actor 身份全部在 pipeline 层；在引擎内截断会把治理依赖反向注入执行件。

| 点 | 位置 | B 态（unification flag on）行为 |
|---|---|---|
| P2 截断 | pipeline.py:574 条件 | `is_growth_mutation_gateway_enabled() or is_governance_unification_enabled()` → 走 `_route_proposal_through_gateway`（gateway_handled=True）。ACCEPT → 现有 Apply Adapter 执行；NEED_REVIEW/DEFER → park（A-store，pending）；REJECT → 丢弃不写 |
| P2b 截断-1 | pipeline.py:622 条件 | 扩展为：unification on 时**一律不进入** legacy `apply(event)` fallback（无论 gateway_handled 与否）。理由：growth_allowed=False 的事件本就被评估器裁定「不成长」，legacy fallback 直写与其矛盾，B 态直接抑制并留审计记录 |
| P2b 截断-2 | pipeline.py:781 run_full_consolidation | 同一抑制逻辑（无生产调用方，防御性一致化） |
| 提案创建 | 不变 | 复用 `_build_proposal_from_evaluated`（line 211）——B 态不新增提案构建逻辑 |

### 2.3 返回值兼容

`_route_proposal_through_gateway` 返回的 dict 与 `GrowthEngine.apply_proposal` 同构（status: applied|needs_review|deferred|rejected|declined + proposal_id + before + delta）。incremental_update 内部仅消费 `status == "applied"`（line 586）→ 兼容；growth_records 生成（line 596 `apply_evaluated`）位于分支之外，B 态照常产出 → Step 14.6 输入不受影响（其 auto_apply 由 §3 截断）。

### 2.4 失败 fallback

- 网关路由异常：沿用 B.5 既有隔离（pipeline try/except 不阻断聊天）；路由失败时**不允许**回落到 legacy apply——B 态下 fallback 即绕过，直接跳过本轮 mutation（fail-closed）。
- `_park_proposal_for_review` 落盘失败：既有实现已隔离（返回 False，不阻断），提案丢失但零直写——可接受（治理降级方向是「少写」不是「绕过写」）。

### 2.5 测试兼容性

flag 默认 False → 574/622 条件值与现状完全一致（`False or False`）→ 既有测试字节级无感知。B.5 既有测试（test_growth_mutation_gateway.py）不读 unification flag，不受影响。

## 3. P3 迁移设计（SelfModel 域）

### 3.1 现状

```
orchestrator Step 14.6 (orchestrator.py:1344-1375)
  policy.evaluate(record) → GovernanceDecision (deny | auto_apply | approval_required)
    auto_apply        → updater.create_proposal_from_growth → apply_proposal → _apply_to_store
                        → SelfModel store（growth_narratives + self_understanding，无人工审核）
    approval_required → SelfModelApprovalQueue（进程内存态，重启即失）→ enqueue
```

### 3.2 如何保留 policy 判断逻辑

**完全不动 policy。** `SelfModelGovernancePolicy._evaluate_rule` 与 RULES 表（context 0.50 / preference 0.65 / trait 0.80 / identity DENY / trace DENY）保持原样；`evaluate()` 仅附加 Phase A 审计记录。B 态下 policy 仍返回 auto_apply 决策——**决策与执行分离**，截断发生在 orchestrator 的执行分支（见 3.3）。这保证 Phase A 的 P3 探针（决策层记录）在 B 态继续产出完整决策流，审计可对照「决策=auto_apply 但未执行」的治理事实。

### 3.3 如何替换 apply 动作

orchestrator.py:1364 `if _decision.action.value == "auto_apply":` 处加 unification 条件：

- **A 态（flag off）**：逐字节现状（auto_apply 直写）。
- **B 态（flag on）**：auto_apply 分支与 approval_required 分支合并为「生成 proposal + 持久化落账 + 零 apply」：
  1. `create_proposal_from_growth(record)`（现有，无副作用）→ SelfModelChangeProposal；
  2. 包装为治理模型 B：`GrowthProposal(proposal_type="self_model", status="pending", affected_dimensions=record.affected_dimensions, metadata={"self_model_proposal": proposal.to_dict(), "governance_decision": decision.to_dict(), "source": "legacy_step_14_6"})`；
  3. `get_proposal_storage().save(...)`（B-store，复用现有 store + 原子写）；
  4. 内存 ApprovalQueue 照常 enqueue（保留旧 API 语义，作查询缓存）；
  5. **不调用** `updater.apply_proposal`。

### 3.4 approval_required 是否已有持久化路径

- **部分有**：B-store + `PROPOSAL_TYPE["SELF_MODEL"]` + PROPOSAL_STATUS 五态 + admin `review_proposal`（按 proposal_id 操作、无类型过滤、只标状态不 apply）全部存在 → 「persistent store + admin review」零新代码。
- **缺口**：approved 的 self_model 提案没有消费器——drain（runtime_core.py:6390）明确 `proposal_type != personality` 即跳过（line 6452「由各自链路处理」）。且 drain 的写入目标是 PersonalityState，与 SelfModel store 不同，**不能**通过把 self_model 提案伪装成 personality 类型来复用 drain（那会改变写入目标并丢失成长叙事，且违反「不修改 drain 语义」红线）。

### 3.5 最小消费器设计（镜像 drain，不修改 drain）

新增 RuntimeCore 方法 `drain_approved_self_model_proposals()`（结构镜像现有 drain，flag-gated）：

- 仅当 `governance_unification_enabled=True` 时存在行为（默认关 → 零行为变化）；
- `get_proposal_storage().list_by_status("approved")` → 过滤 `proposal_type == "self_model"` → 逐条 try/except fail-soft；
- 从 metadata 还原 `SelfModelChangeProposal.from_dict(...)` → `SelfModelUpdater.apply_proposal(...)`（现有公开接口 → _apply_to_store）→ 成功后 storage.save 标 `applied`（现有 PROPOSAL_STATUS）；
- 挂接点：stage 13 现有 growth drain 调用之后（复用既有 fail-soft 壳，不改 stage 契约、不改 drain 方法体）；
- 禁止自动 approve：只消费 admin 已 approve 的记录（与 drain 同语义）。

**决策点（留给实施任务书确认）**：若 Phase B 范围必须更小，本消费器可推迟到 Phase C，代价是验收项「approve 后状态变化」只对 personality 路径成立、self_model 提案长期停留 approved。本审查**推荐 Phase B 一并实现**，因为它是镜像而非新语义，且没有它 P3 链不成环。

### 3.6 状态枚举

**无需补充。** GovernanceAction（deny/auto_apply/approval_required）、PROPOSAL_STATUS（pending/approved/rejected/applied/cancelled）、PROPOSAL_TYPE（含 self_model）、SelfModelChangeProposal.requires_approval 均已存在。

## 4. Runtime fallback 治理不绕过分析

fallback 链：RuntimeCore 异常 → RuntimePipeline per-request 回退 → orchestrator.process → Step 14.5/14.6。

**结论：flag 是模块级全局，两处截断（pipeline 574/622、orchestrator 1364）不区分调用来源——fallback 与直连、QQ 与 HTTP 全部命中同一治理分支。** 验证路径：

1. RuntimeCore 失败 → fallback → Step 14.5 `incremental_update` → 574 条件含 unification → 网关路由（fallback ≠ 直写）；
2. Step 14.6 → 1364 分支 → B 态零 apply、proposal 落 B-store；
3. Phase A 探针在引擎层继续全量记录——B 态验收用「probe 中 direct_apply=0」作为全局不变量，**任何绕过（包括未来新增调用方）都会被探针抓到**。

残余风险：

- `run_full_consolidation`（line 781）无生产调用方，但必须与 incremental_update 同规则抑制，防止未来接线时成为绕过口；
- `GrowthEngine.apply`/`apply_proposal` 被 pipeline 之外的代码直接调用（当前全 src 扫描仅 pipeline 两处 `.apply(` 调用点；`apply_proposal` 仅 pipeline 445/585）——B 态判据依赖探针兜底，而非逐调用点审计。

## 5. Phase B 最小实施计划

### 5.1 文件修改列表

| 文件 | 类型 | 修改目的 | 修改范围 | 影响生产默认行为 |
|---|---|---|---|---|
| src/growth/pipeline.py | 修改 | P2/P2b 截断：574 与 622 条件扩展 + run_full_consolidation 同规则抑制 | `incremental_update` 分支条件 + `run_full_consolidation` apply 抑制；新增私有辅助 `_is_growth_governed()`（= B.5 flag or unification flag） | 否（flag off 时条件值与现状相同） |
| src/orchestrator.py | 修改 | P3 截断：auto_apply 分支 B 态转持久化 approval_required | Step 14.6 执行分支（1364 起）；policy/updater/queue 不动 | 否 |
| src/runtime/runtime_core.py | 修改 | 新增 `drain_approved_self_model_proposals()` 消费镜像 | stage 13 内 fail-soft 壳中追加一次 flag-gated 调用；现有 drain 方法体零改动 | 否 |
| src/governance/governance_unification.py | 不动 | flag 已存在（Phase A） | — | — |
| src/growth/growth_engine.py | 不动 | 探针已在 Phase A 接入 | — | — |
| src/personality/self_model_governance.py | 不动 | 决策纯函数保持 | — | — |
| tests/test_governance_unification_phase_b.py | 新增 | A/B 行为验证（§6） | 新测试文件（规避 conftest 单例陷阱 token） | 否 |
| docs/architecture/p2_6_phase_b_implementation.md | 新增 | 实施报告 | — | — |

**新增模块：零。** 三处改动全部落在既有文件的条件分支上。

### 5.2 实施顺序

1. pipeline.py `_is_growth_governed()` + 574/622 扩展 + run_full_consolidation 抑制（P2/P2b 截断）→ 跑 B.5 与 growth 既有电池；
2. orchestrator.py Step 14.6 B 态持久化分支（P3 截断）→ 跑 4.0.3 governance 电池；
3. runtime_core.py self_model drain 镜像（flag-gated）→ 跑 runtime 相关电池；
4. A/B 全量对照（§6）→ 输出实施报告；
5. 停止。Phase C（A/B 灰度观察）/Phase D（删旧 auto_apply）为独立任务书。

## 6. A/B 测试计划

方法论同 Phase 4/6/P2.5：临时 pytest 插件设置/复位 flag，同电池双态跑，`grep -E "^(FAILED|ERROR)"` + `comm` 对比失败集合。

**A 态（flag off）**：
- 失败集合同 Phase A 基线（0 新增 0 消失）；
- probe 记录分布与 Phase A 完全一致（direct_apply 照常产生）；
- data/ 快照除既有测试噪声外无新差异。

**B 态（flag on）六项判据**：

1. **direct mutation = 0**：probe JSONL 中 `decision=direct_apply` 记录数为 0（growth 域）；P3 的 policy 决策记录允许存在（决策≠执行，断言落在「store 未变化」而非「无 auto_apply 决策」）；
2. **proposal 数增加**：A-store 新增 pending（NEED_REVIEW/DEFER park）+ B-store 新增 type=self_model pending；
3. **gateway 调用增加**：InMemoryAuditWriter requests/decisions/records 数量 > 0 且逐条可审计（mutation_id 关联）；
4. **未 approve 前状态不变化**：growth_state.json 与 self_model 快照逐字节不变；
5. **approve 后状态变化**：personality 提案经 admin review_proposal → drain 应用（personality_state.json 变化、proposal 标 applied）；self_model 提案经 review → 镜像消费器应用（growth_narratives 追加、标 applied）；
6. **fallback 不绕过**：模拟 RuntimeCore 异常触发 orchestrator fallback → B 态仍满足判据 1-3（探针 direct_apply=0、gateway/提案增长）。

附加：B 态下 admin 审核 UI 对 type=self_model 提案可加载/批准/拒绝（复用 review_proposal，无类型过滤——回归验证）；ApprovalQueue 内存缓存与 B-store 持久化双写一致性。

## 7. 风险列表

| # | 风险 | 缓解 |
|---|---|---|
| R1 | B 态抑制 growth_allowed=False 事件的 legacy apply → GrowthState 指标漂移放缓 | 意图内（评估器已裁定不成长）；A/B 量化漂移幅度，报告留档 |
| R2 | 提案量激增：B 态每轮事件转提案，B-store MAX_PROPOSALS=500 截断、A-store append 无界 | 依赖既有去重（exists_similar）+ B.5 park 前 evidence 检查；观察期统计提案增速 |
| R3 | 审计口径混淆：P3 探针记录「auto_apply 决策」但 B 态未执行 | 测试与文档明示「决策记录 ≠ 直写记录」；判据落在 store 快照 |
| R4 | self_model 消费镜像是新执行路径 | 严格镜像 drain 约束：只消费 admin-approved、fail-soft、flag-gated、单提案 try/except；不动 drain 方法体 |
| R5 | B 态治理降级方向：park 落盘失败 → 本轮零直写零落账（提案丢失） | fail-closed 可接受；probe 留痕 source_path 记录可达性 |
| R6 | 双模型混淆：误把 A 模型写入 B-store（字段不匹配静默失败） | 实施检查单：P2/P2b 只写 A-store，P3 只写 B-store；跨桥用 Type A/B adapter |
| R7 | legacy auto_apply 代码仍存在（仅被 flag 门控），未来误接线直调 | Phase D 才删除；Phase B 靠探针判据 1 全局兜底 |

## 8. 红线对照

| 红线 | 本方案 |
|---|---|
| 修改 Stage 04 accept_experience | 不触碰 |
| 修改 approved drain 语义 | 不触碰（镜像方法独立存在） |
| 修改 RuntimePipeline 主链 | 不触碰 |
| 删除 legacy auto_apply | 保留（仅 flag 门控） |
| 开启生产 flag | 默认 False，全程不开 |
| 新建第二套 proposal/store | 零新增（复用 A/B store + Type A/B adapter） |
| 自动 approve | 无任何路径自动 approve；消费器只读 admin-approved |

## 9. 声明

本审查为只读分析 + 方案设计，未修改任何代码、未开启任何 flag、未写入任何生产数据文件。
