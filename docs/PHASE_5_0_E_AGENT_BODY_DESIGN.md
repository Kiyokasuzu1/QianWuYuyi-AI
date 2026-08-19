# Phase 5.0-E Agent Body System 设计文档

文档版本：v1.0
适用范围：Phase 5.0-E（架构设计阶段）
本阶段不写任何代码，仅输出架构设计等待确认。

---

## 1. 当前系统状态

### 1.1 已经具备的能力

羽依当前已经形成"脑"层为主的认知体系，包含：

- Goal：长期方向、Desire、GoalState、GoalManager、GoalPlan
- Initiative：PossibleAction、InitiativeEngine、ActionFilter
- Memory：长期记忆、相关性评估、合并与衰减
- Reflection：自我反思引擎、事件反思、成长反思
- SelfModel：身份理解、稳定性、连续性
- Personality：人格画像、演化管线、稳定性门控
- Emotion：情绪动力学、记忆桥接
- Relationship：关系模型、互动历史、信任与里程碑
- Growth：提案、审批、人格演化
- Lifecycle：生命周期管理、状态机、任务调度
- Integration：统一事件总线、跨模块事件桥接
- Perception：Observation、Fact、Vision 适配层（只读、不执行）

### 1.2 当前缺口

羽依目前仍是"只有大脑，没有身体"的形态：

- 没有 Eye：所有 Observation 都来自历史数据，不是真实的感知
- 没有 Hand：没有 Action 执行能力
- 没有 Permission Boundary：没有统一的"动作-权限"边界
- 没有 Action Executor：没有安全动作执行层
- 没有 ActionResult：动作执行后没有结构化结果记录
- 没有 Body Lifecycle Task：没有身体层的周期检查

### 1.3 本阶段目标

只建立"身体层（Body System）"的安全执行架构：

- 任何动作必须经过 Brain → Proposal → Permission → Executor → Result
- 任何模块不得直接操作现实
- 第一阶段仅实现 MockExecutor，禁止真实操作系统、浏览器、网络
- 默认权限：LEVEL_1（读取类）
- 目标：未来可平滑接入真实能力而不破坏现有认知体系

### 1.4 禁止事项（本阶段及后续实现阶段均生效）

禁止修改：

- src/memory/**
- src/growth/**
- src/personality/**
- src/emotion/**
- src/relationship/**
- src/runtime/lifecycle/**
- src/runtime/self_model/**
- src/runtime/reflection/**
- src/runtime/initiative/**
- src/runtime/goal/**

禁止使用：

- LLM
- 浏览器自动化
- 网络请求
- 操作系统控制
- 第三方依赖

---

## 2. Agent Body 总体架构

### 2.1 核心理念

```
Goal（我想朝哪里发展）
   ↓
Initiative（我可以做什么）
   ↓
ActionProposal（我请求做什么）
   ↓
PermissionCheck（我被允许做吗）
   ↓
Executor（我执行）
   ↓
Result（发生了什么）
   ↓
Memory / Reflection（这成为我的经历）
   ↓
SelfModel（我因此改变）
```

形成"想法 → 行动 → 经历 → 成长"完整闭环。

### 2.2 架构原则

所有行动必须经过：

```
Agent Brain
   ↓
Action Proposal
   ↓
Permission Layer
   ↓
Action Executor
   ↓
Action Result
   ↓
Integration Event
```

任何模块不得直接操作现实。

### 2.3 整体组件图

```
                       ┌────────────────────┐
                       │   Agent Brain      │
                       │ (Goal / Initiative)│
                       └─────────┬──────────┘
                                 │ ActionProposal
                                 ▼
                       ┌────────────────────┐
                       │  Permission Layer  │
                       │ (Manager + Rules)  │
                       └─────────┬──────────┘
                                 │ allow / deny
                                 ▼
                       ┌────────────────────┐
                       │  Action Executor   │
                       │   (Mock / Safe)    │
                       └─────────┬──────────┘
                                 │ ActionResult
                                 ▼
                       ┌────────────────────┐
                       │ Integration Event  │
                       │  (Body Adapter)    │
                       └─────────┬──────────┘
                                 │
                                 ▼
                       ┌────────────────────┐
                       │  Memory / Reflect  │
                       │  SelfModel update  │
                       └────────────────────┘

并行组件：
                       ┌────────────────────┐
                       │    Eye System      │
                       │  (Observation)     │
                       └─────────┬──────────┘
                                 │ ObservationEvent
                                 ▼
                       ┌────────────────────┐
                       │ Observation State  │
                       │  (Body State)      │
                       └────────────────────┘
```

### 2.4 数据流总览

```
Goal
  ↓
Initiative
  ↓
PossibleAction
  ↓
ActionProposal    ←─── Body Lifecycle Task 周期调度
  ↓
PermissionCheck
  ↓ allow
Executor (Mock)
  ↓
ActionResult
  ↓
Memory (experience)
  ↓
Reflection
  ↓
SelfModel
```

---

## 3. Eye System（观察系统）

### 3.1 设计目标

模拟"眼睛"。

第一阶段仅接受结构化 Observation，不连接摄像头、屏幕、麦克风。

未来可平滑接入视觉适配层。

### 3.2 Observation 数据模型

```python
class ObservationType(str, Enum):
    TEXT = "text"             # 文本输入
    SYSTEM = "system"         # 系统级观察
    MANUAL = "manual"         # 人工注入
    FUTURE_SCREEN = "future_screen"  # 未来屏幕读数（预留）


@dataclass
class ObservationEvent:
    observation_id: str
    observation_type: ObservationType
    content_summary: str
    source: str               # 必填：来源标识
    timestamp: str
    confidence: float
    evidence_ids: List[str]   # 可选：支撑该 observation 的原始证据 id
    meta: Dict[str, Any]      # 可选：附加元数据
```

### 3.3 字段约束

- observation_id：唯一，格式 obs_xxxx
- observation_type：必须是 ObservationType 枚举
- source：必填，禁止为空字符串
- confidence：0.0 ~ 1.0
- timestamp：ISO 8601 UTC
- content_summary：观察内容的摘要描述
- evidence_ids：可选，用于未来追溯

### 3.4 观察类型说明

- TEXT：来自人工输入的文本观察（例如用户说"今天看到一段视频"）
- SYSTEM：来自系统事件（窗口标题变化、文件打开等）
- MANUAL：调试或管理后台注入的观察
- FUTURE_SCREEN：未来屏幕读数（当前不实现，仅占位）

### 3.5 Eye System 子模块

```
observation/
├── __init__.py
├── observation_event.py      # ObservationEvent 数据模型
├── observation_state.py      # ObservationState（汇总状态）
└── observation_manager.py    # ObservationManager（注册、查询、校验）
```

#### ObservationManager 职责

- register(observation)：注册一条 Observation，校验字段
- get_by_id(observation_id)：按 id 查询
- list_recent(limit, type_filter)：列出最近观察
- get_state()：返回 ObservationState 快照
- clear()：清空（仅测试用）

#### ObservationState 职责

- observation_count：累计观察数
- by_type：按类型分组计数
- last_observation_id：最近一次观察 id
- last_observation_at：最近一次观察时间

### 3.6 边界

- 不连接摄像头
- 不读取屏幕
- 不解析图像
- 不调用 LLM
- 不写入 Memory（仅产出 ObservationEvent）

---

## 4. Hand System（行动系统）

### 4.1 设计目标

表达"我想做什么"。

仅产出 ActionProposal，不执行任何动作。

### 4.2 ActionProposal 数据模型

```python
class ActionType(str, Enum):
    OBSERVE = "observe"       # 观察类
    SEARCH = "search"         # 信息检索
    LEARN = "learn"           # 学习类
    ORGANIZE = "organize"     # 内部整理
    COMMUNICATE = "communicate"  # 对外交流


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ActionProposal:
    action_id: str
    action_type: ActionType
    description: str
    reason: str
    source_goal_ids: List[str]  # 来源 Goal 列表
    confidence: float           # 0.0 ~ 1.0
    risk_level: RiskLevel
    timestamp: str
    meta: Dict[str, Any]
```

### 4.3 字段约束

- action_id：唯一，格式 act_xxxx
- action_type：必须是 ActionType 枚举
- source_goal_ids：至少 1 个（防止无目的行动）
- confidence：0.0 ~ 1.0
- risk_level：必须是 RiskLevel 枚举
- description、reason：非空

### 4.4 Hand System 子模块

```
action/
├── __init__.py
├── action_request.py      # ActionProposal 数据模型
├── action_result.py       # ActionResult 数据模型
├── action_type.py         # ActionType / RiskLevel 枚举
└── action_manager.py      # ActionManager（Proposal 注册、状态机）
```

#### ActionManager 职责

- register_proposal(proposal)：注册一个 ActionProposal
- get_proposal(action_id)：按 id 查询
- list_pending()：列出待处理 Proposal
- mark_status(action_id, status)：更新状态
- get_state()：返回状态快照

### 4.5 状态机

```
PROPOSED
   ↓ permission_check
ALLOWED
   ↓ executor
EXECUTING
   ↓ result
COMPLETED / FAILED / DENIED / CANCELLED
```

### 4.6 边界

- 不执行任何动作
- 不调用 Executor
- 不通过 Permission
- 仅产出和跟踪 ActionProposal

---

## 5. Permission System（权限系统）

### 5.1 设计目标

这是 Body System 的核心安全闸门。

所有 Action 必须经过 PermissionManager。

### 5.2 权限等级

```python
class PermissionLevel(int, Enum):
    LEVEL_0 = 0   # 完全禁止
    LEVEL_1 = 1   # 读取类
    LEVEL_2 = 2   # 内部整理
    LEVEL_3 = 3   # 低风险操作
    LEVEL_4 = 4   # 外部交互
    LEVEL_5 = 5   # 高风险操作
```

默认等级：LEVEL_1（仅允许读取类动作）

### 5.3 PermissionLevel 与 ActionType 的映射

| ActionType    | 最低允许等级 |
|---------------|--------------|
| OBSERVE       | LEVEL_1      |
| ORGANIZE      | LEVEL_2      |
| LEARN         | LEVEL_2      |
| SEARCH        | LEVEL_3      |
| COMMUNICATE   | LEVEL_4      |

### 5.4 PermissionRule 数据模型

```python
@dataclass
class PermissionRule:
    rule_id: str
    action_type: ActionType
    allowed: bool
    require_confirmation: bool
    created_at: str
    reason: str
    meta: Dict[str, Any]
```

### 5.5 默认规则集（v1.0）

| action_type  | allowed | require_confirmation | reason                  |
|--------------|---------|----------------------|-------------------------|
| OBSERVE      | True    | False                | 读取类，默认允许         |
| ORGANIZE     | True    | False                | 内部整理                 |
| LEARN        | True    | False                | 内部学习                 |
| SEARCH       | True    | True                 | 涉及网络，需确认         |
| COMMUNICATE  | False   | True                 | 外部交互，默认禁止       |

### 5.6 PermissionManager 职责

- check(action_type, current_level)：检查是否允许
- require_confirmation(action_type)：是否需要确认
- get_rule(action_type)：获取规则
- list_rules()：列出所有规则
- get_policy()：返回 PermissionPolicy 快照

### 5.7 PermissionPolicy 数据模型

```python
@dataclass
class PermissionPolicy:
    current_level: PermissionLevel
    default_level: PermissionLevel
    rules: List[PermissionRule]
    updated_at: str
```

### 5.8 决策逻辑

```
is_allowed = rule.allowed AND current_level >= required_level
require_confirmation = rule.require_confirmation OR risk_level >= HIGH
```

### 5.9 边界

- 不执行任何动作
- 不修改任何状态（仅返回决策）
- 不与现有 src/permission 冲突（本模块独立于现有权限系统）
- 不可降级到 LEVEL_0

---

## 6. ActionExecutor（动作执行器）

### 6.1 设计目标

执行被允许的动作。

第一阶段：只实现 MockExecutor。

禁止任何真实操作系统、浏览器、网络。

### 6.2 Executor 接口

```python
class Executor(Protocol):
    def execute(self, proposal: ActionProposal) -> "ExecutorResult":
        ...
```

### 6.3 MockExecutor 设计

- 不发起任何网络请求
- 不访问任何文件
- 不调用任何系统命令
- 仅返回模拟结果

### 6.4 MockExecutor 行为表

| ActionType    | 模拟行为                                                    |
|---------------|-------------------------------------------------------------|
| OBSERVE       | 返回模拟的观察内容（来自预置 fixture）                       |
| SEARCH        | 返回模拟搜索结果（静态文本）                                 |
| LEARN         | 返回学习完成标记                                             |
| ORGANIZE      | 返回整理完成标记                                             |
| COMMUNICATE   | 拒绝执行（默认权限禁止）                                     |

### 6.5 ExecutorResult 数据模型

```python
class ExecutorStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    DENIED = "denied"
    CANCELLED = "cancelled"


@dataclass
class ExecutorResult:
    action_id: str
    status: ExecutorStatus
    result_summary: str
    error: Optional[str]
    timestamp: str
    evidence: List[str]  # 执行证据 id
    meta: Dict[str, Any]
```

### 6.6 SafeExecutor 设计

SafeExecutor 包装 MockExecutor，强制执行以下约束：

- 输入必须是已通过 Permission 的 ActionProposal
- 拒绝任何未授权的 proposal
- 记录每次执行的安全审计日志
- 不允许执行非白名单 action_type

### 6.7 Executor 子模块

```
executor/
├── __init__.py
├── executor.py         # Executor 接口定义
├── executor_result.py  # ExecutorResult 数据模型
└── safe_executor.py    # SafeExecutor 包装器
```

### 6.8 边界

- 不连接网络
- 不读写文件
- 不执行系统命令
- 不调用 LLM
- 不调用浏览器自动化
- MockExecutor 完全确定性，相同输入产生相同输出

---

## 7. Lifecycle 集成设计

### 7.1 BodyLifecycleTask

新增一个 LifecycleTask：

```
BodyLifecycleTask
  ↓
tick()
  ↓
读取 pending ActionProposal
  ↓
PermissionCheck
  ↓ allow
SafeExecutor.execute()
  ↓
ExecutorResult
  ↓
emit IntegrationEvent
```

### 7.2 Task 行为约束

- 不自动创建 ActionProposal（由 Initiative 产生）
- 不自动修改 Permission
- 不自动调用 Executor（仅在 Proposal 已存在且 Permission 通过时）
- 每次 tick 处理 proposal 数量有上限（默认 3）
- 失败 proposal 进入 pending，下一 tick 再处理

### 7.3 Task 调度

- 由 LifecycleManager 调度
- 默认每 30s 一次
- 启动、停止、暂停遵循 LifecycleTask 接口
- 任务状态保存到 LifecycleState

### 7.4 Task 异常处理

- 任何模块异常不导致 RuntimeCore 崩溃
- 异常记录到 audit log
- 单次 tick 异常后下一 tick 继续

### 7.5 Task 与其他 LifecycleTask 共存

- 不与 emotion_lifecycle_task、goal_lifecycle_task、reflection_lifecycle_task 等冲突
- 不修改现有 lifecycle 行为
- 仅新增一个独立 Task

---

## 8. Event 设计

### 8.1 IntegrationEvent 新增类型

```python
class IntegrationEventType(str, Enum):
    # ... 已有事件 ...
    INTEGRATION_OBSERVATION_CREATED = "integration_observation_created"
    INTEGRATION_ACTION_PROPOSED = "integration_action_proposed"
    INTEGRATION_PERMISSION_CHECKED = "integration_permission_checked"
    INTEGRATION_ACTION_COMPLETED = "integration_action_completed"
    INTEGRATION_ACTION_DENIED = "integration_action_denied"
```

### 8.2 事件负载

所有事件必须包含：

```python
{
    "event_id": str,
    "event_type": IntegrationEventType,
    "source": str,           # 必填：来源模块
    "timestamp": str,
    "evidence": List[str],   # 相关证据 id
    "related_ids": List[str] # 相关 action_id / observation_id / goal_id
}
```

### 8.3 事件来源

| 事件                              | source                  |
|-----------------------------------|-------------------------|
| INTEGRATION_OBSERVATION_CREATED   | agent_body.observation  |
| INTEGRATION_ACTION_PROPOSED       | agent_body.action       |
| INTEGRATION_PERMISSION_CHECKED    | agent_body.permission   |
| INTEGRATION_ACTION_COMPLETED      | agent_body.executor     |
| INTEGRATION_ACTION_DENIED         | agent_body.permission   |

### 8.4 Event 落地

- 写入 IntegrationEventLog
- 兼容现有 event_schema
- 不修改现有事件结构
- 不影响现有事件消费者

---

## 9. 文件规划

### 9.1 新增目录

```
src/runtime/agent_body/
├── __init__.py
│
├── observation/
│   ├── __init__.py
│   ├── observation_event.py        # ObservationEvent 数据模型
│   ├── observation_state.py        # ObservationState 状态
│   └── observation_manager.py      # ObservationManager 管理
│
├── action/
│   ├── __init__.py
│   ├── action_request.py           # ActionProposal 数据模型
│   ├── action_result.py            # ActionResult 数据模型
│   ├── action_type.py              # ActionType / RiskLevel 枚举
│   └── action_manager.py           # ActionManager 管理
│
├── permission/
│   ├── __init__.py
│   ├── permission_rule.py          # PermissionRule 数据模型
│   ├── permission_manager.py       # PermissionManager 决策
│   └── permission_policy.py        # PermissionPolicy 快照
│
├── executor/
│   ├── __init__.py
│   ├── executor.py                 # Executor 接口
│   ├── executor_result.py          # ExecutorResult 数据模型
│   └── safe_executor.py            # SafeExecutor 安全包装
│
├── adapter/
│   ├── __init__.py
│   ├── body_adapter.py             # BodyAdapter（接入 Runtime）
│   └── body_event_emitter.py       # EventEmitter（发射 IntegrationEvent）
│
└── lifecycle/
    ├── __init__.py
    └── body_lifecycle_task.py      # BodyLifecycleTask 周期任务
```

### 9.2 配套新增

- `src/contracts/agent_body_schema.py`：Body 相关所有数据模型契约
- `tests/test_agent_body_observation.py`
- `tests/test_agent_body_action.py`
- `tests/test_agent_body_permission.py`
- `tests/test_agent_body_executor.py`
- `tests/test_agent_body_lifecycle.py`
- `tests/runtime/test_agent_body_integration.py`

### 9.3 不修改

- src/memory/**
- src/growth/**
- src/personality/**
- src/emotion/**
- src/relationship/**
- src/runtime/lifecycle/**
- src/runtime/self_model/**
- src/runtime/reflection/**
- src/runtime/initiative/**
- src/runtime/goal/**
- src/permission/** （已有权限系统保留，新增 body 权限为独立模块）
- src/runtime/integration/integration_event.py （仅扩展枚举值）
- src/runtime/integration/integration_event_log.py （兼容扩展）
- config.yaml（API Key 不动）

---

## 10. 测试验收标准

### 10.1 测试规模

- 新增测试总数 >= 200
- 单元测试与集成测试比例约 7:3

### 10.2 Observation 测试

- 创建合法 ObservationEvent
- 字段校验（observation_id、type、source、confidence）
- 序列化 / 反序列化（to_dict / from_dict）
- source 必填校验
- confidence 范围校验
- ObservationManager 注册 / 查询
- ObservationState 快照

### 10.3 Action 测试

- 创建合法 ActionProposal
- source_goal_ids 至少 1 个校验
- 状态机转换（PROPOSED → ALLOWED → EXECUTING → COMPLETED/FAILED/DENIED/CANCELLED）
- ActionManager 注册 / 查询
- 序列化 / 反序列化

### 10.4 Permission 测试

- allow 决策（rule.allowed=True 且等级足够）
- deny 决策（rule.allowed=False）
- require_confirmation 决策
- 等级不足时 deny
- 等级足够时 allow
- 默认等级 LEVEL_1
- PermissionManager 规则增删改查
- PermissionPolicy 快照

### 10.5 Executor 测试

- MockExecutor 各种 action_type 行为
- 失败场景（unknown action_type）
- SafeExecutor 拒绝未授权 proposal
- SafeExecutor 拒绝非白名单 action_type
- ExecutorResult 字段正确性
- 隔离性：相同输入产生相同输出
- 不调用网络、文件、系统命令（mock 验证）

### 10.6 Integration 测试

完整数据流：

```
Goal
  ↓
ActionProposal
  ↓
PermissionCheck
  ↓
Executor
  ↓
ActionResult
  ↓
IntegrationEvent
```

- 正常流：proposal → allow → execute → success → event
- 拒绝流：proposal → deny → event (DENIED)
- 失败流：proposal → allow → execute → failed → event (FAILED)
- 事件载荷完整性（source / timestamp / evidence / related_ids）

### 10.7 Lifecycle 测试

- BodyLifecycleTask 启动 / 停止 / tick
- tick 不会自动创建 proposal
- tick 在 proposal 存在时执行 permission check
- tick 在 allow 时调用 executor
- tick 在 deny 时只发事件
- tick 异常不影响下次 tick
- 与现有 lifecycle task 共存

### 10.8 安全测试

- 禁止执行未授权动作
- 禁止在 PermissionLevel=LEVEL_0 时执行任何动作
- 禁止 COMMUNICATE 在默认等级下执行
- 禁止 SEARCH 在无确认时执行
- MockExecutor 不发起任何网络请求（patch 验证）
- SafeExecutor 拒绝所有非白名单 action_type

### 10.9 回归测试

- 不影响现有 emotion_lifecycle_task
- 不影响现有 goal_lifecycle_task
- 不影响现有 reflection_lifecycle_task
- 不影响现有 personality_lifecycle_task
- 不影响现有 integration_event 已有事件类型
- 不影响现有 permission 模块

### 10.10 验收检查清单

- [ ] 所有新增文件不修改禁止目录
- [ ] 所有新模块无 LLM / 网络 / 浏览器 / OS / 第三方依赖调用
- [ ] 新增测试通过率 100%
- [ ] 新增测试 >= 200
- [ ] 单元测试与集成测试合理分布
- [ ] 安全测试覆盖所有禁止路径
- [ ] 回归测试覆盖现有 lifecycle / integration 关键路径
- [ ] config.yaml 未被修改
- [ ] 所有新增代码符合 PEP 8

---

## 11. 实施顺序（待确认设计后启动）

### Step 1：契约与数据模型

- agent_body_schema.py
- observation_event.py
- action_request.py / action_result.py / action_type.py
- permission_rule.py
- executor_result.py

### Step 2：管理类

- observation_manager.py
- action_manager.py
- permission_manager.py / permission_policy.py
- executor.py / safe_executor.py
- body_event_emitter.py
- body_adapter.py

### Step 3：Lifecycle 集成

- body_lifecycle_task.py
- LifecycleManager 注册

### Step 4：测试

- 单元测试
- 集成测试
- 安全测试
- 回归测试

### Step 5：验收报告

- PHASE_5_0_E_AGENT_BODY_COMPLETION_REPORT.md

---

## 12. 设计边界总结

本设计：

- 只建立"身体层"骨架，不连接任何真实外部能力
- 严格保持所有禁止事项
- 完全可测试、可审计、可回滚
- 与现有"脑"层完全解耦
- 默认权限为 LEVEL_1，最小可用面
- 所有动作必须经过 Permission，不允许任何"快速通道"

---

等待确认。
