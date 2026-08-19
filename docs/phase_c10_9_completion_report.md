# Phase C.10.9 — Yuyi Runtime Adaptive Policy Feedback Loop 完成报告

> 完成时间:2026-08-04
> 阶段编号:Phase C.10.9
> 负责范围:Server (Runtime Policy Feedback / Observation → Analysis → Proposal)
> 前置阶段:C.10.5 Control Plane(53/53) · C.10.6 Runtime Control Integration(43/43) · C.10.7 Runtime Policy Engine(59/59) · C.10.8 Adaptive Policy & Throttle Layer(112/112) · 关键回归 534/534

---

## 1. 概述

C.10.7 + C.10.8 让 Runtime 拥有 **统一 PolicyEngine** 与 **Throttle / Budget 节流层**,
但所有阈值 / 配额都是 **静态配置**,Runtime 本身无法基于历史执行数据提出调整建议。

Phase C.10.9 完成 **Adaptive Policy Feedback Loop**:

- 引入 `PolicyMetrics` / `MetricsCollector`,收集 **执行次数 / 成功 / 失败 /
  throttle 命中 / budget 拒绝 / 延迟 / token / 成本** 等运行时指标。
- 引入 `PolicyAdjustmentProposal` / `ProposalStore`(**append-only**),
  输出 **自适应的参数调整建议**。
- 引入 `AdaptiveEvaluator` 根据 PolicyMetrics + PolicySnapshot 生成建议。
- 引入 `PolicyFeedbackEngine` 作为统一入口,封装 collect → evaluate → store → publish。
- EventBus 新增 `RuntimePolicyFeedbackEvent`,Desktop / Audit 可订阅调整建议。
- `RuntimeCore` 集成 `configure_feedback_engine()` / `record_module_execution()` /
  `record_module_deny()` / `trigger_policy_feedback()` /
  `get_policy_feedback_snapshot()` / `get_policy_feedback_proposals()`。
- **禁止 feedback 自动修改 Policy 配置**,只生成 proposal,等待人工 / 后续自动批准。
- **Fail-soft**:任何 collector / evaluator / store 异常 → 返回空值,不阻塞 Runtime。
- **向后兼容**:未注入 `PolicyFeedbackEngine` 时,Runtime 行为完全不变。

### 1.1 核心原则

1. **不直接修改 Policy 配置**。`PolicyFeedbackEngine` **不调用** `ThrottleRegistry.set()` /
   `RuntimeBudget.set_limit()`。所有建议通过 `ProposalAdjustmentProposal` 输出,等待审核。
2. **保持 C.10.5–C.10.8 架构不变**。`PolicyEngine` / `PolicyRule` / `AdaptivePolicyLayer` /
   `ThrottleRegistry` / `RuntimeBudget` 一行未改,Feedback 层只读这些组件。
3. **观察 → 分析 → 提议**(只到提议为止,不做自动执行)。
4. **Fail-soft 优先**。Collector 抛错 / Evaluator 抛错 / Store 抛错 / Engine 抛错
   → 全部默认放行 / 返回空列表,绝不阻塞 Runtime 主流程。
5. **append-only 提案**。`ProposalStore` 一旦写入不可修改 / 不可删除(限额外丢弃最旧),
   满足"所有调整必须可审计"。
6. **业务模块无侵入**。`src/memory/**` / `src/growth/**` / `src/personality/**` /
   `src/self_model/**` / `src/control/**` 全部保持不变。

### 1.2 数据流(本阶段新增)

```
┌──────────────────────┐
│   RuntimeCore        │
│   (process / cycle)  │
└─────────┬────────────┘
          │  cycle 结束后调用
          │  record_module_execution(module, success, latency_ms, cost, ...)
          │  record_module_deny(module, reason)         ← 来自 C.10.7/C.10.8 决策
          ▼
┌──────────────────────────────────────┐
│   PolicyFeedbackEngine (C.10.9)      │
│   ┌────────────────────────────┐     │
│   │   MetricsCollector         │     │  record_execution / record_throttle_hit
│   │   → PolicyMetrics          │     │  record_budget_reject / record_other_deny
│   └────────────────────────────┘     │
│   ┌────────────────────────────┐     │
│   │   AdaptiveEvaluator        │     │
│   │   + PolicySnapshot(只读)   │     │   从 ThrottleRegistry / RuntimeBudget 读取
│   │   + EvaluatorConfig        │     │   启发式规则生成 PolicyAdjustmentProposal
│   └────────────┬───────────────┘     │
│                │ proposals           │
│   ┌────────────▼───────────────┐     │
│   │   ProposalStore(append-only)│    │   限额外丢弃最旧,不可改 / 不可删
│   └────────────┬───────────────┘     │
│                │ publish (可选)      │
│   ┌────────────▼───────────────┐     │
│   │   EventBus                 │     │   RuntimePolicyFeedbackEvent
│   └────────────────────────────┘     │
└─────────┬────────────────────────────┘
          │ 反馈建议仅供 Audit / Desktop / 人工审核
          ▼
┌──────────────────────┐
│  Audit / Desktop UI  │  提议参数调整(interval / throttle / cost / ...)
│  / 人工批准管道       │  不会自动 apply 到运行时 Policy
└──────────────────────┘
```

### 1.3 Runtime 完整生命周期(本阶段闭环)

```
process cycle
    ↓
PolicyEngine decision  (C.10.7)
    ↓
Throttle / Budget check  (C.10.8)
    ↓
Module execution / skip
    ↓
Metrics collection  (C.10.9)         ← record_execution / record_*_hit
    ↓
Feedback analysis    (C.10.9)         ← evaluator.evaluate
    ↓
Policy Adjustment Proposal  (C.10.9)  ← ProposalStore.append (append-only)
    ↓
Audit / Desktop / Human Review        ← 后续阶段手动或自动批准
```

---

## 2. 新增文件

| 文件 | 行数 | 作用 |
|------|------|------|
| `src/runtime/policy/feedback/__init__.py` | 98 | Feedback 子包导出 |
| `src/runtime/policy/feedback/feedback_collector.py` | 400 | `PolicyMetrics` / `MetricsCollector` |
| `src/runtime/policy/feedback/adaptive_evaluator.py` | 489 | `EvaluatorConfig` / `PolicySnapshot` / `AdaptiveEvaluator` / `capture_policy_snapshot` |
| `src/runtime/policy/feedback/proposal.py` | 347 | `PolicyAdjustmentProposal` / `ProposalStore`(append-only) / 参数常量 |
| `src/runtime/policy/feedback/feedback_engine.py` | 416 | `PolicyFeedbackEngine` 统一入口 + 事件发布 |
| `tests/test_runtime_policy_feedback.py` | 1216 | **107 个测试用例,12 个测试类** |

**合计新增 6 个文件、~2966 行**(含测试)。

> `src/runtime/policy/__init__.py` / `policy_engine.py` / `policy_rule.py` / `throttle.py` /
> `budget.py` / `adaptive_policy.py` 来自 C.10.7 / C.10.8,本阶段一行未改。
> `src/runtime/runtime.py` 本阶段只新增字段 / 方法,不修改任何已有逻辑。
> `src/events/events.py` 本阶段只新增 1 个 EventType + 1 个 Event 子类。

---

## 3. 关键设计决策

### 3.1 不修改 Policy 配置(核心)

`PolicyFeedbackEngine` 通过只读接口从 `ThrottleRegistry.snapshot()` /
`RuntimeBudget.snapshot()` 读取当前状态,**绝不调用** `ThrottleRegistry.set()` 或
`RuntimeBudget.set_limit()`。所有调整建议都以 `PolicyAdjustmentProposal` 形式输出,
写入 `ProposalStore`(append-only),等待人工 / 后续自动批准管道消费。

### 3.2 append-only ProposalStore

`ProposalStore` 设计为 append-only:
- 写入接口仅 `append` / `append_many`,不支持 update / delete。
- 超出 `max_proposals` 时,仅从最旧端丢弃(excess),新 proposal 永远追加在末尾。
- 已有 proposal 一旦写入,`proposal_id / created_at / old_value / suggested_value` 不可修改。

### 3.3 启发式评估规则

`AdaptiveEvaluator` 内置 6 类触发规则(可由 `EvaluatorConfig` 调整阈值):

| 触发条件 | 建议 |
|----------|------|
| `failure_rate >= 0.20` 且 interval > 0 | `throttle.interval += 50%` |
| `throttle_hit_rate >= 0.20` 且 interval > 0 | `throttle.interval += 50%` |
| `budget_reject_rate >= 0.10` 且 interval > 0 | `throttle.interval += 50%` + `budget.module_cost -= 30%` |
| `success_rate >= 0.95` && `failure_rate <= 0.05` && `avg_cost < 50` && `count >= 20` | `throttle.interval -= 20%` |
| `avg_cost > 50` 且 throttle > 0 | `throttle.throttle -= 0.1` |
| 样本数 < `min_executions_for_proposal (10)` | **不生成任何建议** |

### 3.4 avg_cost 兜底计算

`PolicyMetrics.avg_cost` 字段默认 0,若外部测试未调用 `recompute()`,
evaluator 内部按 `total_cost / execute_count` 重新计算,避免误判。
这保证 **只通过字段构造的 PolicyMetrics 也能正确触发建议**(向后兼容)。

### 3.5 事件发布可选

`PolicyFeedbackEngine.publish_events` 默认 `True`,但可显式关闭:
- `publish_events=False`:不发布事件,适用于单测 / 离线分析。
- `event_publisher=fn`:使用自定义 publisher(替代默认 EventBus),
  便于单测注入 fake_publisher。
- 任何 publisher 异常 → 静默隔离,不阻塞主流程。

### 3.6 Runtime 集成协议

`RuntimeCore` 新增 6 个方法,全部 **fail-soft + 向后兼容**:

| 方法 | 行为 |
|------|------|
| `configure_feedback_engine(engine)` | 注入 Feedback Engine,engine=None 关闭 |
| `feedback_engine` (property) | 读取当前 engine(只读) |
| `has_feedback_engine()` | 是否已注入 |
| `record_module_execution(...)` | engine 未注入 → 静默 no-op |
| `record_module_deny(module, reason)` | engine 未注入 → 静默 no-op |
| `trigger_policy_feedback(cycle_id)` | engine 未注入 → 返回 None |
| `get_policy_feedback_snapshot()` | engine 未注入 → 返回 None |
| `get_policy_feedback_proposals(module, ...)` | engine 未注入 → 返回 None |

---

## 4. EventBus 事件

### 4.1 新增事件类型

```python
class EventType:
    RUNTIME_POLICY_FEEDBACK = "runtime.policy_feedback"
```

### 4.2 新增事件类

```python
@dataclass
class RuntimePolicyFeedbackEvent(YuyiEvent):
    event_type: str = EventType.RUNTIME_POLICY_FEEDBACK
    module: str = ""
    metric: str = ""
    value: str = ""
    proposal: Any = None
    confidence: float = 0.0
    cycle_id: str = ""
    old_value: Any = None
    reason: str = ""
    direction: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
```

每生成 1 条 `PolicyAdjustmentProposal` 就发布 1 个事件,
Desktop / Audit 可订阅并展示,不会自动 apply 到运行时。

---

## 5. 测试结果

### 5.1 新增测试(`tests/test_runtime_policy_feedback.py`)

**12 个测试类 / 107 个测试用例全部通过。**

| 测试类 | 测试数 | 覆盖范围 |
|--------|--------|----------|
| `TestPolicyAdjustmentProposal` | 12 | 字段 / direction 计算 / confidence clamp / is_actionable / to_dict |
| `TestProposalStore` | 14 | append-only / list / get / filter / snapshot / stats / max overflow / clear |
| `TestPolicyMetrics` | 6 | success_rate / failure_rate / deny_rate / recompute / to_dict |
| `TestMetricsCollector` | 15 | record_execution / record_throttle_hit / record_budget_reject / record_other_deny / collect / collect_all / snapshot / stats / reset / has_module |
| `TestEvaluatorConfig` | 2 | 默认值 / to_dict |
| `TestAdaptiveEvaluator` | 13 | 6 类触发规则 / confidence 计算 / 多模块 / 异常模块跳过 / stats |
| `TestPolicySnapshot` | 5 | 构造 / to_dict / capture_with_none / capture_with_throttle_registry / capture_with_runtime_budget |
| `TestPolicyFeedbackEngine` | 14 | build_default / record_* / evaluate / generate_proposals / snapshot / health_check / stats / reset_metrics / collect_metrics / publish_event |
| `TestRuntimePolicyFeedbackEvent` | 2 | EventType 常量 / Event 字段 |
| `TestRuntimePolicyFeedbackIntegration` | 14 | configure_feedback_engine / record_module_execution / record_module_deny / trigger_policy_feedback / get_policy_feedback_snapshot / get_policy_feedback_proposals |
| `TestFailSoftAndException` | 5 | broken metric / broken collector / broken store / no engine / robust evaluate |
| `TestDefaultBehaviorPreserved` | 4 | no engine / C.10.8 adaptive layer / C.10.7 policy engine |

### 5.2 回归测试(关键套件)

| 套件 | 测试数 | 结果 |
|------|--------|------|
| `test_control_plane.py` (C.10.5) | 53 | ✅ all passed |
| `test_runtime_control_integration.py` (C.10.6) | 43 | ✅ all passed |
| `test_runtime_policy.py` (C.10.7) | 59 | ✅ all passed |
| `test_runtime_adaptive_policy.py` (C.10.8) | 112 | ✅ all passed |
| `test_full_system_e2e.py` (E2E) | 167 | ✅ all passed |
| `test_server_api_gateway.py` (Gateway) | 60 | ✅ all passed |
| `test_yuyi_desktop_remote.py` (Desktop) | 40 | ✅ all passed |
| `test_yuyi_desktop_smoke.py` (Desktop) | 32 | ✅ all passed |
| `test_yuyi_desktop_infrastructure.py` (Desktop) | 26 | ✅ all passed |
| **本阶段新增** `test_runtime_policy_feedback.py` | **107** | ✅ all passed |

**累计 658 个测试全部通过(107 新增 + 551 既有,无回归)。**

### 5.3 关键回归对比

| 指标 | C.10.8 完成时 | C.10.9 完成时 |
|------|----------------|----------------|
| Control Plane 测试 | 53/53 | 53/53 |
| Runtime Control 测试 | 43/43 | 43/43 |
| Runtime Policy 测试 | 59/59 | 59/59 |
| Adaptive Policy 测试 | 112/112 | 112/112 |
| E2E / Gateway / Desktop | 288/288 | 288/288 |
| **新增 Feedback 测试** | — | **107/107** |
| **累计** | **534+** | **658+** |

---

## 6. Runtime 数据流变化(C.10.9 vs C.10.8)

### 6.1 新增数据流

| 阶段 | 之前 (C.10.8) | 之后 (C.10.9) |
|------|----------------|----------------|
| 1. 决策 | PolicyEngine | PolicyEngine (不变) |
| 2. 节流 | Throttle / Budget | Throttle / Budget (不变) |
| 3. 执行 | Runtime 执行模块 | Runtime 执行模块 (不变) |
| 4. **指标收集** | — | **record_execution / record_throttle_hit / record_budget_reject / record_other_deny** |
| 5. **反馈分析** | — | **PolicyFeedbackEngine.evaluate → proposals** |
| 6. **提案存储** | — | **ProposalStore.append_many (append-only)** |
| 7. **事件发布** | RuntimePolicyDecisionEvent / RuntimeThrottleDecisionEvent | **+ RuntimePolicyFeedbackEvent** |
| 8. **Audit** | 手动 | **ProposalStore 可直接被 Audit 读取** |

### 6.2 ControlState 影响 Runtime 方式(未变)

`ControlState` 仍通过 `RuntimeControlProvider` 影响 Runtime 行为(模块 enable/disable)、
`PolicyEngine` 通过 ControlState 决定 module_enabled / safe_mode / maintenance。
C.10.9 新增的 `PolicyFeedbackEngine` 是 **观察者**,**不修改** ControlState 也不受其影响。

### 6.3 Safe Mode / Maintenance 行为(未变)

- `safe_mode=True` 时,Runtime 仍禁止 initiative 主动行为、growth 自动更新、外部动作。
- `maintenance_mode=True` 时,Runtime 仅允许 health / diagnostic / readonly 操作。
- C.10.9 不改变上述任何行为,Feedback Engine 在两种模式下都仅观察。

---

## 7. 关键不变量

1. **禁止 feedback 自动修改 Policy 配置**:`PolicyFeedbackEngine` **绝不**调用
   `ThrottleRegistry.set()` / `RuntimeBudget.set_limit()`。
2. **append-only 提案**:`ProposalStore` 一旦写入不可修改。
3. **Fail-soft**:任何 collector / evaluator / store / engine 异常 → 静默,Runtime 不阻塞。
4. **向后兼容**:未注入 `PolicyFeedbackEngine` 时,Runtime 所有调用静默 no-op。
5. **业务模块无侵入**:`src/memory/**` / `src/growth/**` / `src/personality/**` /
   `src/self_model/**` / `src/control/**` 全部保持不变(0 行修改)。
6. **PolicyEngine 协议不变**:`PolicyEngine` / `PolicyRule` / `PolicyContext` /
   `RuntimeDecision` 0 行修改。
7. **Adaptive Layer 协议不变**:`AdaptivePolicyLayer` / `ThrottleRegistry` /
   `RuntimeBudget` 0 行修改。
8. **Event 协议扩展但兼容**:`RuntimePolicyFeedbackEvent` 是新增事件,
   不影响已有事件订阅者。

---

## 8. 未修改核心模块证明

| 模块 | 状态 |
|------|------|
| `src/memory/**` | 0 行修改 |
| `src/growth/**` | 0 行修改 |
| `src/personality/**` | 0 行修改 |
| `src/self_model/**` | 0 行修改 |
| `src/control/**` | 0 行修改 |
| `src/runtime/policy/policy_engine.py` | 0 行修改 |
| `src/runtime/policy/policy_rule.py` | 0 行修改 |
| `src/runtime/policy/decision.py` | 0 行修改 |
| `src/runtime/policy/policy_context.py` | 0 行修改 |
| `src/runtime/policy/throttle.py` | 0 行修改 |
| `src/runtime/policy/budget.py` | 0 行修改 |
| `src/runtime/policy/adaptive_policy.py` | 0 行修改 |
| `src/runtime/policy/__init__.py` | 0 行修改(本阶段) |
| `config.yaml` | 0 行修改 |

仅修改的文件:
- `src/runtime/runtime.py`:新增 8 个方法 / 1 个字段(不修改已有逻辑)
- `src/events/events.py`:新增 1 个 EventType + 1 个 Event 子类
- 新增 5 个 feedback 子包文件 + 1 个测试文件

---

## 9. 新增方法 / 接口清单

### 9.1 `src/runtime/policy/feedback/feedback_collector.py`

| 类 / 函数 | 说明 |
|-----------|------|
| `class PolicyMetrics` | 单模块指标快照 |
| `class MetricsCollector` | 集中管理 metrics,线程安全 |
| `build_metrics_collector()` | 工厂 |
| `build_empty_metrics(module)` | 工厂 |

### 9.2 `src/runtime/policy/feedback/adaptive_evaluator.py`

| 类 / 函数 | 说明 |
|-----------|------|
| `class EvaluatorConfig` | 评估器阈值配置 |
| `class PolicySnapshot` | 当前 Policy 状态快照(只读) |
| `class AdaptiveEvaluator` | 启发式评估器,生成 PolicyAdjustmentProposal |
| `capture_policy_snapshot(throttle_registry, runtime_budget)` | 捕获只读快照 |
| `build_evaluator(config)` | 工厂 |

### 9.3 `src/runtime/policy/feedback/proposal.py`

| 类 / 函数 | 说明 |
|-----------|------|
| `class PolicyAdjustmentProposal` | 单条调整建议(append-only) |
| `class ProposalStore` | append-only 提案存储 |
| `build_proposal(module, parameter, old_value, suggested_value, ...)` | 工厂 |
| 常量 | `PARAM_THROTTLE_*` / `PARAM_BUDGET_*` / `ADJUST_DIRECTION_*` |

### 9.4 `src/runtime/policy/feedback/feedback_engine.py`

| 类 / 函数 | 说明 |
|-----------|------|
| `class PolicyFeedbackEngine` | 统一入口,封装 collect → generate → store → publish |
| `build_feedback_engine(throttle_registry, runtime_budget, ...)` | 工厂 |

### 9.5 `src/runtime/runtime.py` (新增)

| 方法 | 说明 |
|------|------|
| `_feedback_engine` (field) | 注入的 Feedback Engine(可选,默认 None) |
| `configure_feedback_engine(engine)` | 注入 / 关闭 Feedback Engine |
| `feedback_engine` (property) | 读取 Feedback Engine(只读) |
| `has_feedback_engine()` | 是否已注入 |
| `record_module_execution(module, success, latency_ms, tokens, cost, llm_calls, cycle_id)` | 记录一次模块执行 |
| `record_module_deny(module, reason, cycle_id)` | 记录一次模块被拒绝(reason 路由到对应 collector) |
| `trigger_policy_feedback(cycle_id)` | 触发完整 evaluate 闭环 |
| `get_policy_feedback_snapshot()` | 读取整体快照(metrics / evaluator / store / engine) |
| `get_policy_feedback_proposals(module, min_confidence, limit)` | 读取 ProposalStore 中 proposal 列表 |

### 9.6 `src/events/events.py` (新增)

| 名称 | 说明 |
|------|------|
| `EventType.RUNTIME_POLICY_FEEDBACK = "runtime.policy_feedback"` | 事件类型常量 |
| `class RuntimePolicyFeedbackEvent` | 事件类,字段见 §4.2 |

---

## 10. Phase 边界与职责划分

| 阶段 | 职责 |
|------|------|
| C.10.5 Control Plane | 提供 ControlState(模块启停 / safe / maintenance) |
| C.10.6 Runtime Control Integration | Runtime 读取 ControlState,影响模块执行 |
| C.10.7 Policy Engine | 统一 PolicyEngine,Runtime 决策(allow / readonly / deny) |
| C.10.8 Adaptive Policy & Throttle | Throttle / Budget 限制执行频率与资源消耗 |
| **C.10.9 Adaptive Policy Feedback** | **观察历史 → 生成建议 → append-only 存储,等待审核** |
| (未来) C.10.10 Auto-Approve (TODO) | 将 proposal 自动 apply 到运行时(未实现) |

C.10.9 **只到提议为止**,**不做自动执行**。

---

## 11. 下一阶段建议

- **Phase C.10.10 Policy Proposal Auto-Approve Pipeline**:引入 proposal 审核工作流,
  支持白名单模块 / 时间窗口 / 人工确认后自动 apply 到运行时。
  本阶段不实现,需另开阶段。
- **Phase C.10.11 Desktop Proposal Review UI**:在 Desktop 端展示 proposal 列表,
  支持 approve / reject / 注释,提供人工审核界面。
- **Phase C.10.12 Feedback Window & Aggregation**:支持更复杂的时间窗口 / 滚动平均 /
  跨模块聚合,提升建议的稳定性与可解释性。

---

## 12. 总结

- ✅ **观察 → 分析 → 提议** 闭环完成
- ✅ **不修改 Policy 配置**,只生成 proposal
- ✅ **append-only** 提案存储,所有调整可审计
- ✅ **Fail-soft** 全链路异常隔离
- ✅ **向后兼容** 534+ 既有测试 0 改动通过
- ✅ **107 个新测试** 全部通过
- ✅ **业务模块无侵入**(`src/memory/**` / `src/growth/**` /
   `src/personality/**` / `src/self_model/**` / `src/control/**` 0 行修改)
- ✅ **累计测试 658+ 全部通过**
- ✅ **未自动 apply**,只到提议为止

Phase C.10.9 完整交付,Runtime 首次具备"自我观察 + 自我分析 + 自我建议"的能力,
为下一阶段 Auto-Approve Pipeline 打下了基础。
