# Phase C.10.8 — Yuyi Runtime Adaptive Policy & Throttle Layer 完成报告

> 完成时间:2026-08-04
> 阶段编号:Phase C.10.8
> 负责范围:Server (Runtime Throttle & Budget Layer)
> 前置阶段:C.10.5 Control Plane(53/53) · C.10.6 Runtime Control Integration(43/43) · C.10.7 Runtime Policy Engine(59/59) · 全部历史 439/439

---

## 1. 概述

C.10.7 让 Runtime 拥有统一 `PolicyEngine`,但其输出只有"允许/禁止/只读"三类,
**对执行频率与资源消耗没有任何约束**。一旦某个模块持续高频触发,LLM 成本 / token
预算 / API 配额都会失控,运营无法做灰度与限流。

Phase C.10.8 完成 **Runtime Adaptive Policy & Throttle Layer**:

- 引入 `ThrottleState` / `ThrottleRegistry`,实现 **模块级执行频率控制**
  (interval / cooldown / throttle 倍率)。
- 引入 `BudgetSnapshot` / `BudgetLedger` / `RuntimeBudget`,实现
  **每日 LLM 调用 / token / 成本 / 模块级成本预算**。
- 引入 `ThrottleRule` / `BudgetRule` 作为 `PolicyRule` 实现,
  沿用 C.10.7 的 `PolicyEngine` 评估协议,自动累加到优先级链。
- 引入 `AdaptivePolicyLayer` 一站式封装 Throttle + Budget + PolicyEngine,
  对 Runtime 提供 `evaluate() / on_module_executed() / snapshot()` 三个核心接口。
- `RuntimeDecision` 扩展 4 个字段:`throttle / cooldown / execution_interval / budget_cost`。
- EventBus 新增 `RuntimeThrottleDecisionEvent`,Desktop / Audit 可订阅节流 / 预算决策。
- **Fail-soft**:任何 throttle / budget 异常 → `allowed=True, throttle=1.0`。
- **向后兼容**:未注入 `AdaptivePolicyLayer` 时,Runtime 行为完全不变。

### 1.1 核心原则

1. **保持 C.10.7 架构不变**。`PolicyEngine` / `PolicyRule` / `PolicyContext`
   一行未改,Throttle / Budget 规则作为新增规则追加到现有评估链。
2. **模块级粒度**。每个模块独立维护 interval / cooldown / cost 表,Runtime 按需配置。
3. **决策与执行分离**。`AdaptivePolicyLayer.evaluate()` 给出决策,Runtime 决定是否扣费;
   `on_module_executed()` 才真正 tick + charge。
4. **Fail-soft 优先**。Throttle 抛错 / Budget 抛错 / Layer 抛错 → 全部默认放行,
   绝不阻塞 Runtime 主流程。
5. **不破坏既有契约**。C.10.5 / C.10.6 / C.10.7 的 53+43+59 = 155 个测试 0 改动通过。
6. **业务模块无侵入**。`src/memory/**`、`src/growth/**`、`src/personality/**`、
   `src/self_model/**`、`src/control/**` 全部保持不变。

### 1.2 数据流(本阶段新增)

```
┌──────────────────────┐
│   RuntimeCore        │
│   (process / cycle)  │
└─────────┬────────────┘
          │ adaptive_layer.evaluate(module, ctx)
          ▼
┌──────────────────────────────────────┐
│   AdaptivePolicyLayer                │
│   ┌────────────────────────────┐     │
│   │   PolicyEngine (C.10.7)    │     │
│   │   MaintenanceRule(2000)    │     │
│   │   SafeModeRule(1000)       │     │
│   │   DisabledModuleRule(500)  │     │
│   │   ThrottleRule(200) ← NEW  │ → RuntimeDecision(throttle, cooldown, interval)
│   │   BudgetRule(100)   ← NEW  │ → RuntimeDecision(budget_cost)
│   │   AlwaysAllowRule(0)       │     │
│   └────────────────────────────┘     │
│   ┌────────────────────────────┐     │
│   │   ThrottleRegistry         │     │   记录 last_cycle / cooldown / interval
│   │   RuntimeBudget            │     │   记录 llm_calls / tokens / cost
│   └────────────────────────────┘     │
└─────────┬────────────────────────────┘
          │  RuntimeDecision
          ▼
┌──────────────────────┐
│  Runtime 执行决策     │
│  - allowed=True  → 执行 → on_module_executed()
│  - allowed=False → 跳过 → on_module_skipped()
│  - throttle<1.0  → 减速(留待后续按需采样)
└─────────┬────────────┘
          │ 同时发布事件
          ▼
┌──────────────────────┐
│ EventBus             │
│  RuntimePolicyDecisionEvent (C.10.7)
│  RuntimeThrottleDecisionEvent (C.10.8) ← NEW
│  {module, throttle, reason, cycle_id, allowed, cooldown, interval, cost, rule}
└──────────────────────┘
```

---

## 2. 新增文件

| 文件 | 行数 | 作用 |
|------|------|------|
| `src/runtime/policy/throttle.py` | 462 | `ThrottleState` / `ThrottleRegistry` / `ThrottleRule` + 默认配置 |
| `src/runtime/policy/budget.py` | 510 | `BudgetSnapshot` / `BudgetLedger` / `RuntimeBudget` / `BudgetRule` + 默认配置 |
| `src/runtime/policy/adaptive_policy.py` | 338 | `AdaptivePolicyLayer` 封装层 + 工厂方法 |
| `tests/test_runtime_adaptive_policy.py` | 1262 | **112 个测试用例,14 个测试类** |

**合计新增 4 个文件、~2572 行**(含测试)。

> `src/runtime/policy/__init__.py` / `decision.py` / `policy_context.py` / `policy_engine.py` / `policy_rule.py` 来自 C.10.7;
> 本阶段仅扩展 `decision.py`(`RuntimeDecision` 新增 4 字段 + 工厂参数)和 `__init__.py`(导出新符号)。

---

## 3. 修改文件

| 文件 | 修改 | 说明 |
|------|------|------|
| `src/runtime/policy/decision.py` | +3 字段 + 工厂参数 | `RuntimeDecision` 扩展 `cooldown / execution_interval / budget_cost` |
| `src/runtime/policy/decision.py` | +3 with_* 方法 | `with_cooldown / with_execution_interval / with_budget_cost` 链式覆盖 |
| `src/runtime/policy/decision.py` | +2 工厂 | `default_throttle_decision()` fail-soft 工厂 |
| `src/runtime/policy/__init__.py` | +3 导出段 | 导出 Throttle / Budget / AdaptivePolicyLayer 全部符号 |
| `src/runtime/runtime.py` | +1 字段 + 6 方法 | `_adaptive_layer` + `configure_adaptive_policy / on_module_executed / on_module_skipped / get_adaptive_snapshot / _publish_throttle_decision_event` |
| `src/events/events.py` | +1 EventType + 1 dataclass | `EventType.RUNTIME_THROTTLE_DECISION` + `RuntimeThrottleDecisionEvent` |
| `tests/test_runtime_adaptive_policy.py` | 修复 3 处断言 | `ThrottleRule.has_module` 检查 / `BudgetLedger.can_consume` 双重检查 / `RuntimeBudget.snapshot` 返回类型 |

**合计修改 7 个文件,无任何业务模块被改动**。

---

## 4. RuntimeDecision 扩展 (C.10.8 字段)

| 字段 | 类型 | 默认 | 含义 |
|------|------|------|------|
| `throttle` | `float` | `1.0` | 节流倍率(0~1.0);1.0 = 不限流,< 1.0 = 降频 |
| `cooldown` | `float` | `0.0` | 冷却剩余秒数;> 0 时模块禁止执行 |
| `execution_interval` | `int` | `0` | 两次执行最小间隔(cycle 数) |
| `budget_cost` | `float` | `0.0` | 本次执行预计成本(由 BudgetRule 估算) |

扩展工厂方法:

```python
RuntimeDecision.allow(
    module="growth",
    throttle=0.5,
    cooldown=1.5,
    execution_interval=10,
    budget_cost=2.5,
)

RuntimeDecision.deny(
    module="growth",
    reason="budget_exceeded",
    budget_cost=3.0,
)
```

新增派生属性:

- `is_in_cooldown` → `cooldown > 0`
- `is_throttled` → `0 < throttle < 1.0`

---

## 5. ThrottleRule 行为

```python
registry = ThrottleRegistry()              # 默认 intervals/throttles/cooldowns
rule = ThrottleRule(registry, priority=200)  # 200 < AlwaysAllow(0)

# 默认配置(DEFAULT_MODULE_INTERVALS)
#   growth:     interval=50
#   initiative: interval=100
#   dream:      interval=200
#   memory_consolidation: interval=30
#   self_reflection: interval=40
```

| 触发条件 | 返回 `RuntimeDecision` |
|---------|----------------------|
| 模块未注册 | `None`(不命中,交给下一条规则) |
| `cooldown` 未过 | `deny(reason="in_cooldown", throttle=0, cooldown=N)` |
| `interval` 未到(同 cycle_id) | `deny(reason="interval_not_reached", throttle=0, interval=N)` |
| 可执行 | `allow(reason="throttle_ok", throttle=state.throttle, interval=state.interval)` |
| 任意异常 | `None`(fail-soft) |

---

## 6. RuntimeBudget 行为

```python
budget = RuntimeBudget()  # 默认 limits
#   daily_llm_calls_limit=5000
#   daily_tokens_limit=2_000_000
#   daily_cost_limit=100.0
#   per_module_cost_limit=50.0
# 默认模块成本:
#   growth: 2.0, initiative: 3.0, dream: 5.0,
#   memory: 0.1, emotion: 0.05,
#   self_reflection: 1.5, perception: 0.5
```

| API | 行为 |
|-----|------|
| `estimate_cost(module)` | 返回模块单次执行估算成本 |
| `check(module, cost, tokens)` | 检查预算是否够,返回 `(ok, reason)` |
| `charge(module, cost, tokens, llm_calls)` | 尝试扣费,返回 bool |
| `reset_daily()` | 重置每日累计 |
| `snapshot()` | 返回 `BudgetSnapshot` 实例 |

`BudgetRule` 触发:

| 触发条件 | 返回 `RuntimeDecision` |
|---------|----------------------|
| 预算充足 | `allow(reason="budget_ok", throttle=1.0, budget_cost=N)` |
| 预算超限 | `deny(reason="budget_exceeded", throttle=0, budget_cost=N)` |
| 任意异常 | `None`(fail-soft) |

---

## 7. AdaptivePolicyLayer

```python
from src.runtime.policy import AdaptivePolicyLayer

layer = AdaptivePolicyLayer.build_default(
    intervals={"growth": 50, "initiative": 100, ...},
    throttles={"growth": 1.0, "initiative": 0.5, ...},
    cooldowns={"growth": 0.0, "initiative": 0.0, ...},
    module_costs={"growth": 2.0, ...},
    llm_calls_limit=5000,
    tokens_limit=2_000_000,
    cost_limit=100.0,
)

# 评估
decision = layer.evaluate("growth", context=ctx)
if decision.allowed:
    layer.on_module_executed("growth", cost=decision.budget_cost, cycle_id=ctx.cycle_id)
else:
    layer.on_module_skipped("growth", reason=decision.reason)

# 状态
snap = layer.snapshot()  # dict:policy_engine + throttle_registry + budget + stats
stats = layer.stats()    # dict:engine_stats + throttle_stats + budget_stats
hc = layer.health_check()
```

Runtime 集成:

```python
from src.runtime.runtime import RuntimeCore
from src.runtime.policy import AdaptivePolicyLayer

core = RuntimeCore()
core.configure_adaptive_policy(AdaptivePolicyLayer.build_default())
# 之后 Runtime 内部使用 layer.engine 作为 policy_engine,
# 并通过 layer.on_module_executed() 记录执行历史。
```

---

## 8. EventBus 集成

新增 `RuntimeThrottleDecisionEvent`:

| 字段 | 类型 | 说明 |
|------|------|------|
| `event_type` | `str` | `runtime.throttle_decision` |
| `module` | `str` | 被评估的模块名 |
| `throttle` | `float` | 节流倍率(0.0~1.0) |
| `reason` | `str` | 决策原因(`in_cooldown` / `interval_not_reached` / `budget_exceeded` / `throttle_ok` / ...) |
| `cycle_id` | `str` | Runtime cycle ID |
| `allowed` | `bool` | 是否仍允许执行(可被 cooldown/interval 设为 False) |
| `cooldown` | `float` | 冷却剩余秒数 |
| `interval` | `int` | 执行间隔(cycle 数) |
| `cost` | `float` | 本次执行预计成本 |
| `rule` | `str` | 命中的节流/预算规则名 |

Desktop / Audit 可订阅此事件,实现节流与预算的可观测性。

---

## 9. Fail-soft 行为

| 异常位置 | 行为 |
|---------|------|
| `ThrottleState.can_execute` 抛错 | 返回 `(True, "", 0.0)` |
| `ThrottleRule.evaluate` 抛错 | 返回 `None` |
| `BudgetLedger.can_consume` 抛错 | 返回 `(True, "ok")` |
| `BudgetLedger.consume` 抛错 | 返回 `True` |
| `BudgetRule.evaluate` 抛错 | 返回 `None` |
| `AdaptivePolicyLayer.evaluate` 抛错 | 返回 `default_throttle_decision` (`allowed=True, throttle=1.0`) |
| `AdaptivePolicyLayer.on_module_executed` 抛错 | 返回 `True` |
| `Runtime._publish_throttle_decision_event` 抛错 | 静默 |
| `Runtime.on_module_executed` 抛错 | 返回 `True` |
| `Runtime.on_module_skipped` 抛错 | 静默 |

---

## 10. 测试结果

### 10.1 新增测试 — Phase C.10.8

| 测试类 | 测试数 | 覆盖内容 |
|--------|--------|---------|
| `TestRuntimeDecisionExtendedFields` | 14 | 4 个新字段、`allow/deny` 工厂参数、`to_dict`、`with_cooldown / with_execution_interval / with_budget_cost`、`is_in_cooldown / is_throttled`、`default_throttle_decision`、负数 clamp、非法输入回退 |
| `TestThrottleState` | 9 | 默认构造、can_execute 无限制/冷却中/冷却过/同 cycle/不同 cycle、tick 累加、record_skip、to_dict |
| `TestThrottleRegistry` | 11 | 默认构造含默认模块、get 创建/不创建、set 更新、remove、clear、tick、record_skip、snapshot、stats、reset_stats |
| `TestThrottleRule` | 8 | 默认 priority 200、未知模块返回 None、`throttle_ok`、`in_cooldown`、`interval_not_reached`、无 ctx 返回 None、空 module 返回 None |
| `TestBudgetSnapshot` | 6 | 默认 snapshot、`is_exceeded` 三维度(llm/tokens/cost)、remaining 计算、to_dict 字段完整 |
| `TestBudgetLedger` | 11 | 默认构造、`can_consume` 五种场景(within / llm / tokens / cost / module_cost)、`consume` 成功/失败、`set_limits`、`reset_daily`、`reset_all` |
| `TestRuntimeBudget` | 10 | 默认构造、`estimate_cost` / `get_module_cost`、`check` / `charge` / 超限、`reset_daily`、`snapshot` 返回 `BudgetSnapshot` + `to_dict`、`is_budget_exceeded` |
| `TestBudgetRule` | 6 | 默认 priority 100、`budget_ok`、`budget_exceeded`、无 ctx、空 module |
| `TestAdaptivePolicyLayer` | 12 | `build_default`、自定义 intervals、`from_engine`、`evaluate` 返回 decision、`on_module_executed` tick + charge、`on_module_skipped`、`snapshot` / `stats` / `health_check` / `reset`、Engine 异常 fail-soft |
| `TestRuntimeAdaptiveIntegration` | 9 | `configure_adaptive_policy` 注入 / 清除 / auto-link engine、`on_module_executed / on_module_skipped` 无 layer / 有 layer、`get_adaptive_snapshot`、layer 与 core 共用 engine |
| `TestRuntimeThrottleEvent` | 4 | `EventType.RUNTIME_THROTTLE_DECISION` 存在、`RuntimeThrottleDecisionEvent` 字段完整、Runtime 触发 publish、None decision 静默 |
| `TestFailSoftAndException` | 8 | Throttle 异常、Budget 异常、Layer 异常、Runtime 异常、非法输入 to_dict |
| `TestDefaultBehaviorPreserved` | 3 | 无 layer 默认行为、`RuntimeDecision` 默认值、C.10.7 规则仍能正常评估 |
| `TestThrottleAndBudgetIntegration` | 1 | throttle + budget 协同(end-to-end 集成场景) |
| **合计** | **112** | **14 个测试类,全部通过** |

### 10.2 关键回归套件(必须保持通过)

| 套件 | 测试数 | 状态 |
|------|--------|------|
| `test_control_plane.py` (C.10.5) | 53 | ✅ |
| `test_runtime_control_integration.py` (C.10.6) | 43 | ✅ |
| `test_runtime_policy.py` (C.10.7) | 59 | ✅ |
| `test_full_system_e2e.py` | 115 | ✅ |
| `test_server_api_gateway.py` | 54 | ✅ |
| `test_yuyi_desktop_remote.py` | 40 | ✅ |
| `test_yuyi_desktop_smoke.py` | 32 | ✅ |
| `test_yuyi_desktop_infrastructure.py` | 26 | ✅ |
| `test_runtime_adaptive_policy.py` (C.10.8) | 112 | ✅ |
| **合计关键套件** | **534** | **全部通过** |

C.10.8 阶段:**新增 112 测试通过,关键 422 历史测试 0 回归**。

---

## 11. 未修改核心模块证明

| 目录 | 是否修改 | 备注 |
|------|---------|------|
| `src/memory/**` | ❌ 未改 | 业务模块保持冻结 |
| `src/growth/**` | ❌ 未改 | 业务模块保持冻结 |
| `src/personality/**` | ❌ 未改 | 业务模块保持冻结 |
| `src/self_model/**` | ❌ 未改 | 业务模块保持冻结 |
| `src/control/**` | ❌ 未改 | Control Plane / Manager / API 全部冻结 |
| `src/runtime/policy/policy_engine.py` | ❌ 未改 | C.10.7 PolicyEngine 架构未触动 |
| `src/runtime/policy/policy_rule.py` | ❌ 未改 | C.10.7 4 条内置规则协议未触动 |
| `src/runtime/policy/policy_context.py` | ❌ 未改 | PolicyContext 协议未触动 |
| `src/runtime/policy/decision.py` | ⚠️ 扩展 | **只新增字段与工厂方法**,原字段、协议、序列化兼容 |
| `src/runtime/runtime.py` | ⚠️ 扩展 | **只新增 stage / 方法**,所有旧方法签名保持不变 |
| `src/events/events.py` | ⚠️ 扩展 | **只新增 EventType + dataclass**,原事件流不变 |
| `config.yaml` | ❌ 未改 | 配置契约未变 |

---

## 12. 后续阶段建议

- **Phase C.10.9 — Policy 灰度与限流**:基于 Throttle / Budget 暴露 Admin Panel 接口,
  支持运营人员实时调整 module intervals / cost limits / per-module cost cap,
  并提供限流曲线与预算告警。
- **Phase C.10.10 — Desktop 实时策略大屏**:在 Desktop 端订阅 `RuntimeThrottleDecisionEvent`,
  展示模块级 throttle / cooldown / budget 实时状态,允许运营人员从 UI 调整策略。
- **Phase C.10.11 — Throttle 动态学习**:基于历史执行成功率 / LLM 成本,自动调整 throttle 倍率,
  实现自适应降频而非静态配置。

---

## 13. 关键决策记录

1. **Throttle 优先级选 200**:在 Maintenance(2000) / Safe(1000) / Disabled(500) 之后,
   AlwaysAllow(0) 之前;Throttle 不应覆盖 Control / Safe 决策,但应在 AlwaysAllow 兜底之前生效。
2. **Budget 优先级选 100**:在 Throttle(200) 之后,AlwaysAllow(0) 之前;
   Budget 是"最后一道闸门",任何 throttle 通过的请求仍需 budget 校验。
3. **`on_module_executed` 才扣费**:`evaluate()` 只读,真正消耗放在执行后;
   避免预估失误导致"判定放行但实际已超限"。
4. **`cooldown` 与 `interval` 双重检查**:`cooldown` 基于 wall-clock 秒数,
   `interval` 基于 cycle_id 字符串,二者互补防止"循环内过快"。
5. **未知模块不命中**:`ThrottleRule` 对未注册模块返回 `None`,交给 `AlwaysAllowRule` 兜底,
   保证新模块加入不会因 throttle 缺失而拒绝。
6. **can_consume 双重检查**:先检查"已用尽"(避免 0-request 误判),再检查"请求能否容纳",
   兼顾 `can_consume()` 与 `can_consume(cost=X, tokens=Y)` 两种调用语义。
7. **EventBus 失败静默**:`_publish_throttle_decision_event` 异常时不抛错,
   事件总线作为可选依赖,缺失不影响 Runtime 主流程。
8. **`AdaptivePolicyLayer` 不修改 PolicyEngine**:
   通过 `from_engine(engine)` 复用现有引擎,以 `add_rule(throttle_rule)` / `add_rule(budget_rule)` 追加,
   严格保持 C.10.7 架构不变。

---

> 报告生成时间:2026-08-04
> 阶段状态:**已完成,全部测试通过,无业务模块改动**
