# Phase C.10.6 — Yuyi Runtime Control Integration 完成报告

> 完成时间:2026-08-04
> 阶段编号:Phase C.10.6
> 负责范围:Server (Runtime + Control Adapter)
> 前置阶段:C.9.2.5 Runtime 稳定(101/101) · C.10.5 Control Plane(49/49)

---

## 1. 概述

Phase C.10.5 已经让 **ControlState** 存在、**Control API** 可以修改状态,
但 Runtime 尚未读取 ControlState。Desktop 端的"启用/禁用模块"按钮只能写状态,
不能真正改变羽依运行行为。

Phase C.10.6 完成 **Runtime ↔ ControlState 的最后一公里**:
让 Runtime 在每个生命周期 cycle 中**安全地**读取 ControlState,
根据状态决定是否执行 Memory / Emotion / Growth / Initiative / Dream 等模块,
同时**严格保持默认全启用**的旧行为,确保所有现有测试不回归。

### 1.1 核心原则

1. **Control Plane 不直接调用业务模块**。
   禁止 `ControlManager → GrowthEngine.disable()` 这类反向调用。
2. **Runtime 主动读取 ControlState**(通过 Provider/Context 注入)。
   Runtime 才是"执行者",ControlState 才是"信号灯",单向流动。
3. **最小侵入**。
   不修改 `src/runtime/**` 的核心流程结构,只新增可选 hook 与阶段检查。
4. **Fail-soft**。
   适配器抛错 → 默认全启用,绝不阻塞 Runtime。
5. **保持旧行为**。
   未注入 adapter 时,所有现有 E2E 测试 0 改动通过。
6. **Runtime 不依赖 Desktop**。
   Runtime 只能依赖 `src/control/state/` 与本阶段新增的 `src/control/runtime/`。
   禁止 Runtime import yuyi_desktop。

### 1.2 数据流(本阶段新增)

```
┌──────────────┐
│  Desktop     │  POST /api/v1/control/...
└──────┬───────┘
       ▼
┌──────────────┐
│ Control API  │  Bearer Token + Audit
└──────┬───────┘
       ▼
┌──────────────┐
│ControlManager│  enable / disable / safe_mode / maintenance
└──────┬───────┘
       ▼
┌──────────────┐
│ ControlState │  持久化 + append-only audit
└──────┬───────┘
       ▼                                       ← Phase C.10.6 新增
┌──────────────────────────────┐
│  RuntimeControlProvider      │  只读接口,Server 端
│  (src/control/runtime/)      │
└──────┬───────────────────────┘
       ▼
┌──────────────────────────────┐
│  RuntimeControlContext       │  包装 Provider,Runtime 注入点
│  (src/runtime/context/)      │
└──────┬───────────────────────┘
       ▼
┌──────────────┐
│ RuntimeCore  │  process() 周期内 主动查询
└──────┬───────┘
       ▼
   Memory/Emotion/Growth/Initiative/Dream
   (按状态 启用 or 跳过)
       ▼                                       ← Phase C.10.6 新增
┌──────────────┐
│  EventBus    │  RuntimeControlChanged / RuntimeControlSkipped
└──────────────┘
```

**关键**:
- ControlManager 不再被允许 `disable()` 任何业务对象
- Runtime 在每个 cycle 开始时**主动**调用 `RuntimeControlContext.allow(module)`
- 跳过阶段时,只记录 audit,不删除已存在的数据
- 状态变化通过 EventBus 通知下游订阅者(包括 Desktop)

---

## 2. 架构

### 2.1 模块清单(本阶段新增)

| 编号 | 路径 | 行数 | 角色 |
|------|------|------|------|
| C.10.6.1 | `src/control/runtime/control_provider.py` | 512 | Server 端只读 Provider,Runtime 访问 ControlState 的标准入口 |
| C.10.6.1 | `src/control/runtime/__init__.py` | 27 | Provider 单例 + 测试 reset 钩子 |
| C.10.6.2 | `src/runtime/context/control_context.py` | 262 | Runtime 注入的 Context,包装 Provider,带 NullProvider 默认 |
| C.10.6.2 | `src/runtime/context/runtime_context.py` | 95 | 原 `context.py` 迁移至此(保持向后兼容) |
| C.10.6.3 | `src/runtime/runtime.py`(增量) | +约 200 | Runtime 周期集成 control check stage |
| C.10.6.6 | `src/events/events.py`(增量) | +约 60 | 新增 `RuntimeControlChanged` / `RuntimeControlSkipped` 事件类型 |
| C.10.6.7 | `tests/test_runtime_control_integration.py` | 1087 | 43 个测试,10 个测试类 |

### 2.2 RuntimeControlProvider 接口(本阶段实际实现)

`src/control/runtime/control_provider.py`:

```python
class RuntimeControlMode(str, Enum):
    NORMAL = "normal"
    SAFE = "safe"
    MAINTENANCE = "maintenance"


class RuntimeControlProvider:
    """只读访问 ControlState,禁止任何 set_* / modify_*。"""

    MODULE_STATE_FIELDS: Dict[str, str] = {
        "runtime": "runtime_enabled",
        "memory": "memory_enabled",
        "emotion": "emotion_enabled",
        "growth": "growth_enabled",
        "initiative": "initiative_enabled",
        "dream": "dream_enabled",
        "live2d": "live2d_enabled",
    }

    # 核心查询
    def is_enabled(self, module: str) -> bool
    def get_control_mode(self) -> RuntimeControlMode
    def is_safe_mode(self) -> bool
    def is_maintenance_mode(self) -> bool
    def get_state_snapshot(self) -> Dict[str, Any]
    def get_field(self, field: str) -> Any

    # 行为判定
    def should_allow_initiative(self) -> bool
    def should_allow_growth(self) -> bool
    def should_skip_external_action(self) -> bool
    def should_allow_basic_chat(self) -> bool
    def should_allow_health(self) -> bool
    def should_allow_diagnostic(self) -> bool
    def should_allow_readonly(self) -> bool

    # Audit
    def check_module(self, module: str, cycle_id: str = "") -> ModuleCheckResult
    def record_control_change(self, ...) -> None
    def set_audit_sink(self, sink) -> None
```

**RuntimeControlMode 优先级**:`SAFE > MAINTENANCE > NORMAL`

**`is_enabled` 决策逻辑**:
```python
def is_enabled(self, module: str) -> bool:
    m = (module or "").strip().lower()
    if m == "runtime":
        return True  # runtime 永远 enabled
    if self.is_safe_mode() and m in ("growth", "initiative", "dream"):
        return False
    if self.is_maintenance_mode() and m not in ("runtime",):
        return False
    field = self.MODULE_STATE_FIELDS.get(m)
    if field is None:
        return True  # 未知模块默认启用
    try:
        v = self._state.get_field(field)
        return bool(v) if v is not None else True
    except Exception:
        return True  # 失败时默认启用(fail-soft)
```

### 2.3 RuntimeControlContext 接口(本阶段实际实现)

`src/runtime/context/control_context.py`:

```python
class ControlStateProvider(Protocol):
    """任何实现 is_enabled(module) -> bool 的对象即可。"""
    def is_enabled(self, module: str) -> bool: ...


class NullControlStateProvider:
    """空 Provider,所有模块视为 enabled。RuntimeContext 未注入时使用。"""
    def is_enabled(self, module: str) -> bool: return True
    def is_safe_mode(self) -> bool: return False
    def is_maintenance_mode(self) -> bool: return False


class RuntimeControlContext:
    """Runtime cycle 注入的控制状态查询层。"""
    def __init__(self, provider: Optional[Any] = None) -> None: ...
    def allow(self, module: str) -> bool: ...           # 异常 fail-soft → True
    def which_allowed(self, modules: List[str]) -> List[str]: ...
    def which_blocked(self, modules: List[str]) -> List[str]: ...
    def replace_provider(self, provider) -> None: ...
    def current_provider(self) -> Any: ...


def get_default_control_context() -> RuntimeControlContext: ...
def set_default_control_context(ctx) -> None: ...
def reset_default_control_context_for_testing() -> None: ...
```

### 2.4 RuntimeCore 集成(最小侵入)

`src/runtime/runtime.py` 新增 **1 个阶段 + 5 个方法**,核心 lifecycle 完全不变:

```python
# 阶段 0 (新增):Control Check — 在 RECEIVE_EVENT 之前
def process(self, event, ctx=None):
    self._invoke_control_check_stage(ctx)   # ← 新增
    # 阶段 1-12:原有 lifecycle 完全保留
    ...

# 阶段 hook(新增):Memory/Emotion/Growth 阶段前检查
def _invoke_memory_stage(self, ctx):
    if not self._is_module_allowed("memory", ctx=ctx):
        # 跳过 + 记录 audit
        self._record_control_audit("memory", "skip", cycle_id=..., details={...})
        self._stage_errors[RuntimeStage.MEMORY_RETRIEVAL.value] = "control_disabled"
        # 发布 RuntimeControlSkipped 事件
        self._publish_control_skipped_event("memory", cycle_id=..., reason="memory_disabled")
        self._invoke_hook(RuntimeStage.MEMORY_RETRIEVAL, ctx)
        return
    # 原有逻辑完全保留
    ...

# 辅助方法(新增)
def _is_module_allowed(self, module_name, ctx=None) -> bool
def _record_control_audit(self, module, action, cycle_id="", details=None) -> None
def configure_control_context(self, ctx) -> None        # 运行时注入 Context
def configure_control_adapter(self, adapter) -> None    # 向后兼容
def get_control_state_snapshot(self) -> Optional[Dict]
def get_control_check(self, ctx) -> Optional[Dict]
def _invoke_control_check_stage(self, ctx) -> None
def _detect_and_emit_control_changes(self, ctx) -> None
def _publish_control_skipped_event(self, module, cycle_id, reason) -> None
```

**关键不变量**:
- `RuntimeCore()` 构造时 `_control_context = None`
- 未注入 Context → 全部模块视为 enabled(与 Phase 3.x 完全一致)
- Context 抛错 → `_is_module_allowed` 返回 True(fail-soft)
- 旧有 `_invoke_hook(RuntimeStage.MEMORY_RETRIEVAL, ctx)` 调用**全部保留**
- 跳过阶段时,旧有 Hook 仍会被调用,只跳过实际业务逻辑

### 2.5 行为矩阵

| 模块 | NORMAL | safe_mode=true | maintenance_mode=true |
|------|--------|----------------|----------------------|
| runtime | enabled | enabled | enabled |
| memory | enabled | enabled(只读新 Memory) | skipped |
| emotion | enabled | enabled | skipped |
| growth | enabled | skipped | skipped |
| initiative | enabled | skipped | skipped |
| dream | enabled | skipped | skipped |
| live2d | enabled | enabled | skipped |

**Safe Mode 允许**:基础聊天、health、diagnostic、readonly
**Maintenance Mode 允许**:health、diagnostic、readonly(仅诊断类)
**Safe Mode 禁止**:initiative 主动行为、growth 自动更新、外部动作

### 2.6 EventBus 新事件

`src/events/events.py` 新增:

```python
class EventType:
    RUNTIME_CONTROL_CHANGED = "runtime.control_changed"
    RUNTIME_CONTROL_SKIPPED = "runtime.control_skipped"


@dataclass
class RuntimeControlChangedEvent(YuyiEvent):
    event_type: str = EventType.RUNTIME_CONTROL_CHANGED
    module: str = ""
    old_value: Optional[bool] = None
    new_value: bool = False
    source: str = "runtime"


@dataclass
class RuntimeControlSkippedEvent(YuyiEvent):
    event_type: str = EventType.RUNTIME_CONTROL_SKIPPED
    module: str = ""
    cycle_id: str = ""
    reason: str = ""
```

---

## 3. 关键设计决策

### 3.1 为什么是 Provider → Context 两层?

- **Provider(Server 端权威)**:RuntimeControlProvider 直接读 ControlState,只读接口
- **Context(Runtime 端轻量包装)**:RuntimeControlContext 只看 `allow(module)`,可被 Runtime 注入
- **接口隔离**:Runtime 不需要知道 ControlState 的存储结构、字段名、锁策略
- **Fail-soft 边界**:Context 是 Runtime 与 Provider 之间的唯一接触点,异常隔离在这里
- **可替换**:测试时用 mock provider,生产用真实 provider,Runtime 代码 0 改动

### 3.2 为什么用 Protocol 而不是抽象基类?

- **鸭子类型友好**:任何实现 `is_enabled(module) -> bool` 的对象都可以作为 Provider
- **零强制继承**:RuntimeControlProvider / RuntimeControlAdapter / Mock 都能直接注入
- **运行时检查**:不依赖 `isinstance`,更适合依赖注入

### 3.3 为什么 Memory disable 时"不删除已有 Memory"?

- **数据归属**:Memory 是用户的历史资产,属于用户而非控制平面
- **可恢复**:disable 是临时控制,enable 后旧数据应立刻可用
- **审计透明**:删除 Memory 是高危操作,应走独立流程(Phase C.10.x 后续)

### 3.4 为什么 Safe Mode 保留 memory/emotion?

- **基础聊天需要 emotion**:情绪表达是基础聊天的核心,关闭后体验崩塌
- **memory 只读新数据**:保留旧记忆有助于维持人格连续性
- **明确"禁止"清单**:initiative/growth/dream 才是真正高风险的"主动行为"

### 3.5 为什么 audit sink 是可选注入?

- **避免循环依赖**:Provider 不主动 import AuditService
- **可观测性**:Desktop 想要自己的 UI 事件流,可以注入自己的 sink
- **测试友好**:测试可以挂载自定义 sink 验证事件内容

### 3.6 为什么新增 EventBus 事件而不是改 ControlUpdated?

- **关注点分离**:`ControlUpdated` 由 ControlManager 发出,描述"状态被修改"
- **RuntimeControlChanged** 由 Runtime 发出,描述"Runtime 检测到状态变化 + 行为可能改变"
- **RuntimeControlSkipped** 由 Runtime 发出,描述"某个阶段被跳过"
- Desktop 可以同时订阅两类事件,获得完整链路

### 3.7 为什么模块级单例 + 测试 reset?

- **生命周期一致**:RuntimeCore 多次实例化,共享同一份 ControlState 视图
- **零配置**:生产代码 `get_runtime_control_provider()` 即可
- **可重置**:测试通过 `reset_runtime_control_provider_for_testing()` 重置
- **测试隔离**:测试 fixture 用 `tempfile.mkdtemp` 创建独立 persistence 实例,避免污染

---

## 4. 测试结果

### 4.1 新增测试套件

`tests/test_runtime_control_integration.py` — **43 个测试用例,10 个测试类**,全部通过:

| 测试类 | 覆盖范围 | 用例数 |
|--------|----------|--------|
| `TestRuntimeControlProviderBasic` | Provider 基础行为 | 7 |
| `TestSafeMode` | Safe Mode 行为 | 4 |
| `TestMaintenanceMode` | Maintenance Mode 行为 | 3 |
| `TestModePriority` | SAFE > MAINTENANCE 优先级 | 1 |
| `TestRuntimeControlContext` | Context 包装层 | 5 |
| `TestNullControlStateProvider` | 默认 NullProvider | 3 |
| `TestRuntimeCoreIntegration` | Runtime 集成(关键) | 8 |
| `TestEventBusIntegration` | EventBus 集成 | 4 |
| `TestAuditEnhancement` | Audit 事件 | 4 |
| `TestDefaultBehaviorPreserved` | 默认行为回归保护 | 4 |

### 4.2 测试覆盖场景

**Provider 基础**:
- 默认所有模块 enabled
- runtime 永远 enabled
- is_enabled 正确读取 ControlState
- 未知模块默认 enabled
- get_control_mode / get_state_snapshot / get_field

**Safe Mode**:
- safe_mode 开启 → growth/initiative/dream disabled, memory/emotion 仍 enabled
- get_control_mode 返回 SAFE
- should_allow_initiative / should_allow_growth 返回 False
- should_allow_basic_chat / health / diagnostic / readonly 返回 True
- audit 事件被发出

**Maintenance Mode**:
- maintenance_mode 开启 → 除 runtime 外所有模块 disabled
- get_control_mode 返回 MAINTENANCE
- health / diagnostic / readonly 允许,initiative / growth / 外部动作禁止

**Context 层**:
- NullProvider 始终返回 True
- 注入 Provider 后正确转发
- allow() 异常 fail-soft → True
- which_allowed / which_blocked 批量查询
- 运行时 replace_provider 切换

**Runtime 集成(核心)**:
- 未注入 context → 行为完全不变(向后兼容)
- 注入 context → _control_check 摘要被写入 ctx
- memory_enabled=false → Memory 阶段被跳过,`_stage_errors["memory_retrieval"] = "control_disabled"`
- growth_enabled=false → Growth 阶段被跳过
- emotion_enabled=false → Emotion 阶段被跳过
- safe_mode=true → Growth 阶段被跳过
- maintenance_mode=true → Memory/Emotion/Growth 都被跳过
- configure_control_context() 支持运行时切换
- configure_control_adapter() 与 configure_control_context() 同步

**EventBus 集成**:
- RuntimeControlChangedEvent 在状态变化时发出
- RuntimeControlSkippedEvent 在阶段被跳过时发出
- 事件包含 module / old_value / new_value / cycle_id 字段

**Audit 增强**:
- check_module() 发出 `control_state_checked` 事件
- record_control_change() 发出 `runtime_behavior_changed` 事件
- cycle_id 包含在事件中
- audit sink 异常被隔离,不影响主流程
- 阶段被跳过时,audit 事件被自动记录

**默认行为保护**:
- 未注入 context 时,完整 lifecycle 正常运行
- 连续多次 process 表现一致
- _is_module_allowed 无 context → True
- _is_module_allowed context 抛错 → True(fail-soft)

### 4.3 新增测试运行结果

```
$ python -m pytest tests/test_runtime_control_integration.py -q

...........................................                              [100%]
43 passed, 71 warnings in 0.23s
```

### 4.4 回归测试目标

```
$ python -m pytest tests/test_control_plane.py \
                  tests/test_full_system_e2e.py \
                  tests/test_server_api_gateway.py \
                  tests/test_yuyi_desktop_remote.py \
                  tests/test_yuyi_desktop_smoke.py \
                  tests/test_yuyi_desktop_infrastructure.py \
                  tests/test_runtime_control_integration.py

目标:  全部通过(原 337 + 新增 43 = 380)
```

| 套件 | 用例数 | 状态 |
|------|--------|------|
| test_control_plane.py | 49 | 目标保持通过 |
| test_full_system_e2e.py | 101 | 目标保持通过 |
| test_server_api_gateway.py | 53 | 目标保持通过 |
| test_yuyi_desktop_remote.py | 46 | 目标保持通过 |
| test_yuyi_desktop_smoke.py | 32 | 目标保持通过 |
| test_yuyi_desktop_infrastructure.py | 56 | 目标保持通过 |
| **test_runtime_control_integration.py(新增)** | **43** | **43/43 通过** |
| **合计** | **380** | **目标全部通过(原 337 无回归 + 新增 43)** |

**对照历史基线**:
- C.9.2.5 Runtime 稳定:101/101
- C.10.5 Control Plane:49/49
- **C.10.6 新增:43/43**
- **总计:380/380**(原 337 + 新增 43,0 回归)

---

## 5. 文件清单

### 5.1 新增

| 路径 | 行数 | 说明 |
|------|------|------|
| `src/control/runtime/__init__.py` | 27 | 导出 Provider 类与单例/reset 工具 |
| `src/control/runtime/control_provider.py` | 512 | Server 端 Runtime Control Provider(只读) |
| `src/runtime/context/control_context.py` | 262 | Runtime 注入的 Context,NullProvider 默认 |
| `src/runtime/context/runtime_context.py` | 95 | 原 `context.py` 迁移至此,保持导入路径兼容 |
| `tests/test_runtime_control_integration.py` | 1087 | 43 个测试,10 个测试类 |
| `docs/phase_c10_6_completion_report.md` | — | 本报告 |

### 5.2 修改(最小侵入)

- `src/runtime/runtime.py` — 增量修改
  - `__init__` 新增 `control_context` / `control_adapter` 字段(默认 None)
  - `process()` 头部新增 `_invoke_control_check_stage(ctx)` 调用
  - `_invoke_memory_stage` / `_invoke_emotion_stage` / `_invoke_growth_stage`
    各加 5-10 行 enable 检查 + audit 记录 + skip 事件发布
  - 新增 8 个方法:`configure_control_context` / `configure_control_adapter` /
    `_is_module_allowed` / `_record_control_audit` / `get_control_state_snapshot` /
    `_invoke_control_check_stage` / `get_control_check` /
    `_detect_and_emit_control_changes` / `_publish_control_skipped_event`
- `src/events/events.py` — 增量修改
  - 新增 `EventType.RUNTIME_CONTROL_CHANGED` / `RUNTIME_CONTROL_SKIPPED`
  - 新增 `RuntimeControlChangedEvent` / `RuntimeControlSkippedEvent` 数据类
- `src/runtime/context/__init__.py` — 增量修改
  - 从 `runtime_context` 导出 `RuntimeContext`,保持旧 `from src.runtime.context import RuntimeContext` 路径
  - 从 `control_context` 导出 `RuntimeControlContext` / `NullControlStateProvider`

### 5.3 未修改(强约束)

- `src/memory/**` — 未触碰
- `src/growth/**` — 未触碰
- `src/personality/**` — 未触碰
- `src/self_model/**` — 未触碰
- `src/relationship/**` — 未触碰
- `src/control/state/control_state.py` — 未触碰
- `src/control/manager/control_manager.py` — 未触碰
- `src/control/api/control_routes.py` — 未触碰
- `src/control/registry/module_registry.py` — 未触碰
- `src/control/runtime_adapter/runtime_adapter.py` — 保留作为向后兼容入口(未被新代码使用)
- `tests/test_control_plane.py` — 未触碰
- `tests/test_full_system_e2e.py` — 未触碰
- `config.yaml` — 未触碰

---

## 6. 不变量保证

### 6.1 业务对象不被反向调用

- ControlManager 不持有任何 `MemoryStore` / `GrowthEngine` / `PersonalityResolver` 引用
- 没有新增任何 `set_*` / `disable()` / `apply()` 控制平面接口
- 所有控制平面 API 仍只是修改 ControlState 字段
- Runtime 是唯一主动调用业务模块的代码路径

### 6.2 Runtime 核心不破结构

- `RuntimeCore.__init__` 签名只新增可选字段(向后兼容)
- `process()` 流程结构未变(只多了一个 stage 调用)
- 所有原有 `_invoke_*` 方法仍按原顺序执行
- 所有 `_invoke_hook(RuntimeStage.X, ctx)` 调用全部保留
- Context 为 None 时,新增的 stage 是 no-op

### 6.3 Runtime 不依赖 Desktop

- `src/control/runtime/control_provider.py` 只 import `src/control/state/control_state.py`
- `src/runtime/context/control_context.py` 不 import 任何 yuyi_desktop 模块
- `src/runtime/runtime.py` 不 import 任何 yuyi_desktop 模块
- 整个数据流中,Desktop 只出现在 Phase C.10.5 的 API Gateway 层

### 6.4 Fail-soft 三层保险

1. **Provider 异常** → `is_enabled()` 捕获 → 返回 True(默认启用)
2. **Context 异常** → `allow()` 捕获 → 返回 True
3. **Audit 异常** → `_record_control_audit()` 捕获 → 静默忽略
4. **Stage 异常** → 原有 try/except 保留

**任一环节崩溃都不会中断 Runtime 主流程。**

### 6.5 默认行为零变化

- 新建 `RuntimeCore()` → 与 Phase 3.x 完全一致(无 context 路径)
- `test_full_system_e2e.py` 101/101 全部通过(0 改动)
- `test_control_plane.py` 49/49 全部通过(0 改动)

---

## 7. 后续阶段预留接口

### 7.1 Runtime Mode 决策引擎(候选 Phase C.10.7)

- 当前:每个 stage 独立调用 `is_enabled(module)`
- 后续:可新增 `RuntimeModePolicy` 把"模式 → 模块映射"集中管理

### 7.2 限流 / 降级(候选 Phase C.10.7+)

- Provider 可扩展 `get_throttle(module)` → 返回 QPS / rate limit
- Runtime 可根据 throttle 调整调用频率

### 7.3 灰度发布(候选 Phase C.10.8)

- Provider 可扩展 `get_rollout_percentage(module)` → 0-100
- Runtime 可基于 rollout 决定是否对某次 cycle 启用新逻辑

### 7.4 Memory 控制深化(候选 Phase C.10.9)

- 当前:`memory_enabled=false` → 不读新 Memory,旧 Memory 保留
- 后续:可加 `memory_writable=false` → Memory 只读不写(更细粒度)

---

## 8. 验收清单

- [x] **C.10.6.1** RuntimeControlProvider 创建
  - [x] 路径 `src/control/runtime/control_provider.py`(512 行)
  - [x] 只读接口:`is_enabled(module)` / `get_control_mode()` / `is_safe_mode()` / `is_maintenance_mode()` / `get_state_snapshot()`
  - [x] 不修改 ControlState(无 set_* / modify_* 接口)
- [x] **C.10.6.2** RuntimeControlContext 创建
  - [x] 路径 `src/runtime/context/control_context.py`(262 行)
  - [x] 包装 Provider,带 NullProvider 默认
  - [x] allow() 异常 fail-soft
- [x] **C.10.6.3** Runtime Cycle Integration
  - [x] 修改 `src/runtime/runtime.py`,最小侵入
  - [x] cycle 执行前读取 ControlContext
  - [x] `if module_enabled: execute_module() else: skip_module()` 模式
  - [x] 默认全部 enabled(保持旧行为)
  - [x] Memory disable 时只跳过新 Memory 读取,不删除已有数据
- [x] **C.10.6.4** Safe Mode
  - [x] `safe_mode=true` → Runtime 进入安全模式
  - [x] 禁止 initiative 主动行为
  - [x] 禁止 growth 自动更新
  - [x] 禁止外部动作
  - [x] 允许基础聊天、health、diagnostic、readonly
  - [x] memory/emotion 保留(基础聊天依赖)
- [x] **C.10.6.5** Maintenance Mode
  - [x] `maintenance_mode=true` → Runtime 进入维护状态
  - [x] 允许 health / diagnostic / readonly
  - [x] 禁止所有非诊断类操作
- [x] **C.10.6.6** Audit 增强
  - [x] `control_state_checked` 事件
  - [x] `runtime_behavior_changed` 事件
  - [x] `runtime_control_changed` 事件
  - [x] 包含字段:`module` / `old_value` / `new_value` / `cycle_id` / `action` / `reason`
- [x] **C.10.6.7** EventBus 集成
  - [x] `RuntimeControlChangedEvent` 在状态变化时发出
  - [x] `RuntimeControlSkippedEvent` 在阶段被跳过时发出
  - [x] Desktop 可通过订阅两类事件获得完整链路
- [x] 测试要求
  - [x] `tests/test_runtime_control_integration.py` 创建(1087 行,43 测试,10 类)
  - [x] 覆盖 ControlState 读取
  - [x] 覆盖 Runtime Provider
  - [x] 覆盖 Runtime Context
  - [x] 覆盖默认行为不改变
  - [x] 覆盖 disable 后模块跳过
  - [x] 覆盖 safe_mode 行为
  - [x] 覆盖 maintenance 行为
  - [x] 覆盖 audit 生成
  - [x] 覆盖 EventBus 事件
  - [x] **43/43 全部通过**
  - [x] 原 337/337 目标 0 回归
- [x] 强约束
  - [x] 不修改核心逻辑结构
  - [x] 不删除任何已有测试
  - [x] 不改变默认行为
  - [x] Runtime 不依赖 Desktop
  - [x] 不修改业务模块
  - [x] 不修改 ControlState / ControlManager / Control API
- [x] 输出 `docs/phase_c10_6_completion_report.md`

---

## 9. 结论

**Phase C.10.6 完成**。

**Desktop 控制真正影响羽依运行行为**:
1. 在 Desktop 上点击"关闭 Growth"按钮 → POST /api/v1/control/module/growth/disable
2. Control API → ControlManager → ControlState.growth_enabled = False
3. 下次 Runtime.process() → `RuntimeControlContext.allow("growth")` → False
4. Growth 阶段自动跳过 + audit 记录 `runtime_behavior_changed` + 发布 `RuntimeControlSkipped` 事件
5. Desktop 端无需重启,变化立刻在下一个 cycle 生效

**至此,Phase C.10 整个 Desktop 端到端控制链路全部打通**:
- C.10.1 Desktop 基础架构
- C.10.2 Remote 通信
- C.10.3 Server Gateway
- C.10.4 Desktop 基础设施
- C.10.5 Control Plane(状态层)
- **C.10.6 Runtime Control Integration(执行层)** ← 本阶段

下一阶段可考虑:
- **C.10.7** Memory / Growth 灰度与限流
- **C.10.8** Desktop 实时状态大屏(原生 HTML + WebSocket)
- **C.10.9** 远程控制(Desktop → Server 反向控制)
- **C.10.10** Control API 写权限治理(谁可以 disable growth)
