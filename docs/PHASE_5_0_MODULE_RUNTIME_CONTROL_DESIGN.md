# Phase 5.0 Module Runtime Control System 设计文档

文档版本：v1.0
适用范围：Phase 5.0 Module Runtime Control 阶段
本阶段不写代码，仅输出架构设计等待确认。

---

## 1. 当前架构分析

### 1.1 当前生命周期管理方式

当前 `src/runtime/lifecycle/` 已实现 Phase 5.0-D1 Lifecycle Core：

#### 已有组件

- `LifecycleManager`：核心调度器
  - 持有 `LifecycleRegistry` / `EventEmitter` / `Clock`
  - 管理 `LifecycleStateMachine`
  - `tick()` 遍历任务 → 决策 → 执行 → 收集结果
  - 异常隔离：单任务失败不影响其他任务
  - `health_check()` 接口
- `LifecycleRegistry`：任务注册
  - `register(task)` / `unregister(task_id)`
  - 支持 `IGNORE_DUPLICATE` / `REJECT_DUPLICATE`
- `LifecycleTask` Protocol + `BaseLifecycleTask` / `SimpleTask`
  - `task_id` / `owner` / `priority` / `condition` / `budget_ms` / `interval_seconds`
  - `execute(ctx)` 返回 `LifecycleResult`
- `LifecycleContext` / `LifecycleState` / `LifecycleDecision` / `LifecycleResult` / `LifecycleError`
- `internal/clock.py`：时钟抽象
- `internal/event_emitter.py`：事件发射

#### 已有 LifecycleTask 实例

- `src/runtime/integration/tasks/` 下：
  - `emotion_lifecycle_task.py`
  - `goal_lifecycle_task.py`
  - `growth_lifecycle_task.py`
  - `initiative_lifecycle_task.py`
  - `memory_lifecycle_task.py`
  - `personality_lifecycle_task.py`
  - `reflection_lifecycle_task.py`
  - `relationship_lifecycle_task.py`
  - `base_integration_task.py`
- `src/runtime/self_model/self_model_lifecycle_task.py`

### 1.2 当前模块加载方式

#### 已有：ModuleLoader（启动时静态发现）

`src/core/module_loader.py`：

- 启动时扫描 `src/` 子目录
- 读取 `<module>/module.py` 中的 `MODULE_INFO` 字典
- 解析为 `ModuleInfo`（name / display / version / dependencies / config_key / enabled）
- 依赖 `config.yaml` 中的 `enabled` 字段判断是否启用
- 不创建实例，仅做元信息注册
- 已知问题：
  - 只在启动时 discover，无法运行时新增
  - 不持有模块实例
  - `enabled` 与 `started` 是两回事，但当前是混为一谈

#### 与 Orchestrator 的关系

- `Orchestrator` 持有 `screen_context_manager` / `control_manager` / `agent_server` 等具体实例
- `init_admin(orchestrator)` 时由 admin 模块反向读取这些实例
- 模块启停实际上是"修改 config.yaml + 重新初始化对应 manager"

### 1.3 当前 Dashboard 数据来源

#### 模块状态来源

`src/admin/api/routes.py`：

- `/api/modules`：从 `_module_loader.get_all_modules()` + `HeartbeatCollector.get_all_status()` 获取
- 心跳状态归一化（避免"心跳超时"误导）
- 依赖状态从 `info.dependencies` 解析
- **问题**：心跳数据依赖 `HeartbeatCollector`，而 `HeartbeatCollector` 当前由各模块主动 register，大部分业务模块未注册
- 因此当前 modules 列表主要是 screen / control / remote / token_opt / initiative 等"基础设施模块"

#### 模块启停来源

- `/api/module/<name>/start`：仅修改 `_module_loader.update_enabled_state()` + `ConfigManager.toggle_module()`，并对 initiative 特殊处理（pkill process）
- `/api/module/<name>/stop`：相同
- `/api/module/<name>/reload`：根据 `module_name` 走 screen / control 重新初始化路径
- **问题**：业务模块（Memory / Growth / Emotion / Personality / SelfModel / Reflection / Initiative / Goal）当前没有启停接口

### 1.4 当前 IntegrationEvent

`src/runtime/integration/integration_event.py`：

- 已定义 17+ 事件类型（experience / memory / emotion / growth / personality / relationship / snapshot / lifecycle / reflection / initiative / desire / goal）
- 严重度：info / notice / warning / critical
- **问题**：当前没有 Module 相关事件（MODULE_REGISTERED / MODULE_STARTED / MODULE_STOPPED / MODULE_RESTARTED / MODULE_ERROR）

### 1.5 当前 Runtime 集成

`src/runtime/runtime_bridge.py` + `src/runtime/runtime_core.py`：

- `RuntimeBridge` 单例持有 `RuntimeCore`
- `RuntimeCore` 集成：
  - Memory System
  - Reflection Engine / Evaluator
  - Growth Proposal / Approval
  - Identity Stability Engine
  - Personality Evolution Pipeline
  - Self Model
  - Relationship System
  - Emotion System
  - Autonomous Decision Layer
- **问题**：这些模块是"硬连接"，无法运行时动态启停

### 1.6 当前架构问题

#### 缺少真正的模块抽象

- 没有"模块（Module）"这一层独立概念
- 业务系统（Memory / Growth / Emotion / Personality / SelfModel / Reflection / Initiative / Goal）没有统一注册接口
- 没有 module_id / module_state / module_instance 三元组

#### 缺少动态启停能力

- 启动时 `RuntimeCore` 一次性集成所有模块
- 运行时无法停止某个模块
- 停止后无法清理 LifecycleTask
- 启动后无法注册新的 LifecycleTask

#### 缺少模块统一状态观测

- 各模块状态分散在：
  - `ModuleLoader._modules`（仅元信息）
  - `HeartbeatCollector._heartbeats`（仅心跳）
  - 各模块自己的内部状态
  - `IntegrationEventStore`（事件流）
- Dashboard 无法获得统一的"模块实时状态"

#### 缺少 Module 事件

- IntegrationEvent 没有 Module 相关事件
- Module 状态变化没有事件通知
- Dashboard 无法实时感知模块启停

#### 模块启停路径分散

- `init_admin(orchestrator)` 通过 `_orchestrator.xxx` 直接访问
- 业务模块没有 `enable() / disable() / start() / stop()` 接口
- 启停依赖"修改 config.yaml + 重启进程"（screen / control / remote）

#### 缺少对未来的扩展

- 没有"模块能力声明"接口
- 业务模块无法声明 LifecycleTask
- Agent Body 未来作为 Module 时没有统一接入路径

---

## 2. 新架构设计

### 2.1 设计目标

为羽依建立统一的"模块运行时控制层"：

- 所有可独立启停的功能单元都抽象为 Module
- Module 通过 `register()` 声明
- Module 通过 `LifecycleController` 实例化与销毁
- Module 状态由 `ModuleManager` 统一管理
- Dashboard 仅通过 Provider 访问
- 所有启停动作产生 IntegrationEvent
- 未来 Agent Body 也作为 Module 接入

### 2.2 核心抽象

```
Module
   ↓
ModuleState（状态模型）
   ↓
ModuleInstance（运行实例）
   ↓
ModuleRegistry（注册表）
   ↓
LifecycleController（生命周期）
   ↓
ModuleManager（控制平面）
   ↓
Dashboard Provider（只读）
   ↓
Dashboard UI
```

### 2.3 数据流

```
Dashboard
   ↓ POST /api/dashboard/v2/module/<id>/start
Dashboard Router
   ↓
ModuleManager.start(module_id)
   ↓
Permission Check（Phase 5.0-E）
   ↓
LifecycleController.start(module_id)
   ↓
   1. 创建 ModuleInstance
   2. 注入 RuntimeContext
   3. 注册 LifecycleTask
   4. 启动模块自身 initialize()
   5. 发布 INTEGRATION_MODULE_STARTED
   ↓
ModuleState 更新
   ↓
WebSocket 推送
   ↓
Dashboard 更新
```

### 2.4 核心原则

- **最小侵入**：不修改现有业务模块（Memory / Growth / Emotion / Personality / Relationship / SelfModel / Reflection / Initiative / Goal）
- **只读 Dashboard**：Dashboard 不直接访问任何 Module 实例
- **可观测**：所有 Module 状态来自 ModuleManager 单一真实源
- **可审计**：所有启停动作产生 IntegrationEvent + AuditLog
- **可热插拔**：运行时任意模块可以启停，不影响其他模块
- **可扩展**：未来 Module（Agent Body、Voice、Vision）按统一接口接入

### 2.5 不修改

禁止修改以下目录：

- src/memory/**
- src/growth/**
- src/emotion/**
- src/personality/**
- src/relationship/**
- src/runtime/lifecycle/**
- src/runtime/self_model/**
- src/runtime/reflection/**
- src/runtime/initiative/**
- src/runtime/goal/**
- src/runtime/integration/integration_event.py（仅扩展枚举值）
- src/runtime/runtime_bridge.py（仅扩展，不破坏现有逻辑）
- src/core/module_loader.py（保留作为元信息发现，不影响新 Module 系统）
- config.yaml（API Key 不动）

---

## 3. 数据模型

### 3.1 ModuleState

```python
class ModuleStatus(str, Enum):
    REGISTERED = "registered"      # 已注册，未启动
    STARTING = "starting"          # 启动中
    RUNNING = "running"            # 运行中
    PAUSING = "pausing"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"            # 已停止
    ERROR = "error"                # 错误
    FAILED = "failed"              # 启动失败


@dataclass
class ModuleState:
    module_id: str                  # 唯一 id
    name: str                       # 显示名
    version: str
    description: str

    status: ModuleStatus
    enabled: bool                   # 是否允许启动
    started_at: Optional[str]       # ISO 8601
    stopped_at: Optional[str]
    last_tick_at: Optional[str]     # 最后一次 tick 时间
    last_tick_age_seconds: Optional[float]
    last_error: Optional[str]
    error_count: int

    health: str                     # "healthy" / "degraded" / "critical" / "unknown"
    registered_at: str
    restart_count: int
    meta: Dict[str, Any]
```

### 3.2 ModuleDefinition

```python
@dataclass
class ModuleDefinition:
    module_id: str
    name: str
    version: str
    description: str
    dependencies: List[str]
    category: str                   # brain / body / integration / utility
    config_key: Optional[str]
    factory: Callable               # 创建实例的工厂
    lifecycle_tasks: List[str]      # 启动时要注册的 LifecycleTask 工厂
    on_start: Optional[Callable]    # 启动钩子
    on_stop: Optional[Callable]     # 停止钩子
    can_disable: bool = True
    can_restart: bool = True
    critical: bool = False          # 是否核心模块（停止需更高权限）
    reload_mode: str = "soft"       # hot / soft / restart
```

### 3.3 ModuleInstance

```python
@dataclass
class ModuleInstance:
    module_id: str
    instance: Any                   # 实际模块对象
    runtime_context: Any            # 注入的 RuntimeContext
    lifecycle_task_ids: List[str]   # 已注册的 LifecycleTask id
    started_at: str
    last_error: Optional[str]
    error_count: int
```

### 3.4 ModuleRegistryEntry

```python
@dataclass
class ModuleRegistryEntry:
    module_id: str
    definition: ModuleDefinition
    registered_at: str
    state: ModuleState
    instance: Optional[ModuleInstance]
```

### 3.5 ModuleEvent

```python
@dataclass
class ModuleEvent:
    event_id: str
    event_type: str                 # MODULE_REGISTERED / MODULE_STARTED / MODULE_STOPPED / MODULE_RESTARTED / MODULE_ERROR
    source: str                     # "runtime.modules"
    timestamp: str
    module_id: str
    old_state: Optional[ModuleStatus]
    new_state: Optional[ModuleStatus]
    reason: str
    evidence: List[str]
    related_ids: List[str]
```

---

## 4. 模块注册与生命周期

### 4.1 ModuleRegistry

负责：
- 注册 ModuleDefinition
- 列出已注册模块
- 校验依赖关系
- 校验模块 ID 唯一性

接口：

```python
class ModuleRegistry:
    def register(self, definition: ModuleDefinition) -> ModuleRegistryEntry
    def unregister(self, module_id: str) -> bool
    def get(self, module_id: str) -> Optional[ModuleRegistryEntry]
    def list_all(self) -> List[ModuleRegistryEntry]
    def list_by_category(self, category: str) -> List[ModuleRegistryEntry]
    def has(self, module_id: str) -> bool
    def resolve_dependencies(self, module_id: str) -> List[str]
```

### 4.2 ModuleManager

负责：
- 控制模块启停
- 维护 ModuleState
- 调用 LifecycleController
- 发布 IntegrationEvent
- 异常处理与恢复

接口：

```python
class ModuleManager:
    def start(self, module_id: str, operator: str = "admin", reason: str = "") -> ModuleStartResult
    def stop(self, module_id: str, operator: str = "admin", reason: str = "") -> ModuleStopResult
    def restart(self, module_id: str, operator: str = "admin", reason: str = "") -> ModuleRestartResult
    def enable(self, module_id: str, operator: str = "admin") -> bool
    def disable(self, module_id: str, operator: str = "admin") -> bool

    def get_state(self, module_id: str) -> Optional[ModuleState]
    def get_all_states(self) -> List[ModuleState]

    def get_health(self, module_id: str) -> Dict[str, Any]
    def get_recent_events(self, module_id: str, limit: int = 20) -> List[ModuleEvent]
```

### 4.3 LifecycleController

负责：
- 创建 ModuleInstance
- 注入 RuntimeContext
- 注册 LifecycleTask
- 清理资源
- 调用模块 on_start / on_stop

接口：

```python
class LifecycleController:
    def create_instance(self, definition: ModuleDefinition) -> ModuleInstance
    def inject_runtime_context(self, instance: ModuleInstance, context: RuntimeContext) -> None
    def register_lifecycle_tasks(self, instance: ModuleInstance, task_factories: List[Callable]) -> List[str]
    def unregister_lifecycle_tasks(self, task_ids: List[str]) -> None
    def invoke_start_hook(self, instance: ModuleInstance) -> None
    def invoke_stop_hook(self, instance: ModuleInstance) -> None
    def destroy_instance(self, instance: ModuleInstance) -> None
```

### 4.4 启动流程

```
ModuleManager.start(module_id)
   ↓
1. 校验 module_id 存在
2. 校验 permission
3. 校验当前状态（必须非 RUNNING）
4. 解析依赖（dependencies 必须 RUNNING）
5. 创建 ModuleState（status=STARTING）
6. LifecycleController.create_instance()
7. LifecycleController.inject_runtime_context()
8. LifecycleController.register_lifecycle_tasks() → 获得 task_ids
9. LifecycleController.invoke_start_hook()  ← 模块自定义启动
10. 更新 ModuleState（status=RUNNING, started_at, restart_count++）
11. 发布 INTEGRATION_MODULE_STARTED 事件
12. 记录 audit log
13. 返回成功
```

### 4.5 停止流程

```
ModuleManager.stop(module_id)
   ↓
1. 校验 module_id 存在
2. 校验 permission
3. 校验当前状态（必须 RUNNING / ERROR）
4. 更新 ModuleState（status=STOPPING）
5. LifecycleController.invoke_stop_hook()  ← 模块自定义停止
6. LifecycleController.unregister_lifecycle_tasks(task_ids)
7. LifecycleController.destroy_instance()
8. 更新 ModuleState（status=STOPPED, stopped_at）
9. 发布 INTEGRATION_MODULE_STOPPED 事件
10. 记录 audit log
11. 返回成功
```

### 4.6 重启流程

```
ModuleManager.restart(module_id)
   ↓
1. 调用 stop(module_id)
2. 调用 start(module_id)
3. 异常恢复：start 失败时回滚 stop（保留 STOPPED 状态）
4. 发布 INTEGRATION_MODULE_RESTARTED 事件
```

---

## 5. 内置模块（v1.0 首批注册）

### 5.1 业务模块（adapter 模式）

| module_id       | category    | critical | 备注                  |
|-----------------|-------------|----------|-----------------------|
| memory_system   | brain       | True     | Memory System adapter |
| emotion_system  | brain       | True     | Emotion adapter       |
| growth_system   | brain       | True     | Growth adapter        |
| personality_system | brain    | True     | Personality adapter   |
| relationship_system | brain   | False    | Relationship adapter  |
| selfmodel_system | brain      | True     | SelfModel adapter     |
| reflection_system | brain     | False    | Reflection adapter    |
| initiative_system | brain     | False    | Initiative adapter    |
| goal_system     | brain       | False    | Goal adapter          |

**重要说明**：business module 仍保留在原路径（src/memory/、src/growth/ 等），不在 Phase 5.0 中重写它们。Module 系统通过"adapter 包装"的方式将它们接入 Module 框架。

### 5.2 集成模块

| module_id       | category    | critical | 备注                  |
|-----------------|-------------|----------|-----------------------|
| integration_event_bus | integration | True | IntegrationEvent 总线 |
| lifecycle_core | integration | True     | Lifecycle Manager     |
| persistence    | integration | True     | Persistence Manager   |

### 5.3 Agent Body（Phase 5.0-E 预留）

| module_id        | category | critical | 备注             |
|------------------|----------|----------|------------------|
| agent_body_eye   | body     | False    | Eye System       |
| agent_body_hand  | body     | False    | Hand System      |
| agent_body_permission | body | True  | Permission Layer |
| agent_body_executor | body  | False    | Mock Executor    |

Agent Body 的每个子系统都是一个 Module，符合未来 Body 整体启停的扩展。

### 5.4 工具模块

| module_id       | category    | critical | 备注                  |
|-----------------|-------------|----------|-----------------------|
| audit_log       | utility     | True     | Audit Logger          |
| logger          | utility     | True     | 系统 Logger           |
| metrics         | utility     | False    | Metrics 收集          |

---

## 6. API 设计

### 6.1 GET 接口

#### 模块列表

`GET /api/dashboard/v2/module/list`

返回：
```json
{
    "ok": true,
    "data": {
        "modules": [
            {
                "module_id": "memory_system",
                "name": "Memory System",
                "version": "5.0.0",
                "category": "brain",
                "status": "running",
                "enabled": true,
                "started_at": "2026-08-01T10:00:00Z",
                "stopped_at": null,
                "last_tick_at": "2026-08-01T12:00:00Z",
                "last_tick_age_seconds": 1.5,
                "last_error": null,
                "error_count": 0,
                "health": "healthy",
                "dependencies": [],
                "can_disable": false,
                "critical": true
            }
        ]
    }
}
```

#### 单个模块状态

`GET /api/dashboard/v2/module/<module_id>/status`

#### 模块生命周期任务

`GET /api/dashboard/v2/module/<module_id>/tasks`

#### 模块最近事件

`GET /api/dashboard/v2/module/<module_id>/events?limit=20`

#### 模块依赖图

`GET /api/dashboard/v2/module/dependencies`

#### 整体健康

`GET /api/dashboard/v2/module/health`

### 6.2 POST 接口

#### 启动

`POST /api/dashboard/v2/module/<module_id>/start`

Body：
```json
{
    "operator": "admin",
    "reason": "manual start"
}
```

返回：
```json
{
    "ok": true,
    "data": {
        "module_id": "memory_system",
        "old_state": "stopped",
        "new_state": "running",
        "started_at": "2026-08-01T12:00:00Z"
    }
}
```

#### 停止

`POST /api/dashboard/v2/module/<module_id>/stop`

#### 重启

`POST /api/dashboard/v2/module/<module_id>/restart`

#### 启用 / 禁用

`POST /api/dashboard/v2/module/<module_id>/enable`
`POST /api/dashboard/v2/module/<module_id>/disable`

### 6.3 WebSocket 推送

`WS /api/dashboard/v2/module/ws`

订阅 channels：
- module_state_changes
- module_events
- module_errors

推送消息：
```json
{
    "channel": "module_state_changes",
    "event": {
        "event_id": "evt_xxx",
        "event_type": "INTEGRATION_MODULE_STARTED",
        "module_id": "memory_system",
        "old_state": "stopped",
        "new_state": "running",
        "timestamp": "2026-08-01T12:00:00Z"
    }
}
```

### 6.4 错误码

| code                  | http | 说明                |
|-----------------------|------|---------------------|
| MODULE_NOT_FOUND      | 404  | 模块不存在          |
| MODULE_ALREADY_RUNNING| 409  | 已在运行            |
| MODULE_NOT_RUNNING    | 409  | 未运行              |
| MODULE_HAS_DEPENDENTS | 409  | 有其他模块依赖      |
| MODULE_CRITICAL       | 403  | 核心模块，需更高权限 |
| PERMISSION_DENIED     | 403  | 权限不足            |
| INVALID_STATE         | 409  | 状态非法            |
| INTERNAL_ERROR        | 500  | 系统错误            |

---

## 7. Dashboard 设计

### 7.1 Module Control 页面

#### 布局

```
┌─────────────────────────────────────────────────────┐
│  顶栏：返回 / 筛选 / 刷新                            │
├─────────────────────────────────────────────────────┤
│  概览卡片：                                          │
│  [总数] [运行中] [已停止] [错误] [核心模块]           │
├─────────────────────────────────────────────────────┤
│  模块表格：                                          │
│  ┌──────┬──────┬──────┬──────┬──────┬─────────┐   │
│  │ 模块 │ 类别 │ 状态 │ 启动时间│ 错误 │ 操作   │   │
│  ├──────┼──────┼──────┼──────┼──────┼─────────┤   │
│  │Memory│brain │运行  │10:00 │0    │[停止]  │   │
│  │Emotion│brain│运行  │10:01 │0    │[停止]  │   │
│  │Growth│brain │停止  │ -    │0    │[启动]  │   │
│  │...                                              │   │
│  └──────┴──────┴──────┴──────┴──────┴─────────┘   │
├─────────────────────────────────────────────────────┤
│  最近事件流（按时间倒序，最多 50 条）                │
└─────────────────────────────────────────────────────┘
```

#### 交互

- 启动 / 停止 / 重启 按钮
- 点击后弹二次确认 Modal
- 二次确认后 POST
- WebSocket 实时刷新
- 点击模块行展开详情（LifecycleTask 列表 / 依赖图）

### 7.2 模块详情弹窗

```
模块名：Memory System
module_id: memory_system
版本：5.0.0
类别：brain
critical: 是

状态：RUNNING
启动时间：2026-08-01 10:00:00
最后 tick：2026-08-01 12:00:00（1.5s 前）
错误次数：0
重启次数：0

依赖：无
被依赖：Emotion / Growth / Reflection

LifecycleTask 列表：
  - memory_lifecycle_task  RUNNING  last_tick=1.5s
  - memory_relevance_task  RUNNING  last_tick=10.2s

最近事件：
  - 2026-08-01 10:00:00  MODULE_STARTED  by admin
  - 2026-08-01 09:00:00  MODULE_REGISTERED

操作：[停止]  [重启]
```

### 7.3 实时刷新

- WebSocket 推送 module_state_changes
- 前端收到推送后局部更新表格
- 错误状态红色高亮
- 核心模块停止需输入二次确认词

---

## 8. 生命周期流程

### 8.1 应用启动时

```
app.startup()
   ↓
ModuleRegistry.discover_all()  ← 扫描预置模块
   ↓
对每个 module_id：
   ModuleRegistry.register(definition)
   发布 MODULE_REGISTERED
   ↓
ModuleManager.bootstrap()
   ↓
读取持久化快照 data/runtime/modules.json
   ↓
对每个 enabled=true 且 previous_state=started 的 module：
   ModuleManager.start(module_id)
   ↓
完成
```

### 8.2 运行时启停（Dashboard）

```
Dashboard 点击 [启动 Growth]
   ↓
前端二次确认
   ↓
POST /api/dashboard/v2/module/growth_system/start
   ↓
ModuleProvider.start(growth_system, operator, reason)
   ↓
ModuleManager.start(growth_system)
   ↓
Permission Check（Phase 5.0-E）
   ↓
LifecycleController.start()
   ↓
更新 ModuleState
   ↓
发布 INTEGRATION_MODULE_STARTED
   ↓
返回成功
   ↓
WebSocket 推送 module_state_changes
   ↓
Dashboard 自动更新
```

### 8.3 运行时依赖

- 启动 A 前必须确保 A 的 dependencies 全部 RUNNING
- 停止 A 前必须确保 A 不是其他模块的依赖（或先停止其他模块）
- 启动 / 停止失败时回滚状态

### 8.4 异常处理

- 启动失败：状态回滚到 REGISTERED，发布 INTEGRATION_MODULE_ERROR
- 运行中异常：模块自更新 state.last_error / state.error_count，发布 INTEGRATION_MODULE_ERROR
- 异常超过阈值：状态变为 ERROR
- 连续失败超过 3 次：自动 disable

### 8.5 持久化

- 每次状态变化后写 `data/runtime/modules.json`（append-only 风格）
- 文件结构：
  ```json
  {
      "schema_version": "1.0",
      "modules": {
          "memory_system": {
              "module_id": "memory_system",
              "status": "running",
              "enabled": true,
              "started_at": "...",
              "restart_count": 0
          }
      },
      "events": [
          {
              "event_id": "evt_xxx",
              "module_id": "memory_system",
              "event_type": "MODULE_STARTED",
              "timestamp": "..."
          }
      ]
  }
  ```
- 应用启动时读取用于恢复，但运行时状态以 ModuleManager 为准
- 文件不阻塞运行（写失败时记录日志）

---

## 9. 安全设计

### 9.1 权限分级

| 操作                       | 所需权限         |
|----------------------------|------------------|
| 查看模块列表 / 状态        | 任何已登录        |
| 启动 / 重启非核心模块      | MODULE_CONTROL   |
| 停止非核心模块             | MODULE_CONTROL   |
| 启用 / 禁用模块            | MODULE_CONTROL   |
| 启动 / 停止核心模块        | MODULE_CRITICAL  |
| 强制 kill 模块             | MODULE_FORCE     |
| 修改 ModuleDefinition      | 不允许（只读）   |

### 9.2 二次确认

- 所有启动 / 停止 / 重启 必须二次确认
- 核心模块停止需输入"STOP-<module_id>"
- 二次确认信息包含：
  - 模块名
  - 当前状态
  - 依赖关系
  - 风险提示
  - 操作员

### 9.3 黑名单 / 白名单

- 启动白名单：只允许启动 ModuleRegistry 中已注册的模块
- 禁止启动未注册的"未知模块"
- 禁止修改 ModuleDefinition

### 9.4 审计

- 所有 POST 接口记录 audit log
- 字段：
  - operator
  - module_id
  - action
  - reason
  - timestamp
  - result
- audit log 写入 src/admin/core/audit.py

### 9.5 危险操作提示

- 停止 Memory System：提示"将影响所有依赖记忆的功能"
- 停止 Lifecycle Core：提示"将影响所有模块生命周期调度"
- 停止 Agent Body Permission：提示"将影响所有身体层动作"

---

## 10. IntegrationEvent 扩展

### 10.1 新增事件类型

在 `src/runtime/integration/integration_event.py` 扩展 `ALL_INTEGRATION_EVENT_TYPES`：

```python
INTEGRATION_MODULE_REGISTERED = "integration.module.registered"
INTEGRATION_MODULE_STARTED = "integration.module.started"
INTEGRATION_MODULE_STOPPED = "integration.module.stopped"
INTEGRATION_MODULE_RESTARTED = "integration.module.restarted"
INTEGRATION_MODULE_ENABLED = "integration.module.enabled"
INTEGRATION_MODULE_DISABLED = "integration.module.disabled"
INTEGRATION_MODULE_ERROR = "integration.module.error"
INTEGRATION_MODULE_HEALTH_CHANGED = "integration.module.health_changed"
```

### 10.2 事件载荷

```python
@dataclass
class IntegrationEvent:
    event_id: str
    event_type: str
    source: str = "runtime.modules"
    timestamp: str
    payload: Dict[str, Any]
    evidence: List[str] = field(default_factory=list)
    related_ids: List[str] = field(default_factory=list)
    module_id: Optional[str] = None
    old_state: Optional[str] = None
    new_state: Optional[str] = None
    reason: Optional[str] = None
```

### 10.3 兼容性

- 现有事件类型完全保留
- 仅追加 Module 事件
- 不修改事件加载逻辑

---

## 11. 测试方案

### 11.1 测试规模

- ModuleState：>= 20
- ModuleRegistry：>= 30
- ModuleManager：>= 50
- LifecycleController：>= 50
- Integration：>= 30
- Dashboard API：>= 30

总计：>= 210 个测试

### 11.2 ModuleState 测试

- 创建合法 ModuleState
- 字段校验
- 状态转换合法性
- 序列化 / 反序列化
- 错误计数累加
- 重启计数累加
- 健康状态推断

### 11.3 ModuleRegistry 测试

- 注册合法 ModuleDefinition
- 重复注册（IGNORE_DUPLICATE / REJECT_DUPLICATE）
- 注册非法 module_id
- 列出所有模块
- 按 category 列出
- 依赖解析（直接依赖 / 传递依赖）
- 检测循环依赖
- 注销模块

### 11.4 ModuleManager 测试

- 启动模块（正常路径）
- 启动模块（已 RUNNING）
- 启动模块（依赖未满足）
- 启动模块（无权限）
- 启动模块（核心模块）
- 启动模块（异常路径）
- 停止模块（正常路径）
- 停止模块（未 RUNNING）
- 停止模块（有依赖者）
- 停止模块（异常路径）
- 重启模块
- 启用 / 禁用
- 获取状态
- 获取所有状态
- 获取健康
- 异常恢复

### 11.5 LifecycleController 测试

- 创建实例（factory）
- 注入 RuntimeContext
- 注册 LifecycleTask
- 注销 LifecycleTask
- 调用 on_start 钩子
- 调用 on_stop 钩子
- 销毁实例
- factory 异常
- 注入异常
- 注册 LifecycleTask 失败回滚
- 销毁异常

### 11.6 Integration 测试

- 完整启动流程（register → start → running）
- 完整停止流程（running → stop → stopped）
- 重启流程
- 状态变化产生 IntegrationEvent
- WebSocket 推送
- 持久化文件写入
- 恢复时读取持久化
- 错误状态发布错误事件
- 异常不导致其他模块受影响

### 11.7 Dashboard API 测试

- GET /api/dashboard/v2/module/list
- GET /api/dashboard/v2/module/<id>/status
- GET /api/dashboard/v2/module/<id>/tasks
- GET /api/dashboard/v2/module/<id>/events
- POST /api/dashboard/v2/module/<id>/start
- POST /api/dashboard/v2/module/<id>/stop
- POST /api/dashboard/v2/module/<id>/restart
- POST /api/dashboard/v2/module/<id>/enable
- POST /api/dashboard/v2/module/<id>/disable
- 错误码正确性
- audit log 记录
- 二次确认

### 11.8 安全测试

- 未注册模块无法启动
- 无权限操作被拒绝
- 二次确认缺失被拒绝
- 核心模块需要更高权限
- audit log 完整
- 危险操作有提示

### 11.9 回归测试

- 不影响现有 /api/ 路径
- 不影响现有 LifecycleTask
- 不影响现有 IntegrationEvent
- 不影响现有 RuntimeCore 集成
- 不修改业务模块代码

---

## 12. 实施步骤

### Step 1：数据模型与契约

新增：
- `src/contracts/module_state_schema.py`
- `src/contracts/module_event_schema.py`
- `src/runtime/modules/__init__.py`
- `src/runtime/modules/module_state.py`
- `src/runtime/modules/module_definition.py`
- `src/runtime/modules/module_instance.py`

修改：
- `src/runtime/integration/integration_event.py`（追加事件类型）

风险：低
测试：数据模型测试

### Step 2：Registry 与 Manager

新增：
- `src/runtime/modules/module_registry.py`
- `src/runtime/modules/module_manager.py`

风险：中（需要保证并发安全）
测试：Registry / Manager 测试

### Step 3：LifecycleController

新增：
- `src/runtime/modules/lifecycle_controller.py`
- `src/runtime/modules/module_adapter.py`（业务模块适配基类）

风险：中
测试：LifecycleController 测试

### Step 4：IntegrationEvent 集成

新增：
- `src/runtime/modules/module_event_emitter.py`

修改：
- `src/runtime/integration/integration_event_log.py`（兼容扩展）

风险：中
测试：事件发布测试

### Step 5：内置模块注册

新增：
- `src/runtime/modules/builtin/`：内置模块的 adapter

包含：
- memory_module.py
- emotion_module.py
- growth_module.py
- personality_module.py
- relationship_module.py
- selfmodel_module.py
- reflection_module.py
- initiative_module.py
- goal_module.py
- agent_body_*.py（Phase 5.0-E 预留）

风险：中（必须确保不破坏现有功能）
测试：内置模块启动 / 停止 / 重启

### Step 6：Dashboard Provider

新增：
- `src/admin/dashboard_provider.py`（已有占位）
- `src/admin/module_provider.py`（新增）

修改：
- `src/admin/api/routes.py`（追加 v2 路由）

风险：中
测试：API 测试

### Step 7：Dashboard UI

新增：
- `static/admin/dashboard_v2/pages/module.html`
- `static/admin/dashboard_v2/js/module.js`
- `static/admin/dashboard_v2/css/module.css`

风险：低
测试：UI 集成测试

### Step 8：持久化

新增：
- `src/runtime/modules/module_persistence.py`

修改：
- `data/runtime/modules.json`（运行时自动创建）

风险：低
测试：持久化测试

### Step 9：安全与权限

新增：
- `src/admin/permissions/module_permission.py`

修改：
- Phase 5.0-E Permission 集成

风险：中
测试：权限测试

### Step 10：验收与文档

新增：
- `docs/PHASE_5_0_MODULE_RUNTIME_CONTROL_REPORT.md`
- `docs/module_control.md`

---

## 13. 验收标准

### 13.1 功能验收

- [ ] 所有内置模块可启动 / 停止 / 重启
- [ ] 启动后自动注册 LifecycleTask
- [ ] 停止后自动清理 LifecycleTask
- [ ] 状态可被 Dashboard 实时查看
- [ ] 状态变化产生 IntegrationEvent
- [ ] WebSocket 实时推送
- [ ] 持久化文件可恢复

### 13.2 性能验收

- [ ] 模块启动 < 1s
- [ ] 模块停止 < 1s
- [ ] WebSocket 推送延迟 < 500ms
- [ ] 状态查询 API < 200ms

### 13.3 安全验收

- [ ] 二次确认必须
- [ ] 未注册模块不可启动
- [ ] 核心模块停止需更高权限
- [ ] 所有操作记录 audit log

### 13.4 兼容性验收

- [ ] 不修改 src/memory/**
- [ ] 不修改 src/growth/**
- [ ] 不修改 src/emotion/**
- [ ] 不修改 src/personality/**
- [ ] 不修改 src/relationship/**
- [ ] 不修改 src/runtime/lifecycle/**
- [ ] 不修改 src/runtime/self_model/**
- [ ] 不修改 src/runtime/reflection/**
- [ ] 不修改 src/runtime/initiative/**
- [ ] 不修改 src/runtime/goal/**
- [ ] 不修改 src/runtime/integration/integration_event.py 现有事件
- [ ] 不修改 config.yaml

### 13.5 测试验收

- [ ] 新增测试 >= 210 个
- [ ] 单元测试 100% 通过
- [ ] 集成测试 100% 通过
- [ ] 安全测试 100% 通过
- [ ] 回归测试 100% 通过

### 13.6 扩展性验收

- [ ] 未来 Agent Body 整体可作为 Module 接入
- [ ] 未来 Vision / Voice 可作为 Module 接入
- [ ] ModuleDefinition 可动态扩展

---

## 14. 设计边界

本设计：

- 只新增模块运行时控制层
- 不修改业务模块
- 不修改 Lifecycle Core
- 不修改现有 IntegrationEvent 加载逻辑
- 不修改现有 Dashboard
- 通过 adapter 模式接入业务模块
- 为未来 Agent OS 留出扩展空间

---

等待确认。
