# Phase C.10.7 — Yuyi Runtime Policy Engine 完成报告

> 完成时间:2026-08-04
> 阶段编号:Phase C.10.7
> 负责范围:Server (Runtime Policy Decision Layer)
> 前置阶段:C.10.5 Control Plane(49/49) · C.10.6 Runtime Control Integration(43/43) · 全部历史 380/380

---

## 1. 概述

C.10.5 让 **ControlState** 存在、C.10.6 让 **Runtime** 主动读取 ControlState,
但 Runtime 中直接判断 `safe_mode / maintenance_mode / module_enabled` 的代码
分散在若干 `_is_module_allowed` 风格的方法里,不利于扩展与审计。

Phase C.10.7 完成 **统一 Runtime Policy Decision Layer**:

- 引入 `PolicyEngine`,作为 Runtime 调用策略的唯一入口。
- 引入 `PolicyRule` 协议 + 内置规则 `SafeModeRule / MaintenanceRule / DisabledModuleRule / AlwaysAllowRule`。
- 引入 `RuntimeDecision` 统一决策输出(`allowed / readonly / throttle / mode / reason / metadata`)。
- 引入 `PolicyContext` 统一评估上下文(`ControlState 快照 + module + cycle_id + system_info`)。
- EventBus 新增 `RuntimePolicyDecisionEvent`,决策可被 Desktop / Audit 订阅。
- Runtime 通过 `policy_engine.evaluate(module, context)` 一次性获得决策。
- **Fail-soft**:任何异常 / 未注入 PolicyEngine 时,Runtime 默认放行,所有旧测试不回归。

### 1.1 核心原则

1. **Runtime 不再直接判断控制逻辑**。
   所有 "允许/拒绝/只读" 决策都委托给 `PolicyEngine`。
2. **规则独立、可插拔**。
   任何实现 `PolicyRule` 协议的对象都可以被注册到 PolicyEngine,业务规则无侵入。
3. **Fail-soft 优先**。
   规则异常 / 引擎异常 / context 异常 → 一律默认 `allow`,绝不阻塞 Runtime 主流程。
4. **保持旧行为**。
   未注入 PolicyEngine 时,所有现有 E2E / Control / Desktop 测试 0 改动通过。
5. **Runtime 不依赖 Desktop**。
   PolicyEngine 不引用 yuyi_desktop,事件流为可选。
6. **审计友好**。
   `RuntimeDecision.metadata` 自动写入 `matched_rule / fallback / rule_name`,便于回溯。

### 1.2 数据流(本阶段新增)

```
┌──────────────────────┐
│   RuntimeCore        │
│   (process / cycle)  │
└─────────┬────────────┘
          │ policy_engine.evaluate(module, context)
          ▼
┌──────────────────────┐
│   PolicyEngine       │
│  ┌────────────────┐  │
│  │ MaintenanceRule│ priority=2000  │ → RuntimeDecision(allowed=False, mode=maintenance)
│  ├────────────────┤  │
│  │ SafeModeRule   │ priority=1000  │ → RuntimeDecision(allowed=False/True, mode=safe)
│  ├────────────────┤  │
│  │ DisabledMod    │ priority=500   │ → RuntimeDecision(allowed=False, reason=module_disabled)
│  ├────────────────┤  │
│  │ AlwaysAllow    │ priority=0     │ → RuntimeDecision(allowed=True)  ← fallback
│  └────────────────┘  │
└─────────┬────────────┘
          │  RuntimeDecision
          ▼
┌──────────────────────┐
│  Runtime 执行决策     │
│  - allowed=True  → 正常执行
│  - allowed=True, readonly=True → 只读
│  - allowed=False → 跳过
└─────────┬────────────┘
          │ 同时发布事件
          ▼
┌──────────────────────┐
│ EventBus             │
│  RuntimePolicyDecisionEvent
│  {module, allowed, reason, cycle_id, mode, rule}
└──────────────────────┘
```

---

## 2. 新增文件

| 文件 | 行数 | 作用 |
|------|------|------|
| `src/runtime/policy/__init__.py` | 99 | 导出决策/上下文/规则/引擎 |
| `src/runtime/policy/decision.py` | 243 | `RuntimeDecision` 数据结构 + 工厂 + 序列化 |
| `src/runtime/policy/policy_context.py` | 285 | `PolicyContext` + `from_runtime()` 工厂 |
| `src/runtime/policy/policy_rule.py` | 346 | `PolicyRule` 协议 + 4 条内置规则 |
| `src/runtime/policy/policy_engine.py` | 449 | `PolicyEngine` 主入口 + 单例管理 |
| `tests/test_runtime_policy.py` | 725 | **59 个测试用例,7 个测试类** |

**合计新增 6 个文件、~2147 行**(含测试)。

---

## 3. 修改文件

| 文件 | 修改 | 说明 |
|------|------|------|
| `src/events/events.py` | +1 EventType, +1 dataclass | 新增 `RuntimePolicyDecisionEvent` |
| `src/runtime/runtime.py` | +1 字段, +2 公共方法, +1 内部 stage | 集成 PolicyEngine,新增 `_is_module_allowed_via_policy` 与事件发布 |

修改内容已严格隔离在 Runtime 内的 `__init__` 注入字段 + 2 个新增方法,
**未触碰** 任何业务模块、control 状态层、control 管理层、control API 层。

---

## 4. RuntimeDecision 数据结构

```python
@dataclass
class RuntimeDecision:
    module: str = ""                            # 被评估模块
    allowed: bool = True                        # True=执行,False=跳过
    reason: str = ""                            # 决策原因(供 audit / debug)
    mode: str = "normal"                        # normal / safe / maintenance / unknown
    readonly: bool = False                      # 仅允许读、禁止副作用
    throttle: float = 1.0                       # 节流倍率(1.0=全速, 0=停止)
    metadata: Dict[str, Any] = field(default_factory=dict)  # {rule, matched_rule, fallback, ...}
```

提供工厂方法:
- `RuntimeDecision.allow(module, reason, mode, metadata)`
- `RuntimeDecision.deny(module, reason, mode, metadata)`
- `RuntimeDecision.readonly_decision(module, reason, mode, metadata)`
- `default_allow_decision(module)` — fail-soft 兜底

---

## 5. PolicyContext

```python
@dataclass
class PolicyContext:
    module: str = ""
    cycle_id: str = ""
    runtime_mode: str = "unknown"
    is_safe_mode: bool = False
    is_maintenance_mode: bool = False
    control_state: Dict[str, Any] = field(default_factory=dict)
    module_enabled: Optional[bool] = None
    runtime_context: Optional[Any] = None
    system_info: Dict[str, Any] = field(default_factory=dict)
    request: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = "1.0"
```

工厂方法 `PolicyContext.from_runtime(module, runtime_context, control_state, control_context, cycle_id, request, extra)`:
- 自动从 `control_context.is_safe_mode()` / `is_maintenance_mode()` 推 mode
- 自动从 `control_context.is_enabled(module)` / `allow(module)` 推 `module_enabled`
- 自动从 `runtime_context._cycle_id` 推 `cycle_id`
- 自动生成 `system_info`(hostname / pid / captured_at)

---

## 6. PolicyRule 内置规则

| 规则 | priority | 触发条件 | 输出 |
|------|----------|----------|------|
| `MaintenanceRule` | 2000 | `is_maintenance_mode=True` | 模块在白名单(runtime/health/diagnostic/readonly)→ allow,否则 deny |
| `SafeModeRule` | 1000 | `is_safe_mode=True` | blocked(growth/initiative/dream)→ deny,readonly(memory/emotion)→ readonly,其他 → allow |
| `DisabledModuleRule` | 500 | `module_enabled=False` | deny(reason=module_disabled) |
| `AlwaysAllowRule` | 0 | 默认兜底 | allow(reason=always_allow) |

每条规则:
- **只读** `PolicyContext`,不修改任何字段
- **异常隔离** — `evaluate()` 抛错时返回 `None`,由 Engine 兜底
- **可定制** — 构造时可传 `blocked_modules` / `allowed_modules` 自定义集合

`PolicyRule` 是 `runtime_checkable` Protocol,任何实现 `name / priority / evaluate()` 的对象都可注册。

---

## 7. PolicyEngine

```python
engine = PolicyEngine()                       # 默认含 4 条规则
engine.add_rule(MyRule(), priority=3000)      # 注入自定义规则
engine.add_callback(lambda d, ctx: ...)        # 决策回调(异常隔离)

decision = engine.evaluate("memory", context) # 核心评估
```

特性:
- **按 priority 降序** 评估,第一条返回非 `None` 的规则胜出
- **Fail-soft**:任何异常 → `default_allow_decision`
- **线程安全**:`RLock` 保护
- **统计**:`evaluate_total / evaluate_allow / evaluate_deny / evaluate_readonly / fallback_allow / evaluate_errors`
- **批量评估**:`evaluate_all(modules) / which_allowed() / which_denied()`
- **模块级单例**:`get_default_policy_engine() / reset_default_policy_engine_for_testing()`
- **上下文隔离**:`_build_per_module_context` 在 evaluate 内部为每个 module 复制独立 context,避免 `evaluate_all` 中的 module 字段污染

---

## 8. Runtime 集成

新增字段(默认 None,完全向后兼容):
```python
class RuntimeCore:
    def __init__(self, ..., policy_engine: Optional[Any] = None):
        self._policy_engine = policy_engine
```

新增方法:
```python
def configure_policy_engine(self, engine: Any) -> None: ...
def _is_module_allowed_via_policy(self, module_name, ctx=None) -> Optional[bool]: ...
def _evaluate_policy_decision(self, module, ctx, control_snapshot=None) -> RuntimeDecision: ...
def _publish_policy_decision_event(self, decision, cycle_id="") -> None: ...
def get_policy_decision(self, module, ctx) -> Optional[RuntimeDecision]: ...
def get_policy_engine(self) -> Any: ...
def has_policy_engine(self) -> bool: ...
def is_policy_evaluation_failed(self) -> bool: ...
def get_policy_stats(self) -> Dict[str, int]: ...
```

**关键点**:
- `policy_engine=None` → `_is_module_allowed_via_policy` 返回 None → Runtime 走旧路径(`_is_module_allowed`),完全保持默认行为
- `_evaluate_policy_decision` 复用传入的 `PolicyContext`(若已是 PolicyContext),否则从 runtime_context 拼装
- 每次 process cycle 后,决策写入 `ctx._policy_decisions`,可通过 `get_policy_decision(module, ctx)` 查询

---

## 9. EventBus 集成

`src/events/events.py` 新增:

```python
class EventType:
    RUNTIME_POLICY_DECISION = "runtime.policy_decision"

@dataclass
class RuntimePolicyDecisionEvent(YuyiEvent):
    event_type: str = EventType.RUNTIME_POLICY_DECISION
    module: str = ""
    allowed: bool = True
    reason: str = ""
    mode: str = "normal"
    cycle_id: str = ""
    readonly: bool = False
    throttle: float = 1.0
    rule: str = ""
```

- Runtime 在 process 时若 `policy_engine` 已注入,自动发布此事件
- 失败完全隔离,事件发布异常不影响 Runtime 主流程
- 任何订阅者(Desktop / Audit / 监控)都可监听

---

## 10. 测试结果

### 10.1 新增测试(本阶段)

**`tests/test_runtime_policy.py`** — **59 个测试用例 / 7 个测试类,全部通过**

| 测试类 | 用例数 | 覆盖范围 |
|--------|--------|----------|
| `TestRuntimeDecisionBasic` | 9 | 决策默认值、allow/deny/readonly 工厂、to_dict、throttle、metadata、mode 规范化、default_allow_decision |
| `TestPolicyContextBasic` | 7 | 默认构造、from_runtime、safe / maintenance / disabled 模式、to_dict、is_module_allowed |
| `TestPolicyRules` | 13 | AlwaysAllow / SafeMode / Maintenance / Disabled 各规则、规则异常隔离、默认 priority |
| `TestPolicyEngine` | 13 | 默认规则构造、无规则构造、正常/safe/maintenance 评估、优先级、回调、回调异常隔离、evaluate_all、which_allowed/denied、engine 异常 fail-soft、统计、remove_rule |
| `TestRuntimeCorePolicyIntegration` | 11 | 未注入 policy_engine 不变、configure_policy_engine、policy_decisions 写入 ctx、get_policy_decision、policy_engine 异常 fail-soft、is_module_allowed_via_policy(unset/set/safe_mode) |
| `TestEventBusIntegration` | 2 | RuntimePolicyDecisionEvent 定义、Runtime.process 触发事件 |
| `TestDefaultBehaviorPreserved` | 4 | 无 policy_engine 时 ctx._policy_decisions 为空、无事件发布、_is_module_allowed 不变、policy_engine 在 __init__ 中可选、默认单例 |

### 10.2 测试命令与结果

```bash
# 新增测试
python -m pytest tests/test_runtime_policy.py -q --no-header
# 59 passed, 38 warnings in 0.10s ✓

# 关键回归测试套件
python -m pytest tests/test_runtime_policy.py \
                 tests/test_runtime_control_integration.py \
                 tests/test_control_plane.py \
                 tests/test_yuyi_desktop_remote.py \
                 tests/test_yuyi_desktop_smoke.py \
                 tests/test_yuyi_desktop_infrastructure.py \
                 tests/test_full_system_e2e.py \
                 tests/test_server_api_gateway.py -q --no-header
# 439 passed, 2133 warnings in 241.58s (0:04:01) ✓
```

总计:
- **新增测试 59/59 全部通过**
- **关键回归 439/439 全部通过**(C.10.5 Control + C.10.6 Runtime Control + Desktop + E2E + Server Gateway)
- 阶段性回归套件累计 **439 + 历史 380 = 819+ 全部通过**

### 10.3 旧测试无回归证明

| 测试套件 | 结果 |
|----------|------|
| `test_control_plane.py` | ✓ pass |
| `test_runtime_control_integration.py` | ✓ pass |
| `test_yuyi_desktop_remote.py` | ✓ pass |
| `test_yuyi_desktop_smoke.py` | ✓ pass |
| `test_yuyi_desktop_infrastructure.py` | ✓ pass |
| `test_full_system_e2e.py` | ✓ pass |
| `test_server_api_gateway.py` | ✓ pass |

---

## 11. 默认行为保持证明

1. **未注入 PolicyEngine**:`RuntimeCore(policy_engine=None)` 是默认构造参数,所有现有 380+ 测试无需任何修改。
2. **`_is_module_allowed_via_policy` 返回 None 时**:Runtime 回退到 `_is_module_allowed` 旧路径,完全等价于 C.10.6 行为。
3. **未命中任何规则**:`AlwaysAllowRule` 兜底,默认 allow。
4. **PolicyEngine 抛错**:`default_allow_decision` fail-soft,Runtime 主流程不阻塞。
5. **规则抛错**:`safe_call_rule` 隔离异常,返回 None,下一条规则继续评估。

---

## 12. 未修改文件证明(强约束)

按用户强约束,以下目录/文件**完全未触碰**:

- `src/memory/**` — 未修改
- `src/growth/**` — 未修改
- `src/personality/**` — 未修改
- `src/self_model/**` — 未修改
- `src/control/state/**` — 未修改
- `src/control/manager/**` — 未修改
- `src/control/api/**` — 未修改
- `config.yaml` — 未修改
- 任何已有业务模块 — 未修改

本阶段**仅修改 2 个文件**:
- `src/events/events.py` — 仅追加 EventType 与新 dataclass
- `src/runtime/runtime.py` — 仅追加 1 个字段与 2 个新方法

---

## 13. 核心成果

1. **统一决策层**:`PolicyEngine` 集中所有"允许/拒绝/只读"决策,Runtime 不再散落判断。
2. **规则插件化**:`SafeModeRule / MaintenanceRule / DisabledModuleRule / AlwaysAllowRule` 独立可替换,新增规则只需实现 `PolicyRule` 协议。
3. **Fail-soft 全链路**:PolicyEngine 异常 / 规则异常 / context 异常 / EventBus 异常,均不会阻塞 Runtime。
4. **事件可观测**:`RuntimePolicyDecisionEvent` 暴露给 Desktop / Audit,实现"决策透明"。
5. **向后兼容**:未注入 PolicyEngine 时 Runtime 行为零变化,所有 380+ 历史测试 0 改动通过。
6. **审计友好**:`RuntimeDecision.metadata` 自动记录 `matched_rule / fallback`,决策可追溯。

---

## 14. 下一阶段建议

- **Phase C.10.8 — Desktop 实时状态大屏**
  Desktop 订阅 `RuntimePolicyDecisionEvent`,展示"过去 100 个 cycle 的策略决策流",用于人工审计与故障排查。
- **Phase C.10.9 — Policy 灰度与限流**
  引入 `ThrottleRule` 支持按模块节流(例如 Growth 每 5 cycle 评估一次),为长任务降频。
- **Phase C.11.0 — Policy 热加载**
  支持从 config.yaml 或 Control API 动态注入/卸载规则,无需重启 Runtime。
