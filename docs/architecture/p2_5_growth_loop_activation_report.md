# P2.5 Growth Loop Activation Report

> 阶段：P2.5（Growth Loop 最小激活）
> 目标：将已有 GrowthPipeline 接入 Runtime Cognitive Loop——只连接已有组件，不新增 Growth 模块
> 状态：**完成，A/B 全通过，按任务书停止**
> 相关文档：docs/architecture/p2_5_growth_loop_audit.md（Phase 0 审计 + Phase 1 设计）

---

## 1. 结论摘要

P2.5 的最小激活链已接通并验证：

```
Runtime 循环轨迹（真实执行产物）
  → GrowthEvaluator.evaluate（纯函数，只读）
  → GrowthProposal 生成（canonical schema，status="proposed"，内存态）
  → 审核语义（pending，由既有 approval 链承接）
  → MutationGateway（仅测试路径演示；运行时零调用）
```

- **新增独立 flag**：`growth_loop_activation_enabled`（默认 False），与 B.15 全部 flag 严格独立，
  `apply_runtime_cognitive_activation()` 绝不触碰它。
- **默认关闭**：flag=False 时 `run_growth_loop_adapter` 直接返回，与旧行为字节级等价。
- **不自动修改人格**：proposal `proposed_changes` 恒为空（元事件不伪造维度变更），
  不 apply / 不 accept / 不落盘。
- **不绕过 MutationGateway**：运行时 adapter 零次调用网关（探针实测 0 请求/0 决策/0 记录）；
  治理链由 Phase 3 测试在**测试路径**演示（gateway.evaluate 五道检查 + 审计落账）。
- **不开启无限自主成长 / 不接 ActionSystem / 不接 Scheduler**：本阶段无任何调度或主动行为。

## 2. Phase 0 审计要点

GrowthPipeline 组件**全部已存在**（GrowthEvaluator / GrowthEngine / GrowthIntegrationService /
ProposalManager / ProposalStore / MutationGateway / approval_manager / reflection_growth_bridge），
且 Stage 04（GROWTH_EVALUATION → accept_experience → ProposalStore）已在生产运行，
受 Phase 4.3 红线冻结（唯一入口 accept_experience；经历必须来自 ExperienceJournal；
禁止 GrowthAdapterImpl.evaluate；禁止直调 GrowthEngine；fail-soft）。

真正的缺口三条，本阶段补齐的是一条：**Runtime 认知轨迹 → GrowthEvaluator 的只读连接 + 独立激活 flag**。
Stage 04 冻结路径零改动；reflection_growth_bridge（config-off）与 scheduler 保持关闭。

详见 `docs/architecture/p2_5_growth_loop_audit.md`。

## 3. 实现改动（Phase 2）

| 文件 | 改动 | 性质 |
|---|---|---|
| src/runtime/cognitive_activation.py | 新增 `_growth_loop_activation_enabled=False` + getter/setter；新增 `run_growth_loop_adapter(core, ctx, *, executed_stages=None)`（只读评估 + 内存 proposal，fail-soft）；`reset_all_cognitive_activation_flags()` 一并复位新 flag；`__all__` 扩展 | 新增（untracked 模块内追加） |
| src/runtime/lifecycle_executor.py | execute() 末尾（cycle_completed 发布后、return 前）单点调用 `run_growth_loop_adapter(core, ctx, executed_stages=self.last_stage_order)` | 单点接线，可回滚 |

adapter 行为（flag=True 时）：

1. 证据收集（只读）：本轮已执行阶段（`executor.last_stage_order`，剔除 `ctx._phase_errors` 中失败项）
   + ctx._b15_reflection_record（存在时）。不使用 user_message、不调 LLM、不读写 ExperienceJournal。
2. 构造元事件 `runtime_cognitive_loop`（认知循环自身轨迹事件，与用户经历严格区分）。
3. `GrowthEvaluator().evaluate(dict副本)` → 成长元数据（实测：evidence≥17 条 →
   source_reliability=1.0 → confidence=0.575 → growth_level="context" → growth_allowed=True）。
4. growth_allowed 时生成 canonical GrowthProposal（id 确定性 `b15gl_<event_id>`，
   status="proposed"，proposed_changes=[]）挂 ctx._growth_loop_proposal。
5. 状态写 `ctx.lifecycle_trace["growth_loop_activation"]`（内存）。零文件 I/O。

## 4. 测试（Phase 3）

新增 `tests/test_b15_growth_loop.py`（9 tests，全部通过；规避 conftest 单例陷阱 token）：

| 任务书检查项 | 测试 |
|---|---|
| 1. Growth 事件可以产生 Proposal | test_growth_event_produces_pending_proposal / test_no_growth_without_evidence |
| 2. Proposal 经过治理链 | test_proposal_passes_governance_chain（测试路径 gateway.evaluate 五道检查 + InMemoryAuditWriter 1 请求/1 决策/1 记录） |
| 3. MutationGateway 是唯一修改入口 | test_mutation_gateway_is_only_mutation_entry（运行时探针零调用） |
| 4. flag 关闭无行为变化 | test_flag_off_no_behavior_change + test_growth_flag_defaults_false_and_b15_independent |
| 5. SelfModel 保护有效 | test_self_model_protected（快照 deepcopy 前后一致） |
| 6. Personality 变化可审计 | test_personality_change_auditable（零变化 + 事件 id/评估/proposal/trace 全程留痕） |
| 附加：零文件 I/O | test_growth_loop_zero_file_io（持久化 store 实例化哨兵，零触发） |

回归：B.15 Phase 2/3/5 相关 39 tests + P2.5 9 tests = 48 passed。

## 5. A/B 验证（Phase 4）

方法（沿用 Phase 4/5/6 惯例）：

- **A 状态**：全部 flag 默认 False（无插件）。
- **B 状态**：仅临时插件开启 `growth_loop_activation_enabled=True`
  （/tmp/p2b15/p25/b25flags.py，不进仓库；不开启 B.15 staging / scheduler / growth mutation）。
- 电池：19 个测试文件（B.15 全套 + lifecycle 全套 + growth 域治理链 + 新 P2.5 文件），
  546 tests / 态。
- 比较：测试失败集合 / Proposal 数量 / Mutation 调用 / Personality 变化 / data 修改。

### 5.1 测试失败集合

| 指标 | A 状态 | B 状态 |
|---|---|---|
| 结果 | 7 failed, 538 passed, 1 skipped | 7 failed, 538 passed, 1 skipped |
| 新增失败（B−A） | — | **0** |
| 消失失败（A−B） | — | **0** |
| warnings | 1676 | 1676（摘要文本逐行一致，diff=0） |

两态共有的 7 个失败均为工作区既有基线失败，与本阶段无关：

- test_phase_4_0_1_lifecycle_executor.py::test_missing_stage_method_is_noop
  （Phase 4.0.4-Pre ctx 归一化既有行为；已做对照实验：移除 P2.5 全部编辑后仍失败）
- test_growth_pipeline.py ×3（GrowthPipeline() 构造时 EventExtractor 初始化既有故障，P2.5 未触碰该模块）
- test_growth_mutation_gateway.py ×2（工作区未提交的既有测试/实现状态）
- test_runtime_full_lifecycle_verification.py::test_20_orchestrator_health_check（既有基线）

全部失败 traceback 中零处出现 P2.5 模块（grep 验证）。

### 5.2 Proposal 数量 / Mutation 调用 / Personality 变化（专用探针）

探针（/tmp/p2b15/p25/probe.py）：3 轮完整 17 阶段循环 + MutationGateway 探针挂载 +
SelfModel 快照对比，A/B 各跑一次：

| 指标 | A 状态 | B 状态 |
|---|---|---|
| 循环次数 | 3 | 3 |
| 每轮阶段执行 | 17 | 17 |
| 内存 pending Proposal 数 | 0 | 3（每轮 1 个，status="proposed"） |
| MutationGateway 请求 / 决策 / 记录 | 0 / 0 / 0 | 0 / 0 / 0 |
| Personality/SelfModel 变化 | 无（快照一致） | 无（快照一致） |

### 5.3 data 修改

- A/B 两态电池运行前后 `data/` 文件清单差异：**0**（无新增、无删除文件）。
- `data/proposals/` 全部文件 md5 两态一致（**0 差异**——无任何提案落盘）。
- 唯一内容变化：`data/chroma_db/chroma.sqlite3`（电池内既有测试的向量库二进制写入，
  A/B 两态均有且无 diff 模式差异；与 Phase 4/5/6 结论一致，非本阶段代码）。
- 探针运行前后 data/ md5 差异：A=0，B=0。

## 6. 红线合规自查

| 任务书约束 | 状态 |
|---|---|
| 1. 默认关闭 | ✅ flag 默认 False；新进程实测全关 |
| 2. 新增独立 flag | ✅ growth_loop_activation_enabled，B.15 组合不触碰 |
| 3. 不自动修改人格 | ✅ 零 apply/accept；proposed_changes 恒空 |
| 4. 不绕过 MutationGateway | ✅ 运行时零调用；唯一入口由测试路径演示 |
| 5. 不开启无限自主成长 | ✅ 每轮仅 1 个 pending proposal，无自动流转 |
| 6. 不接 ActionSystem | ✅ 未触碰 |
| 7. 不接 Scheduler | ✅ 未触碰 |
| Phase 4.3 红线 | ✅ Stage 04 冻结路径零改动；不构造 fake 经历；不调 GrowthAdapterImpl.evaluate |
| AGENTS.md §8 | ✅ 不新增模块（挂既有 B.15 adapter 体系）；数据可追溯（确定性 id）；可回滚（复位 flag） |

## 7. 回滚方式

```
from src.runtime.cognitive_activation import reset_all_cognitive_activation_flags
reset_all_cognitive_activation_flags()   # growth loop + B.15 全部复位
```

或删除 `lifecycle_executor.py` 末尾 3 行 P2.5 调用块（单点接线）。无任何落盘数据需要清理。

## 8. 停止点声明

本阶段到此结束。**未进入**：scheduler、autonomous action、self modification、
无限成长、reflection_growth_bridge 启用、ProposalStore 落盘。等待下一阶段任务书。
