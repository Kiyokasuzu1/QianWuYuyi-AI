# P2.6 Phase B-1 冻结基线（Freeze Baseline）

> 性质：只读分析产物（本任务未修改任何代码、未开启任何 flag；仅新增本文档）
> 日期：2026-08-21
> 上游文档：p2_6_phase_b_final_decision.md → p2_6_phase_b_implementation.md → p2_6_phase_b_validation_review.md → **本文档**
> 用途：Phase C 实施前的权威基线。Phase C 验收时以此为准判定「是否引入基线外变化」。

---

## 1. 冻结范围与 git 快照

### 1.1 Phase B-1 文件清单（冻结对象）

| 类别 | 文件 | 状态 |
|------|------|------|
| 生产修改 | src/growth/pipeline.py | 修改（P-1~P-6） |
| 生产修改 | src/orchestrator.py | 修改（O-1~O-4） |
| 新增测试 | tests/test_governance_unification_phase_b.py | 新增 18 用例 |
| 新增文档 | docs/architecture/p2_6_phase_b_final_decision.md | 新增 |
| 新增文档 | docs/architecture/p2_6_phase_b_implementation.md | 新增 |
| 新增文档 | docs/architecture/p2_6_phase_b_validation_review.md | 新增 |
| 新增文档 | docs/architecture/p2_6_phase_b1_freeze_baseline.md | 本文档 |

### 1.2 工作区整体快照（`git diff HEAD --stat`，2026-08-21）

```
19 files changed, 1709 insertions(+), 274 deletions(-)
```

其中 Phase B-1 增量 = 实施文档 §2 清单（pipeline.py 的 P-1~P-6、orchestrator.py 的 O-1~O-4）。**其余全部为既有未提交工作**（早于 Phase B-1，含 B.5 网关、Phase A 探针、Phase 4.0.x 主链等；判定证据：HEAD 中不存在 `_route_proposal_through_gateway`，`git show HEAD:src/growth/pipeline.py | grep -c _route_proposal_through_gateway` = 0）。此外存在大量 untracked 文件（src/governance/ 整目录、.cache/ 诊断产物、多个既有测试文件）与用户自行删除的 UI 资源（D 状态项），均与 Phase B-1 无关。

### 1.3 flag 状态（冻结时点）

| flag | 位置 | 冻结值 |
|------|------|--------|
| governance_unification_enabled | src/governance/governance_unification.py:11（模块级） | **False**（config.yaml 无此键） |
| is_growth_mutation_gateway_enabled（B.5） | 模块级 | **False**（config.yaml 无此键） |
| growth_apply_drain_enabled | config.yaml:146 | true（**既有 personality drain，先于 Phase B-1，不属于本阶段产物**） |
| growth_apply_drain_limit | config.yaml:147 | 5 |

---

## 2. Phase B-1 最终架构状态

```
User Input → Orchestrator.process()
  → Step 14.5 GrowthPipeline.incremental_update
       A 态（flag=False，默认）: evaluate → apply_proposal/apply(event) 旧路径，行为不变
       B 态（统一治理开启）: 全部 ChangeItem 强制路由 MutationGateway
           ACCEPT → 唯一执行入口 _apply_route → engine.apply_proposal(single)
           NEED_REVIEW/DEFER → A-store park（pending + 治理链接）
           REJECT → 丢弃
       growth_records.add 门控（F1）: B 态仅 proposal ACCEPTED 且 mutation 成功才追加
  → Step 14.6 SelfModel 治理链（_process_self_model_governance）
       A 态: auto_apply → SelfModelUpdater.apply_proposal；approval_required → 内存队列 enqueue
       B 态: auto_apply 降级为 approval_required ——
           B-store GrowthProposal(type=self_model, status=pending, metadata=完整载荷) 持久化
           + 内存队列 enqueue + **零 apply**
  → 后续主链（Stage 04 / RuntimeCore stage 13 drain）未被触碰
```

**治理裁决边界（冻结事实）**：统一治理模式下，legacy 成长链唯一写入口 = gateway ACCEPT 执行；self_model 唯一变化 = B-store pending 提案。B-store approved 的消费器只有 personality drain（personality-only）；self_model approved **无消费器**（Phase C 待建）。

---

## 3. 已完成能力

| 编号 | 能力 | 落点 |
|------|------|------|
| P-1 | `_is_growth_governed()` 开关聚合（统一治理 OR B.5） | pipeline.py |
| P-2/P-2b | incremental_update 强制 gateway 路由；pending/defer/reject 绝不 legacy apply；proposal 失败 fail-closed 返回 deferred | pipeline.py |
| P-4（F1） | growth_records 门控：B 态仅 applied 追加 | pipeline.py |
| P-6 | run_full_consolidation B 态抑制 direct apply（A 态兼容） | pipeline.py |
| O-2/O-3 | Step 14.6 执行层抽取 + B 态提案化（self_model pending 落 B-store，零 apply） | orchestrator.py |
| O-4 | `_persist_self_model_governance_proposal`（metadata 完整载荷，供 Phase C 反序列化） | orchestrator.py |
| T-1 | 18 用例（A 态等价 ×3、B.5 保持、B 态闭环 ×7、run_full ×2、Step14.6 ×4、fallback 双链路） | 新测试文件 |
| D-1~D-4 | 最终决策 / 实施 / 验收审查 / 冻结基线 四份架构文档 | docs/architecture/ |

---

## 4. 未完成能力（登记在案）

| 编号 | 缺口 | 现状证据 | 归属 |
|------|------|----------|------|
| U1 | self_model approved 消费闭环缺失 | B-store approved 消费方全仓唯一 = runtime_core.py:6438 drain（6452 行 `proposal_type != PERSONALITY → skip`）；PROPOSAL_TYPE["SELF_MODEL"] 全仓仅 orchestrator.py:2282 一处写入、零处读取 | **Phase C** |
| U2 | runtime_core.py:2072 dormant auto_apply | `_self_model_updater_internal.apply_proposal` 仅在 `accept_growth_proposal` 链内可达；`accept_growth_proposal` 全仓零外部调用方（grep 仅 1801 内部调用 + 2024 docstring） | Phase C 顺手门控 / 可延迟 D |
| U3 | orchestrator B 态内存队列无消费端 | orchestrator.py:436 自建 SelfModelApprovalQueue，2235/2246 仅 enqueue，全仓无 approve 调用方（RuntimeCore 2175 的 queue.approve 属另一实例的建议链） | Phase D 审视（B-store 账本为权威，队列为暂存） |
| U4 | run_full_consolidation 在仅 B.5 开启（统一关闭）时仍直写 | pipeline.py:809/813 门控只判统一开关 | B.5 迁移范围（登记） |
| U5 | src/admin/selfmodel_consumer.py dormant 基础设施去留 | 全仓零生产调用方 | Phase D 清理决策 |
| U6 | 双体系 Proposal 长期合并 | 最终决策 §1：B-store 权威、A-store 生成层 | Phase D+ |

---

## 5. 明确禁止修改区域（Phase C 期间冻结）

| 层级 | 对象 | 冻结理由 |
|------|------|----------|
| 文件级 | src/growth/pipeline.py | Phase B-1 冻结产物 |
| 文件级 | src/orchestrator.py | Phase B-1 冻结产物 |
| 文件级 | src/personality/self_model_governance.py（policy RULES） | 红线：治理规则不动 |
| 文件级 | src/growth/growth_engine.py | Phase A 探针状态冻结 |
| 文件级 | src/personality/self_model_updater.py | 只读复用，零改动 |
| 行级 | runtime_core.py:6390-6524 `drain_approved_growth_proposals` 方法体 | 红线：personality drain 语义不变 |
| 行级 | Stage 04（growth_integration.accept_experience 及运行时主链 stage 顺序） | 红线：RuntimePipeline 契约稳定 |
| 行级 | runtime_core.py:6341 `_stage_13_self_model_persistence` 既有主链持久化 | 既有 Phase 4.0.x 主链，范围外 |
| 数据级 | 双体系 Proposal schema（A/B 两套，不新增第三套） | 最终决策裁决 |
| 配置级 | 不得新增 governance_unification / B.5 的 config 键、不得改默认值 | 红线：生产 flag=False |

---

## 6. Phase C 开始前必须保持的不变量

1. **legacy auto_apply 不删除**：orchestrator.py:2238-2240 A 分支 `apply_proposal` 保留；pipeline.py:599/663 legacy apply_proposal / apply(event) 保留。
2. **flag=False 行为完全一致**：45 快电池 + 2 真实链单测保持通过（见 §7）。
3. **RuntimePipeline 契约稳定**：stage 顺序、Stage 04、lifecycle_executor 零改动。
4. **personality drain 语义不变**：personality-only filter、applied 回写、fail-soft、config 键名/默认值均不动。
5. **proposal 双体系不新增第三套**：Phase C 消费器只读复用 B-store + 既有 adapter。
6. **B 态 fail-closed**：统一治理模式下 proposal 失败/未进 gateway 时禁止 direct apply、禁止 growth_state.json 写入（P-2 契约）。

---

## 7. 测试基线结果（冻结时点 2026-08-21）

| 组 | 命令 | 结果 |
|----|------|------|
| 快电池 | `pytest tests/test_governance_unification_phase_b.py tests/test_governance_audit_probe.py tests/test_growth_mutation_gateway.py -q` | **45 passed, 1.90s**（18 + 16 + 11） |
| 真实链 A | `pytest tests/test_phase_385_step5_dual_update.py::test_proposal_success_skips_apply` | **1 passed, 45.71s**（单跑） |
| 真实链 B | `pytest tests/test_growth_phase_382b.py::test_incremental_update_produces_proposal` | **1 passed, 148.73s**（单跑） |

备注：批量 battery 下 382b/385 出现的 12 个失败与 Phase A 基线一致、单跑全过（测试批量污染，非回归）；数据快照基线见实施文档 §4（B 态仅 proposals.jsonl 变化，growth_state.json / personality_growth_history.json hash 不变，probe direct_apply=0）。
