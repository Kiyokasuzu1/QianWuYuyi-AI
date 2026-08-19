# Phase 6.0 Runtime Growth Integration 架构变化报告

> QianWuYuyi-AI — Runtime Growth Pipeline 闭环接入
> 2026-07-30

## 1. 目标回顾

将 Phase 5.5.1 Hotfix + Phase 5.5.2 Stability 沉淀的稳定组件（Authority 隔离、Mirror 安全、Memory Action 隔离、RetryQueue、StateMachine、ProposalSyncManager、RetryWorker、ProposalLifecycleManager、GrowthRateLimiter）连接成 **真实运行的 Runtime Growth 闭环**。

核心原则：
1. 不重构现有模块
2. 不修改 RuntimeCore 核心逻辑
3. 所有人格修改必须经过 PersonalityAdapter
4. 所有 Proposal 必须经过 LifecycleManager

## 2. 修改文件清单

### 2.1 新增文件

| 文件 | 行数 | 职责 |
|---|---|---|
| [runtime_growth_pipeline.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/runtime/pipeline/runtime_growth_pipeline.py) | 644 | Runtime Growth Pipeline 主体：6 阶段串联 + 异常隔离 + 持久化 + 分步调试 |
| [runtime/pipeline/__init__.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/runtime/pipeline/__init__.py) | 21 | 导出 RuntimeGrowthPipeline |
| [test_phase_6_0_integration.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/tests/test_phase_6_0_integration.py) | 1121 | 67 个集成测试 |
| [check_phase_6_0_ast.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/scripts/check_phase_6_0_ast.py) | 124 | AST 检查 + Authority 边界验证 |

### 2.2 修改文件

| 文件 | 变更点 |
|---|---|
| [approval_manager.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/growth/approval_manager.py) | +2 参数（lifecycle_manager / apply_hook）；approve / reject / modify 中接入生命周期状态转换 |
| [personality_adapter.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/personality/personality_adapter.py) | +1 参数（growth_limiter）；apply_proposal 前调用 _filter_proposal_by_limiter；通过后调用 limiter.record() |

### 2.3 未修改文件（满足"不修改 RuntimeCore 核心逻辑"）

- [runtime_core.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/runtime/runtime_core.py) — **零修改**
- 所有 Phase 5.5.1 / 5.5.2 模块 — 保持向后兼容

## 3. 架构变化：Runtime Growth Pipeline

### 3.1 闭环流程

```
Experience
   ↓
Memory.store_experience()
   ↓
Reflection（真实引擎 / 兜底最小化）
   ↓
GrowthAdapter.propose_growth()
   ↓
ProposalLifecycleManager.record_transition(pending→approved)
   ↓
PersonalityAdapter.apply_proposal()
   ↓   ├─ GrowthRateLimiter.check() [Phase 6.0 限流]
   ↓   └─ Path Validation         [Phase 5.5.1]
   ↓
ProposalLifecycleManager.record_transition(applying→applied | failed)
```

### 3.2 三阶段交付

#### Phase 1：ProposalLifecycleManager 接入 ApprovalManager

**接口扩展**（向后兼容）：
```python
ApprovalManager(
    growth_adapter,
    history_path=None,
    lifecycle_manager=None,   # NEW
    apply_hook=None,          # NEW
)
```

**状态机**：
```
pending ──approve──> approved ──apply start──> applying
                                                │
                                                ├──success──> applied
                                                └──failure──> failed

pending ──reject──> rejected
```

实现位置：[approval_manager.py:117-218](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/growth/approval_manager.py#L117-L218)

关键方法：
- `_run_lifecycle_after_approve(proposal, actor)` — 驱动 pending→approved→applying→applied/failed
- `_run_lifecycle_reject(proposal, actor)` — 驱动 pending→rejected
- `modify_proposal(...)` — 修改并触发完整生命周期

异常隔离：lifecycle.record_transition 失败仅 warning，不阻塞 approval 流程（保持审批权威性）。

#### Phase 2：GrowthRateLimiter 接入 PersonalityAdapter

**接口扩展**（向后兼容）：
```python
PersonalityAdapter(
    runtime_context=None,
    growth_limiter=None,  # NEW
)
```

**流程**：
1. `apply_proposal(proposal)` 首先调用 `_filter_proposal_by_limiter()`
2. 对每个 ChangeItem 计算 `delta = after - before`
3. 对每个合法 trait 调用 `limiter.check(trait, delta, confidence)`
4. decision ∈ {allow, warn, deny}：
   - **deny** → 从 proposal 移除该 change
   - **warn** → 保留但在 envelope 中记录
   - **allow** → 保留
5. 若全部被 deny → envelope.note = "rate_limited_all_traits_denied"
6. 应用通过后调用 `limiter.record()` 消耗配额

实现位置：[personality_adapter.py:117-235](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/personality/personality_adapter.py#L117-L235)

异常隔离：limiter.check 异常时保守通过（已 log warning），不阻塞人格修改。

#### Phase 3：Runtime Growth Pipeline 主体

**位置**：[runtime_growth_pipeline.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/runtime/pipeline/runtime_growth_pipeline.py)

**核心类**：
| 类 | 职责 |
|---|---|
| `PipelineStage` | 6 阶段枚举：EXPERIENCE / MEMORY / REFLECTION / PROPOSAL / LIFECYCLE / PERSONALITY |
| `StageStatus` | 阶段状态：OK / FAILED / SKIPPED |
| `StageResult` | 单阶段结果：stage / status / started_at / finished_at / inputs / outputs / error |
| `PipelineRun` | 一次完整运行：run_id / stages / final_status / proposal_id / metadata |
| `RuntimeGrowthPipeline` | Pipeline 主体：run_cycle / 6 个 step_* / snapshot / persistence |

**核心方法**：
- `run_cycle(experience, auto_approve=False, metadata=None)` — 一站式闭环
- `start_run(experience, metadata)` — 启动 + 缓存 experience
- `step_memory(run)` — 调用 memory.store_experience
- `step_reflection(run)` — 调用 reflection_engine / 最小化兜底
- `step_proposal(run)` — 调用 growth_adapter.propose_growth
- `step_lifecycle(run, auto_approve)` — 审批 + 应用人格
- `step_personality(run)` — 实际 apply（已由 lifecycle 驱动，作为兜底）
- `finish_run(run)` — 标记结束 + 持久化
- `get_recent_runs(limit)` / `get_run(run_id)` — 历史查询
- `snapshot()` — 统计快照
- `clear_runs()` — 清空历史

**降级矩阵**：

| 缺失组件 | 行为 |
|---|---|
| 无 memory_adapter | 跳过 memory 阶段（StageStatus.SKIPPED） |
| 无 reflection_engine | 自动生成最小化 ReflectionInsight |
| 无 growth_adapter | 跳过 proposal / lifecycle / personality |
| 无 approval_manager | 跳过审批，应用人格 |
| 无 lifecycle_manager | 退化为无生命周期阶段记录 |
| 无 personality_adapter | 跳过人格应用 |
| 任意 stage 异常 | 隔离到该 stage，不中断后续阶段 |

**持久化**：
- `history_path` 可选，启用后每次 `finish_run()` 自动写入
- 不可序列化对象（RuntimeExperience 等）自动转换为 `{_id, _type}` 或 `{_repr, _type}`
- `load_runs()` 自动恢复

## 4. 测试覆盖

### 4.1 测试统计

| 阶段 | 测试类 | 测试数 |
|---|---|---|
| Phase 1: Lifecycle 集成 | TestPhase1LifecycleIntegration | 12 |
| Phase 2: RateLimiter 集成 | TestPhase2RateLimiterIntegration | 12 |
| Phase 3: Pipeline 端到端 | TestPhase3RuntimeGrowthPipeline | 24 |
| 集成: 端到端 | TestPhase6EndToEnd | 8 |
| 回归: 5.5 兼容性 | TestPhase6RegressionSafety | 6 |
| Schema: 快照 | TestPhase6SchemaSnapshot | 5 |
| **合计** | **6 classes** | **67 tests** |

### 4.2 关键测试点

- ✅ pending → approved → applying → applied（成功）
- ✅ pending → approved → applying → failed（apply 异常）
- ✅ pending → rejected（拒绝）
- ✅ 缺 lifecycle_manager 时向后兼容
- ✅ limiter 单次 delta 超限 → DENY
- ✅ limiter 每日配额用尽 → DENY
- ✅ limiter 低 confidence → DENY
- ✅ limiter 异常时保守通过
- ✅ Pipeline 6 阶段完整串联
- ✅ 各 stage 异常隔离
- ✅ 缺组件时降级
- ✅ 持久化与加载
- ✅ 与 Phase 5.5.1 / 5.5.2 兼容性
- ✅ 完整闭环：Experience → Memory → Reflection → Proposal → Approve → Personality
- ✅ 关闭 lifecycle/limiter 时降级
- ✅ Schema 快照测试

## 5. Authority 边界验证

### 5.1 RuntimeCore Authority 边界

- RuntimeCore（[runtime_core.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/runtime/runtime_core.py)） — **零修改**
- Phase 6.0 文件中 `runtime_core` 引用数：**0**（AST 自动验证）

### 5.2 PersonalityAdapter 必经性

- Pipeline 不直接调用 `PersonalityResolver` / `SelfModelStore`
- 所有人格修改统一经 `personality_adapter.apply_proposal()`
- Pipeline 引用 personality_adapter：**已确认**

### 5.3 LifecycleManager 接入验证

- `approval_manager.py` 含 `lifecycle_manager` 参数与 `record_transition` 调用：**已确认**
- approval_record.metadata 包含 phase6 字段：lifecycle_state、envelope

### 5.4 GrowthRateLimiter 接入验证

- `personality_adapter.py` 含 `growth_limiter` 参数与 `_filter_proposal_by_limiter` 方法：**已确认**
- envelope.rate_limit.decisions 包含每个 trait 的 decision

## 6. 向后兼容性

| 已有接口 | Phase 6.0 行为 |
|---|---|
| `ApprovalManager(growth_adapter, history_path)` | ✅ 仍可用（lifecycle_manager / apply_hook 默认 None） |
| `PersonalityAdapter(runtime_context)` | ✅ 仍可用（growth_limiter 默认 None） |
| `GrowthRateLimiter()` 默认参数 | ✅ 完全不变 |
| `ProposalLifecycleManager` 所有 API | ✅ 完全不变 |
| 所有 Phase 5.5.1 / 5.5.2 测试 | ✅ 175 个全通过 |

## 7. 失败处理策略

| 阶段 | 失败行为 |
|---|---|
| Experience 注入失败 | PipelineRun 标记 failed，stage.error 记录 |
| Memory store 失败 | stage 标记 failed，pipeline 继续 |
| Reflection 失败 | 使用最小化兜底 |
| Proposal 生成失败 | lifecycle / personality 自动跳过 |
| Approve 失败 | approval envelope 包含 error，pipeline 标记 failed |
| Personality apply 失败 | lifecycle 记录 failed 状态，pipeline 标记 failed |
| Pipeline 持久化失败 | 仅 log error，不中断运行 |

## 8. 性能特征

- 内存：每次 PipelineRun 包含 6 个 StageResult；`_max_runs = 200`，超出从头部丢弃
- 持久化：每次 finish_run 全量写入（如需增量可后续优化）
- 限流：O(N) per proposal，N = proposed_changes 数量

## 9. 后续可扩展点（不在 Phase 6.0 范围）

1. Pipeline scheduler / cron 触发（目前需手动调用 run_cycle）
2. Pipeline 事件订阅（目前无 EventBus，符合"不引入 EventBus"约束）
3. 增量持久化（目前全量写）
4. Pipeline 嵌套（子 Pipeline 复用）
5. Approval 自动批处理
