# P2.6 Phase B-1 实施文档

> 任务：P2.6 Phase B-1 实施（治理统一开关 `governance_unification_enabled` 打开时，Legacy Growth 写入路径进入治理流程）
> 依据：docs/architecture/p2_6_phase_b_final_decision.md
> 实施日期：2026-08-21
> 结论：完成。18/18 新测试通过；回归无新增失败；真实 data 快照验证通过。

---

## 1. 修改文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/growth/pipeline.py` | 生产修改（白名单） | `_is_growth_governed()` + incremental_update / run_full_consolidation 截断 + growth_records 门控 |
| `src/orchestrator.py` | 生产修改（白名单） | Step 14.6 执行层 B 态降级 + B-store 持久化 + 零 apply |
| `tests/test_governance_unification_phase_b.py` | 新增测试（白名单） | 18 个用例 |
| `docs/architecture/p2_6_phase_b_implementation.md` | 本文档（白名单） | — |

白名单之外零修改。**注意**：`git diff`（相对 HEAD）在这两个生产文件上显示的增量远大于本任务改动——HEAD 中尚不存在既有未提交的 B.5 网关路由等工作（约 285 行），本任务增量以 §2 清单为准；两文件行尾均为纯 LF，无格式噪音。

## 2. 修改点

### 2.1 src/growth/pipeline.py

| # | 位置 | 修改 |
|---|------|------|
| P-1 | import 区 | 新增 `from src.governance.governance_unification import is_governance_unification_enabled` |
| P-2 | 模块级 | 新增私有 helper `_is_growth_governed()` = `is_growth_mutation_gateway_enabled() or is_governance_unification_enabled()`（任一开关关闭时对旧行为零影响） |
| P-3 | incremental_update proposal 路由条件 | `if _is_growth_governed():` 强制走 `_route_proposal_through_gateway`——统一开启时即使 B.5 开关关闭也强制网关路由，legacy `apply_proposal` 不再作为直写路径 |
| P-4 | incremental_update growth_records（F1 门控） | `if not is_governance_unification_enabled() or proposal_applied:` 才执行 `apply_evaluated` + `growth_records.add`。A 态 / 仅 B.5 态行为不变（无条件追加）；B 态仅 ACCEPT-applied 追加（pending/deferred/rejected/failed 均不追加） |
| P-5 | incremental_update else 分支 | 新增 `elif is_governance_unification_enabled():` fail-closed 分支——proposal 未生成 / 未 ACCEPT / 未进入 gateway 时禁止 legacy `apply(event)` 直写，返回 `{"status": "deferred", "reason": "governance_unification_no_apply", "proposal_id": None}` |
| P-6 | run_full_consolidation | ① growth_records 同款 F1 门控（B 态不追加）；② `apply(event)` 在统一开启时替换为 fail-closed deferred（无 gateway 路由，禁止 direct apply）。A 态兼容不变 |

### 2.2 src/orchestrator.py

| # | 位置 | 修改 |
|---|------|------|
| O-1 | import 区 | 新增 `from src.governance.governance_unification import is_governance_unification_enabled` |
| O-2 | Step 14.6 | 治理循环抽出为 `_process_self_model_governance(_growth_result)`（便于治理测试；A 态行为与抽出前逐字节等价；返回计数 dict 为纯增量，生产调用方不读取） |
| O-3 | Step 14.6 分支 | B 态：`auto_apply` 降级为 approval_required（`_persist_self_model_governance_proposal` + enqueue + **零 apply**）；`approval_required` 同样持久化 + enqueue。A 态：完全保持旧行为（auto_apply 立即 apply，approval_required 仅内存队列） |
| O-4 | 新增 `_persist_self_model_governance_proposal()` | 生成 B-store `GrowthProposal(proposal_type="self_model", status="pending", source="orchestrator", affected_dimensions=record.affected_dimensions, metadata={"self_model_proposal": proposal.to_dict(), "governance_decision": {...}, "source": "legacy_step_14_6"})` 并 `storage.save()`。仅落盘、绝不 apply；异常静默隔离 |

### 2.3 治理约束确认（任务书禁止项）

- 禁止自动 apply：✅ B 态零 apply（O-3）
- 禁止新建 proposal/store/model：✅ 复用 A-store（park）/ B-store（持久化）/ 现有模型
- 禁止修改 RuntimeCore / Stage 04 / approved drain：✅ 零触碰
- 禁止删除 legacy auto_apply：✅ 仅条件分支，A 态路径完整保留
- `flag=False` 行为完全等价旧版本：✅ §3 测试 + §4 快照

## 3. A/B 测试结果

### 3.1 新测试（tests/test_governance_unification_phase_b.py）

`18 passed in 1.60s`，分组：

| 组 | 用例 | 验证点 |
|----|------|--------|
| 开关矩阵 | test_is_growth_governed_matrix | 4 种 flag 组合 |
| A/B 兼容 | test_a_state_proposal_path_unchanged | flag=False：apply_proposal 直写 + records 无条件追加 + delta/写入路径与旧版一致 |
| | test_a_state_legacy_fallback_unchanged | proposal 构建失败仍走 legacy apply(event) |
| | test_b5_mode_gateway_records_preserved | 仅 B.5 开启（统一关闭）：NEED_REVIEW 不直写但 records 仍追加（B.5 语义不被 Phase B-1 破坏） |
| B 态治理 | test_b_state_accept_forced_through_gateway | 统一开启 + B.5 关闭仍强制路由；ACCEPT 才执行与追加 |
| | test_b_state_needs_review_no_direct_apply | direct_apply==0、proposal 停车 +1、records 不追加、状态快照不变 |
| | test_b_state_rejected_no_park_no_records | REJECT 不直写/不停车/不追加 |
| | test_b_state_deferred_park_no_records | DEFER 停车 +1、不直写/不追加 |
| | test_b_state_proposal_none_fail_closed | proposal 构建失败禁止 fallback direct apply |
| | test_b_state_applied_path_through_route_spy | ACCEPT 才追加 records 且与 applied 列表一致 |
| run_full_consolidation | A 态兼容 / B 态禁止直写 + F1 门控 | 2 用例 |
| Step 14.6 | A 态 auto_apply 立即 apply / A 态 approval_required 仅队列 / B 态 auto_apply 降级（零 apply + B-store self_model pending + metadata 载荷）/ B 态 approval_required 持久化 / deny 跳过 | 5 用例 |
| fallback | test_fallback_orchestrator_path_still_governed | 模拟 RuntimeCore 失败进入 orchestrator：Step 14.5（pipeline）与 Step 14.6 两层均治理化、零直写 |

### 3.2 回归

| battery | 结果 |
|---------|------|
| test_governance_audit_probe.py + test_growth_mutation_gateway.py + test_growth_pipeline.py + test_phase402_gate3_fallback.py | **34 passed** |
| test_b13_self_model_mutation_governance.py + test_admin_governance.py | **48 passed** |
| test_growth_phase_382b.py + test_phase_385_step5_dual_update.py + test_governance_proposal_manager.py | 25 passed + **12 failed（既有基线）** |

12 个失败与 Phase A 基线完全一致（3× 382b + 9× 385），且抽检 `test_proposal_success_skips_apply`（41.5s）与 `test_incremental_update_produces_proposal`（108s）**单独运行均通过**——为已知的批量测试污染/flakiness，非本任务引入（Phase A 会话已用 stash A/B 实验证实同一失败集合）。

## 4. data 快照结果（真实文件验证）

方法：独立临时工作目录（不触碰仓库 `data/`），真实 `GrowthPipeline` / `GrowthEngine` / `GrowthState` / A-store `ProposalStore`，仅桩化治理层上游（事件提取/归一/评估）与 LLM 语义解析；B 态使用受控 NEED_REVIEW 裁决（真实网关默认会 ACCEPT，无法演示「未 approve 不变」，受控裁决用于数据层验证，路由行为由 §3 单测覆盖）。

| 判据 | 结果 |
|------|------|
| A 态 legacy 链正常写入 | ✅ events=1、records=1，growth_state.json 落盘（hash `0cf2dc…`） |
| B 态 growth_state.json hash 不变 | ✅ `0cf2dc…` 不变（未 approve 状态不变） |
| B 态 personality_growth_history.json hash 不变 | ✅ `c65f63…` 不变（F1 门控在真实文件层面生效——resolver 人格影响路径被切断） |
| B 态 proposal 数量增加 | ✅ `data/proposals/proposals.jsonl` 新增 1 条 parked proposal（唯一变化文件） |
| B 态 gateway 被调用 | ✅ route_calls=1（B.5 开关关闭时由统一开关强制路由） |
| B 态 direct_apply==0 | ✅ probe 记录数保持 1（仅 A 态那条），B 态零新增 |

## 5. 未解决问题（登记，非本任务范围）

1. **self_model approved 消费器（Option B）未实现**——Phase B-1 交付 P3 为「提案化 + B-store 持久化 + 人工审核 + 零 apply」；apply 消费器按最终裁决推迟 Phase C（RuntimeCore 红线）。
2. **runtime_core.py:2072 dormant auto_apply 路径**——parent `accept_growth_proposal` 零生产调用方；Phase C 加 flag-gated 截断。
3. **B 态 self_model 提案积压**——Phase B 窗口内 approved 提案只记录不 apply；admin 可拒绝，Phase C 消费器补齐。
4. **probe direct_apply 语义边界**——B 态下若真实网关裁决 ACCEPT，执行件经 `apply_route` 写入时仍会被 Phase A 探针记为 `direct_apply`（观察点不区分「治理批准后的执行」与「绕过」）；「direct_apply==0」判据适用于非 ACCEPT 路径，ACCEPT 执行属于治理内 apply。
5. **run_full_consolidation 在仅 B.5 开启（统一关闭）时仍直写**——B.5 迁移层既有范围（该方法本就未接 B.5 路由），本任务仅按统一开关门控，未扩大 B.5 行为面。
6. **git diff 噪声**——两个生产文件相对 HEAD 的 diff 含既有未提交工作（B.5 网关路由等）；本任务增量以 §2 清单为准。
