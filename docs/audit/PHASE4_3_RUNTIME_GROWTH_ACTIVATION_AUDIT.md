# Phase 4.3 — Runtime Growth Activation 审计报告

日期：2026-08-12
性质：**纯审计，未修改任何代码**（任务卡第一阶段）

---

## 0. 一句话结论

Stage 4 空转的根因不是"缺模块"，而是**三件套错位**：
装配的适配器（`GrowthAdapter`，反思→提案）没有 `evaluate`；
有 `evaluate` 的适配器（`GrowthAdapterImpl`）模式违规且未装配到 RuntimeCore；
而 Stage 4 handler 用静默 `getattr` 兜底，**断点无任何日志、无任何错误记录**——
空转是隐形的。

---

## 1. 当前 Growth 生命周期状态

### 活着的部分（生产真实运行）

| 链路 | 位置 | 状态 |
|---|---|---|
| Reflection → GrowthProposal 存储 | `runtime_core.py:1442-1471` → `GrowthAdapter.store_insight` | ✅ 活 |
| Proposal 查询 / accept / reject API | `runtime_core.py:1533-1610` | ✅ 活 |
| Scheduler tick 评估 pending proposals | `autonomous_scheduler.py:174` → `get_growth_proposals` + `evaluate_growth_proposal` | ✅ 活 |
| **RelationshipEvent → digest → Evaluator → Proposal(pending) → Approval** | 4.2-D `RelationshipEvidenceAdapter`（Stage 3 子步骤触发） | ✅ 活（本阶段刚接通） |

### 死的部分

| 链路 | 状态 |
|---|---|
| **Stage 4 每轮生命循环的成长评估** | ❌ 静默空转（断点见 §2） |
| `GrowthAdapterSpec.evaluate/submit`（Phase 3.7.1 设计接口） | ❌ `raise NotImplementedError`，从未装配 |
| `GrowthAdapterImpl`（Phase 3.7.2 实现） | ⚠️ 有实现但**模式违规**（§4），仅注册在 legacy runtime.py |
| `ctx.growth_proposals` 槽位（runtime_context.py:59） | ❌ 生产路径从未被填充 |

**现状精确描述：羽依"会成长"（4.2-D 事件驱动链路活着），
但 Stage 4 这个"生命循环里的成长心跳"从未跳动过一次。**

---

## 2. Stage 4 断点定位

### 调用链

```
RuntimeCore.process()
  ↓ LifecycleExecutor.execute()（17 阶段唯一调度点）
  ↓ Stage 4 GROWTH_EVALUATION
  ↓ runtime_core.py:4807 _stage_04_growth_evaluation(event, ctx)
      ga = self.growth_adapter          # runtime_core.py:393 装配
      ev = getattr(ga, "evaluate", None)
      if not callable(ev): return       # ←★ 断点：静默 return
```

### 断点的三层错位

**① 装配层**：`runtime_core.py:393` 装配的是
`src/runtime/adapters/growth_adapter.py::GrowthAdapter`——
它是 Phase 3.5 的"反思洞察→提案存储"实现，
只有 `store_insight/list_proposals/accept/reject`，**没有 `evaluate`**。

**② 接口层**：同文件的 `GrowthAdapterSpec`（Phase 3.7.1 设计的统一入口）
定义了 `evaluate/submit`，但两者都 `raise NotImplementedError`，
且**从未被装配到任何地方**。

**③ 静默层**：handler 用 `getattr(ga, "evaluate", None)` + `not callable → return`，
**不写 `_stage_errors`、不打日志、不记 ctx._phase_errors**。
这解释了为什么之前所有审计都没"看到"失败——根本没有失败信号可看。
另外注意：即使 `evaluate` 存在，`ev(ctx)` 的返回值也被**丢弃**，
`ctx.growth_proposals` 永远不会被填充（invoke 契约也未完成）。

### 附带发现：两个 RuntimeCore 并存（如实上报）

| 类 | 位置 | 生产地位 |
|---|---|---|
| `RuntimeCore(ModuleBase)` | `runtime_core.py:204` | **生产**（RuntimeBridge:137 实例化；4.1/4.2 全部工作的载体；LifecycleExecutor 17 阶段） |
| `RuntimeCore`（旧） | `runtime.py:215` | 仅 Orchestrator 最后兜底（orchestrator.py:367，共享桥失败时才实例化） |

旧 `runtime.py::_invoke_growth_stage`（:2697）反而是"完整"实现：
`adapter.evaluate(event) → submit(proposal) → 回填 ctx.growth_proposals`，
其注册表（:238）挂的是 `GrowthAdapterImpl`——但这个实现本身违规（§4）。
**即：唯一活着的 evaluate 实现，长在一个基本不运行的类上，且模式是错的。**

---

## 3. Stage 4 可用输入清单

执行到 Stage 4 时，ctx 已持有：

| 输入 | 来源 | 可用性 |
|---|---|---|
| 本轮用户消息 / event | Stage 1 + `ctx.user_message` | ✅ |
| `ctx.retrieved_memories`（记忆证据，≤20 条） | Stage 2（4.1 修复后真实填充） | ✅ |
| `ctx.emotion_snapshot` | Stage 3 | ✅ |
| `ctx.relationship_snapshot` | Stage 3 子步骤（4.2-B） | ✅ |
| 在途 experience（`_building_experience_id` + 4.1D experience→memory 映射） | Stage 1 事件处理 | ⚠️ 当轮 experience **尚未 finish**（落 memory 在行动分派后），只能拿到 id 与上一轮记录 |
| SelfModel 上下文 | **Stage 9 才构建** | ❌ 结构性缺失（阶段顺序决定） |
| relationship evidence | 4.2-D 已在 Stage 3 记录路径直接转发 Growth | ➖ 不经 Stage 4（独立链路） |

**按任务卡要求：只记录缺口，不补数据。**
两个结构性事实需在 4.3 设计时接受：
1. 当轮经历在 Stage 4 时刻只能以"在途 id + 用户消息原文"形式存在；
2. SelfModel 上下文永远不可能出现在 Stage 4（除非调整阶段顺序——不建议，
   17 阶段表是冻结契约）。

---

## 4. Proposal 安全边界审计

### ❌ GrowthAdapterImpl（不能接线，模式违规）

`src/runtime/adapters/impl/growth_adapter_impl.py::evaluate`：

```
event → GrowthEngine.apply(event_dict)   # ← 先直接改 GrowthState！
      → 把 metrics 包成 GrowthProposal    # ← 再补一张提案
      → confidence=0.6（硬编码）
      → evidence_ids=[event_id]（形式锚定，非真实证据）
```

三条违规：
1. **先改状态后补提案**——与「Event → GrowthProposal → Validation → Persistence」
   原则完全相反，提案沦为既成事实的收据；
2. **绕过 Normalizer/Validator/GrowthEvaluator**（digest 红线全部失效）；
3. **绕过 Approval**（提案不经过 ProposalManager/审批流）。

**结论：4.3 不得把 GrowthAdapterImpl 接进 Stage 4。**
（它目前只挂在 legacy runtime.py 注册表，影响面受控。）

### ✅ 合规模板已存在（4.2-D 验证过）

```
RelationshipEvidenceAdapter（4.2-D，22/22 测试）
  normalize → validate → match → GrowthEvaluator（红线硬编码）
  → GrowthIntegrationService.process_event → Proposal(pending) → Approval
```

### ✅ 更大的现成资产：GrowthIntegrationService.accept_experience

`growth_integration.py:396`——**本身就是"一条 memory record →
完整 digest 管线 → pending proposal → Approval → Evolution"的封装**，
含 EligibilityFilter 时机门控 + 0.8 置信门槛 + auto_accept=False 冻结，
且红线文档化（永不 apply personality / 永不调 GrowthState.apply）。
它是 ExperienceBridge 的现有 Growth 入口，**接口契约恰好就是
"经历记录 → 提案"，与 Stage 4 的语义完全同构**。

---

## 5. 可复用资产汇总（按复用价值排序）

| # | 资产 | 说明 |
|---|---|---|
| 1 | `GrowthIntegrationService.accept_experience(record)` | 完整合规管线现成入口，红线文档化 |
| 2 | `ctx.growth_proposals` 槽位（runtime_context.py:59） | Stage 4 输出落点已存在，含 `has_growth()` 查询 |
| 3 | 4.2-D `RelationshipEvidenceAdapter` 管线模式 | 同形态接线刚被 22 条测试验证 |
| 4 | `_stage_04_growth_evaluation` handler 骨架 | control_blocked 检查已就位，只需替换中间段 |
| 5 | 4.1D experience→memory 映射（`memory_adapter.get_experience_memory_id`） | 拿"上一轮已落盘经历记录"的现成通道 |
| 6 | ProposalManager fingerprint 去重 | 天然防重复提案 |
| 7 | legacy runtime.py `_invoke_growth_stage` 的 ctx 回填写法 | 契约参考（evaluate→proposals→填 ctx） |

---

## 6. 最小接线方案（建议，待顾问批准）

### 方案：Stage 4 = "上一轮经历的成长判断"

```
Stage 4 GROWTH_EVALUATION
  ↓ 取上一轮已落盘的 experience memory record
    （memory_adapter experience 映射 / journal，4.1D 资产）
  ↓ GrowthIntegrationService.accept_experience(record)   【现成，不改】
    → EligibilityFilter → Normalizer → Validator → Evaluator
    → Proposal(pending) → Approval
  ↓ 把 process_result 的 proposal 写入 ctx.growth_proposals
  ↓ 全程 fail-soft；输出永远只是 canonical GrowthProposal
```

设计要点：

1. **为什么是"上一轮"而不是"当轮"**：Stage 4 时刻当轮 experience 未落盘，
   上一轮记录是真实、持久、可审计的证据。一轮延迟换来证据诚实。
2. **不需要新模块**：handler 内组合既有资产（装配层加一个懒加载
   `GrowthIntegrationService`，与 4.2-D 同一模式）。
3. **不碰 GrowthAdapterImpl / GrowthAdapter 现有职责**：
   GrowthAdapter 继续管 reflection→proposal 存储；Stage 4 走 integration service。
4. **顺手补可观测性**：handler 的静默 `getattr` 兜底至少写 debug 日志 +
   `ctx._phase_errors`（当前断点隐形的直接原因）。
5. **明确不做**：不调 `GrowthEngine.apply`；不调整 17 阶段顺序；
   不把 SelfModel 数据引入 Stage 4；不弱化 0.8 置信门槛。

### 备选（不推荐）：当轮用户消息即时评估

直接拿 `ctx.user_message` 构造临时 record。证据未落盘、与 4.2-D
关系事件路径职责重叠（同一文本可能被两条链路重复评估），且
"经历还没发生就评估"语义上不诚实。仅在顾问明确要求时考虑。

---

## 7. 风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| R1 误接 GrowthAdapterImpl（直接 apply 模式） | **高** | 本报告 §4 已标记禁用；实施任务卡应明文禁止 |
| R2 同一经历被 Bridge 与 Stage 4 重复评估 | 中 | ProposalManager fingerprint 去重兜底；可加"已评估"标记（实施期定） |
| R3 多数轮次被 EligibilityFilter / 0.8 门槛拦截，Stage 4"看起来还是空转" | 低 | 这是设计使然（成长本来稀有）；观测指标应看 `ctx._phase_errors` 消失 + 周期性 pending proposal 出现，而非每轮都出提案 |
| R4 accept_experience 内 GrowthEligibilityFilter 对单轮记录的观察窗口判定 | 中 | 实施期先用真实记录跑通观测，再决定是否调整输入选择（不改 filter 本身） |
| R5 legacy runtime.py 旧 RuntimeCore 的 GrowthAdapterImpl 路径仍在（orchestrator 兜底场景） | 低 | 本阶段不动；列入 legacy 退役清单（与 trust+0.05 同列） |
| R6 双 RuntimeCore 并存导致的认知混淆 | 中 | 如实上报顾问，建议未来单独立项收敛；4.3 只动生产类 |

---

## 8. 4.3 最终目标对照

任务卡目标：

```
每次经历 → Runtime 感知 → Growth 判断 → 生成候选变化 → 等待审批
```

方案达成度：

- **每次经历** → Stage 4 每轮取上一轮经历记录 ✅
- **Runtime 感知** → 进入 17 阶段生命循环，不再是事件驱动的旁路 ✅
- **Growth 判断** → Evaluator 红线 + EligibilityFilter 时机判断 ✅
- **生成候选变化** → canonical GrowthProposal 填入 `ctx.growth_proposals` ✅
- **等待审批** → auto_accept=False 冻结，Approval 流既有 ✅

即：羽依从"拥有成长模块的程序"变成"具有成长循环的 AI"——
成长成为每轮心跳的一部分，而不是只在特定事件旁路里发生。

---

## 9. 给顾问的请示点

1. 是否批准 §6 方案（Stage 4 评估**上一轮**已落盘经历）？
2. 是否明文确认：**禁止**将 GrowthAdapterImpl 接入 Stage 4？
3. handler 静默兜底补可观测性（debug 日志 + `_phase_errors` 记录）
   是否算入 4.3 范围？（不改行为，只让断点可见）
4. 双 RuntimeCore 并存是否列入后续收敛议题（本阶段不动）？
