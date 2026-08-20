# P2.3-B.5 Growth Mutation Boundary Migration 完成报告

> 阶段：P2.3-B.5（成长红线迁移）
> 分支：develop/v1.1 ｜ HEAD：225d040 ｜ 日期：2026-08-20
> 前置文档：docs/governance/mutation_boundary_audit.md（B.1）、
> docs/governance/mutation_gateway_design.md（B.2）、
> docs/governance/mutation_gateway_implementation_report.md（B.3）、
> docs/governance/p24b4_personality_mutation_migration_report.md（B.4）、
> docs/governance/p25b5_growth_legacy_mutation_registry.md（本阶段 Phase 1 产出）

本阶段目标：将 Growth 领域绕过治理链的成长更新路径迁移到 Mutation Gateway，
解决 B.1 审计的 4 条 P1 红线（incremental_update 直接 apply、proposal 绕过审核、
ProposalStore 双轨、Growth→Personality 缺少统一 MutationRequest）。
**不是重写 Growth 系统**：保留现有 Growth API，新增迁移层，默认关闭新路径。

目标链路：事件 → Growth 分析 → MutationRequest → MutationGateway →
Proposal / Approval → Growth Apply Adapter → Personality/SelfModel 更新 → Audit。

---

## 1. 修改文件列表

| 文件 | 变更 | 说明 |
|---|---|---|
| `src/growth/mutation_adapter.py` | 新增（577 行） | GrowthMutationAdapter：GrowthEvent/GrowthProposal → MutationRequest；`growth_mutation_gateway_enabled` 迁移开关（默认 False）；Legacy Proposal → MutationProposal 统一转换（`to_mutation_proposal` / `attach_governance_linkage` / `LEGACY_STATUS_TO_CANONICAL`）；route 裁决信封（含 apply_route 约定）；DEFER 延迟队列；audit_trace 观测 |
| `src/growth/pipeline.py` | 修改（+293/-5） | `incremental_update()` 增加 flag 门控治理分支（`_route_proposal_through_gateway` / `_build_evidence_from_event` / `_park_proposal_for_review`）；未 ACCEPT 时抑制 legacy apply(event) fallback；新增 `mutation_outcomes` / `deferred_requests` 观测属性与可选 `proposal_store` 注入 |
| `src/governance/checks/identity_check.py` | 修改（1 行） | `PRIVILEGED_ACTORS` 追加 `"growth_system"`（任务书规定的标准 actor；白名单扩展≠绕过，requires_audit=True 审计强制不变） |
| `tests/test_growth_mutation_gateway.py` | 新增（437 行，11 个测试） | 覆盖 Phase 6 任务书 9 项 + 2 项附加保证（linkage 键写入、legacy→canonical 转换） |
| `docs/governance/p25b5_growth_legacy_mutation_registry.md` | 新增 | Phase 1 只读审计注册表（G-01..G-13） |
| `docs/governance/p25b5_growth_mutation_migration_report.md` | 新增 | 本报告 |

未触碰：`GrowthPipeline` 类结构、`GrowthEngine`（执行件保留）、`ProposalStore`、
`ProposalManager`、`GrowthLoop`、Growth 数据格式、`RuntimeContext`、
`memory/emotion/personality` 核心算法、`data/`（0 修改）、`src/security/permission.py`（0 修改）。

## 2. Growth legacy mutation 数量变化

Phase 1 注册表共登记 **G-01..G-13 共 13 处** legacy mutation，按 4 条 P1 红线分布：

| 红线 | 条目 | 迁移开关开启后的状态 |
|---|---|---|
| ① incremental_update 直接 apply | G-01（pipeline.py:306 proposal 零审核执行）、G-02（fallback apply(event)）、G-03（run_full_consolidation）、G-04（orchestrator.py:1327 每轮自动触发） | G-01 **关闭**：proposal 先经 Gateway 裁决，ACCEPT 才进入执行件。G-02 **关闭**（proposal 已生成时）：未 ACCEPT 则 legacy apply 被抑制（`governed_no_apply`）。G-04 触发链保持，但内部改走治理路径。G-03 保留 legacy（批量整合路径无 proposal 可转换，登记为已知限制，不在本阶段范围） |
| ② proposal 绕过审核 | G-05（growth_engine.py:232 apply_proposal 不检查 status）、G-06（apply(event) 直写）、G-07（ProposalManager 自批准 mark_approved=True）、G-08（GrowthLoop 自管状态机）、G-09（ProposalReviewer 直写 status） | G-05/G-06 保留为**纯执行件**：网关开启时唯一调用入口是 ACCEPT 后的 apply_route，执行件自身无审核的缺陷由上游裁决补偿。G-07/G-08/G-09 保留（旧 API 不删，评审环节本身合法），新产生的 proposal 一律携带治理链字段 |
| ③ ProposalStore 双轨 | Track A（proposal_store.py JSONL，5 状态词表）vs Track B（proposal/storage.py JSON 单例，另一套词表），共 5 套状态词表并存 | **统一**：`to_mutation_proposal` 转换 Legacy → canonical MutationProposal；`LEGACY_STATUS_TO_CANONICAL` 映射（cancelled→rejected）；旧 API 保留，新 proposal 带 mutation_id/request_id/trace_id/evidence |
| ④ Growth→Personality 缺统一 Request | G-10（GrowthState 直写）、G-11（PersonalityResolver 消费成长增量）、G-12（PersonalityAdapter.apply_proposal）、G-13（TraitStateUpdater） | **建立 Growth Apply Boundary**：成长域执行件 = GrowthEngine.apply_proposal；人格域执行件 = PersonalityAdapter/TraitStateUpdater；Growth 侧网关开启时只产出 MutationRequest，不越过 Request 直接调用人格执行件。G-11 漂移路径由 B.4 的 `personality_mutation_gateway_enabled` 门控，两开关独立 |

变化后：

- **迁移开关开启（`growth_mutation_gateway_enabled=True`）**：incremental_update 主链路的
  直写入口（G-01/G-02）数量 **0**，其余保留条目为执行件或评审生命周期，均登记在册。
- **迁移开关关闭（默认 False）**：所有旧路径原样保留，字节不变
  （A/B 回归证明行为一致，见 §6）。

## 3. MutationRequest 流程

```
GrowthEvent（incremental_update 评估产出 GrowthProposal）
    ↓
GrowthMutationAdapter.build_request()            # 9 字段契约；target_domain="growth"
    ↓                                            # target_path 必须形如 growth.metrics.<dim>，否则 ValueError
MutationRequest                                  # mutation_id / actor_identity="growth_system" /
    ↓                                            # target_domain="growth" / target_path / proposed_change /
MutationGateway.evaluate()                       # evidence / context_snapshot / risk_level
    ↓                                            # 五道固定顺序：Identity→Boundary→Evidence→Conflict→Audit
DecisionVerdict
    ├── ACCEPT      → apply_route → GrowthEngine.apply_proposal()（现有执行件，单变更提案）
    │                 返回 status=="applied" 即 apply_result 记账；状态变更仅发生在这一支
    ├── REJECT      → 不改变任何状态（路由信封记录 reason + audit_reference）
    ├── NEED_REVIEW → 整份 proposal 以 status="pending" 存入 ProposalStore 等待审核
    │                 （evaluator_meta._governance 携带 mutation_id/request_id/trace_id/decision/evidence）
    └── DEFER       → 同上入库 + 进入 GrowthMutationAdapter.deferred 延迟队列
```

裁决信封（route 返回）统一含：request_id / mutation_id / trace_id / target_domain /
target_path / decision / audit_reference / applied / reason / evidence_refs，
所有裁决留痕在 `GrowthPipeline.mutation_outcomes`（观测属性）。

**IdentityCheck 接入缺口处理**：任务书规定 `actor_identity="growth_system"`，
但 B.3 白名单不含该身份，会 fail-closed REJECT。处理：`PRIVILEGED_ACTORS` 追加
`"growth_system"`（与 system/gateway 同级，requires_audit=True 审计强制不变）。
这是白名单扩展，不是 Gateway 绕过。

## 4. Proposal 生命周期变化

| 环节 | 迁移前 | 迁移后（开关开启） |
|---|---|---|
| 产生 | `_build_proposal_from_evaluated` 产出 status="proposed" | 同左（API 不变），随后经 Gateway 裁决 |
| 状态进入 | 直接 `apply_proposal`（零审核） | ACCEPT 才进入执行件；NEED_REVIEW/DEFER 以 "pending" 入库待审 |
| 治理链字段 | 无 | 所有新入库 proposal 的 evaluator_meta._governance 带 mutation_id/request_id/trace_id/evidence/decision/audit_reference |
| 双轨词表 | 5 套状态词表并存 | 统一转换层 `LEGACY_STATUS_TO_CANONICAL`（cancelled→rejected 等），旧 API 不删除 |
| 重复提案 | 无拦截 | ConflictCheck 注入 exists_similar：重复 → DEFER（测试覆盖） |
| 未批准执行 | apply_proposal 不检查 status | 网关裁决在调用执行件之前；proposal 未经 ACCEPT 不可能到达执行件（测试覆盖） |

## 5. 哪些绕过路径关闭

| # | 路径 | 状态 |
|---|---|---|
| G-01 | incremental_update 直接 apply status="proposed" 的 proposal | ✅ 关闭（flag ON）：先 Gateway 裁决，ACCEPT 才执行 |
| G-02 | incremental_update fallback 直接 apply(event)（proposal 已生成时） | ✅ 关闭（flag ON）：未 ACCEPT 抑制 legacy apply，防绕过 |
| G-07 自批准 / G-08 自管状态机 / G-09 直写 status | ⚠️ 保留（旧 API 不删；评审生命周期合法） | 登记在册；新 proposal 强制治理链字段 |
| G-03 run_full_consolidation 直接 apply | ⚠️ 保留 legacy（无 proposal 可转换，范围外） | 登记在册，见注册表 §6 |
| G-05 apply_proposal 无 status 检查 | ✅ 补偿关闭：开关开启时唯一调用入口是 ACCEPT 后的 apply_route | 执行件保留为纯执行 |
| G-11 Resolver 消费成长增量漂移 | ✅ 由 B.4 `personality_mutation_gateway_enabled` 门控（两开关独立） | 跨域漂移已有上游门 |
| 无 proposal 生成时 fallback apply(event) | ⚠️ 保留（该分支没有可转换的 proposal，任务书未要求） | 登记在册 |

审计接线（Phase 5）：所有经 Gateway 的 Growth mutation 记录
request_id / mutation_id / decision / target_path / evidence_reference / apply_result；
REJECT 与执行失败同样记录（audit_reference 对 ACCEPT 与 REJECT 均非空，测试覆盖）。

## 6. 测试结果

**Phase 6 新增测试**（tests/test_growth_mutation_gateway.py）：**11/11 通过**，
覆盖任务书 9 项：

1. GrowthEvent 可生成 MutationRequest ✅
2. target_domain="growth"（非 growth 路径抛 ValueError）✅
3. Gateway reject 不产生状态变化（actor 为沙盒身份 → REJECT，执行件 0 次调用、metric 不变）✅
4. Gateway accept 才进入 apply（强证据 ACCEPT → applied；弱证据 NEED_REVIEW → 不 apply）✅
5. proposal 未批准不能执行（status="proposed" 且无证据 → 不 ACCEPT，无 apply）✅
6. duplicate proposal 被 conflict check 拦截（第二次同源 → DEFER，进入延迟队列）✅
7. audit 正常记录（requests/decisions 观测、ACCEPT 与 REJECT 均带 audit_reference）✅
8. feature flag false 保持旧行为（outcomes 空、metric 正常增长）✅
9. feature flag true 走治理路径（ACCEPT 带审计引用；弱证据 → NEED_REVIEW，状态不变，提案以 pending 入库且带治理链字段）✅

**Phase 7 A/B 回归电池**（Growth 22 + Proposal 5 + Evolution 9 + Personality mutation 1 +
Governance 5 + Context 11 = 53 个文件，B 侧另含新测试文件）：

| 运行 | 结果 |
|---|---|
| A（迁移前：pipeline.py 回退 HEAD、identity_check 回退、新文件移除） | 14 failed, 1421 passed |
| B（迁移后：当前工作区） | 14 failed, 1432 passed（+11 = 新测试文件全部通过） |

失败集合 diff（排序后逐行比较）：**完全一致 → 新增失败 = 0**。
14 个失败均为电池内既存失败（test_growth_digest_baseline_r2_5_0_b.py ×4、
test_growth_trait_apply_closed_loop.py ×6、test_growth_pipeline.py ×1、
test_engine_context.py ×1、test_phase403_gate1_identity_context_in_chain.py ×1、
test_phase40_r254_evolution_gates.py ×1），与本次迁移无关。

**前置定向回归**：governance + personality mutation + proposal 套件 224 passed，0 失败。

**运行方式**：无真实 LLM 依赖 —— 测试通过 monkeypatch 注入确定性
extractor/normalizer/validator/evaluator/meaning 解析件，全部使用临时路径。

## 7. 回滚方式

迁移开关默认 `False`，**零配置即旧行为**，回滚无需改代码：

1. **仅关闭治理路径**：`set_growth_mutation_gateway_enabled(False)`（默认值即此）。
2. **完整移除 B.5**：
   - `git checkout -- src/growth/pipeline.py`（回退 293 行增量）；
   - `src/governance/checks/identity_check.py` 删除 `PRIVILEGED_ACTORS` 中的
     `"growth_system"` 及其注释（B.5 唯一一行改动）；
   - 删除 `src/growth/mutation_adapter.py`、`tests/test_growth_mutation_gateway.py`；
   - 删除本报告与注册表文档。
3. 字节级备份已存于 `/tmp/p23b5/*.b5`（A/B 过程使用并验证恢复一致）。
4. 依赖检查：`src/growth/mutation_adapter.py` 唯一被 `src/growth/pipeline.py` 引用，
   移除 pipeline 修改后无其他模块引用（已 grep 验证）。

未破坏项：GrowthPipeline / ProposalStore / ProposalManager / Growth 数据格式全部保留；
data/ 0 修改；身份核心（IDENTITY_CORE）未触碰；无自动批准 mutation 路径
（唯一执行入口是 Gateway ACCEPT 裁决后的 apply_route）。
