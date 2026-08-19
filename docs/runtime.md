# Runtime 架构设计

**Phase**: 3.7.0 — Runtime Integration Design
**状态**: 设计阶段（接口骨架 / 架构冻结）
**前置**: Phase 3.6.x（Schema Governance Final Audit）
**后续**: Phase 3.7.1+ 业务实现

---

## 1. 目标

定义羽依 AI 的 **统一 Runtime 生命周期**：一个清晰、可测试、可演进的运行时架构，作为 Memory / Emotion / Personality / Growth 协同的主干。

**关键原则**：

- Runtime = 编排层（orchestration），不实现业务
- 业务模块 = Memory / Emotion / Personality / GrowthEngine，各自独立
- 依赖方向：Runtime → Contracts → 业务模块
- Runtime 通过 `RuntimeContext` 传递状态，不直接持有业务对象
- 所有事件经 `EventBus` 转发，Runtime 订阅 / 派发
- Schema 契约（canonical GrowthProposal 等）由 `src/contracts/*` 维护，Runtime 不修改

---

## 2. Runtime Core 职责

`RuntimeCore` 负责协调羽依的整个生命周期，**不**直接实现任何业务逻辑：

| 职责 | 说明 |
|------|------|
| **生命周期管理** | 启动 / 停止 / 暂停 / 恢复 / 优雅关闭 |
| **Event 调度** | 接收外部事件并按优先级派发 |
| **Context 管理** | 构建并下发 `RuntimeContext`，串联各模块 |
| **状态加载** | 启动时从持久化层加载 Memory / Emotion / Personality / Growth 快照 |
| **状态保存** | 周期或事件触发后将状态写回持久化层 |
| **模块协调** | 顺序调用 Memory → Emotion → Growth → Personality 形成一次"思维循环" |

**Runtime 不负责**：

- 不直接计算情感（→ Emotion 模块）
- 不直接生成记忆（→ Memory 模块）
- 不直接更新人格（→ Personality 模块）
- 不直接评估成长（→ GrowthEngine）
- 不直接做归一化（→ GrowthProposalNormalizer）

---

## 3. RuntimeContext

`RuntimeContext` 是一次"思维循环"中所有模块共享的上下文快照。

**字段（冻结于 v1.0）**：

| 字段 | 类型 | 来源 | 用途 |
|------|------|------|------|
| `session_id` | `str` | Runtime 生成 | 当前会话 ID |
| `user_input` | `str` | 外部事件载荷 | 用户原始输入 |
| `timestamp` | `str` | Runtime | ISO 8601 时间戳 |
| `memory_context` | `MemoryContext` | Memory 模块 | 检索结果 |
| `emotion_state` | `EmotionState` | Emotion 模块 | 情绪快照 |
| `personality_snapshot` | `PersonalitySnapshot` | Personality 模块 | 人格快照 |
| `growth_proposals` | `List[GrowthProposal]` | Growth 模块 | 已归一化的成长提议（canonical） |

> **重要**：RuntimeContext 仅持有对各模块产物的**引用**或**轻量快照**。Runtime 不复制也不缓存模块内部状态。

---

## 4. 生命周期

RuntimeCore 内部采用单进程事件循环，**显式阶段**如下：

```
START
  ↓
Load State        ← 从持久化层加载快照
  ↓
Receive Event     ← 接收外部 Event（用户消息 / 系统事件 / 定时事件）
  ↓
Memory Retrieval  ← 调用 Memory 模块：检索相关记忆
  ↓
Emotion Update    ← 调用 Emotion 模块：更新情绪状态
  ↓
Growth Evaluation ← 调用 GrowthEngine：基于事件生成 GrowthProposal
  ↓
Personality Update← 调用 Personality 模块：消费 GrowthProposal / 推进演化
  ↓
Response          ← 组装最终响应（由 Response Engine 消费 RuntimeContext）
  ↓
Persistence       ← 保存本次循环的 RuntimeContext 与各模块状态
```

**约束**：

- 每个阶段均为**可跳过**（按事件类型路由，例如定时事件跳过 Response 阶段）
- 每个阶段必须返回标准化的"阶段结果"，RuntimeCore 决定下一步
- 任一阶段异常不能破坏其他阶段（错误隔离）

---

## 5. 事件流

### 5.1 Event 基类

```python
@dataclass
class Event:
    id: str
    type: str             # "user_input" | "system" | "tick" | "growth_proposal"
    source: Optional[str]
    timestamp: str
    payload: Dict[str, Any]
    related_ids: List[str]
    priority: int         # 0=normal, 1=high, 2=critical
    metadata: Dict[str, Any]
```

### 5.2 Runtime 事件分类

| 类型 | 触发方 | 路由 |
|------|--------|------|
| `user_input` | UI / IM 桥 | 完整生命周期（含 Response） |
| `system` | Admin / 控制台 | 跳过 Response，触发治理 |
| `tick` | Scheduler | 周期状态同步 / 衰减 |
| `growth_proposal` | Growth 模块 | 路由到 Personality（短链路） |

---

## 6. 模块接口契约（Runtime 视角）

Runtime 通过 **Protocol**（结构子类型）依赖各模块，避免反向耦合：

```python
class MemoryPort(Protocol):
    def retrieve(self, ctx: RuntimeContext) -> MemoryContext: ...

class EmotionPort(Protocol):
    def update(self, ctx: RuntimeContext) -> EmotionState: ...

class GrowthPort(Protocol):
    def evaluate(self, ctx: RuntimeContext) -> List[GrowthProposal]: ...

class PersonalityPort(Protocol):
    def apply(self, ctx: RuntimeContext, proposals: List[GrowthProposal]) -> PersonalitySnapshot: ...
```

Runtime 只 import 这些 Protocol 定义所在的 `src.contracts.runtime_integration_schema` 等契约模块，**不** import 业务实现模块（`src.memory.memory_service` / `src.emotion.emotion_manager` / 等）。

---

## 7. 依赖方向

```
            ┌──────────────┐
            │   Runtime    │   ← 编排层（本设计）
            └──────┬───────┘
                   │ 依赖 Protocol
                   ↓
            ┌──────────────┐
            │  Contracts   │   ← 契约（schema / Protocol / 事件定义）
            └──────┬───────┘
                   │ 业务模块实现 Protocol
        ┌──────────┼──────────┬──────────┐
        ↓          ↓          ↓          ↓
    ┌───────┐  ┌────────┐  ┌────────┐  ┌────────┐
    │Memory │  │ Emotion│  │Growth  │  │Person- │
    │       │  │        │  │Engine  │  │ality   │
    └───────┘  └────────┘  └────────┘  └────────┘
```

**禁止**：

- 业务模块 import Runtime（单向依赖）
- 业务模块之间互相 import（通过 Runtime 协调）
- Runtime 直接 import 业务实现（只能 import Protocol）

---

## 8. 持久化边界

Runtime 不直接持久化各模块状态。Runtime 仅：

1. 启动时调用 `ModulePort.load_state()` 加载
2. 阶段结束时调用 `ModulePort.snapshot()` 触发各模块内部持久化
3. 不关心存储后端（文件 / DB / 向量库 / 内存均可）

---

## 9. 错误隔离

每个生命周期阶段必须 try/except 包裹，Runtime 记录 stage_error 但不中断后续阶段：

```python
try:
    memory_result = memory_port.retrieve(ctx)
    ctx.memory_context = memory_result
except Exception as e:
    runtime_log.stage_error("memory", e)
    ctx.memory_context = MemoryContext.empty()
```

最终 RuntimeContext 始终可被下游消费（Response / Persistence）。

---

## 10. 接口文件清单（本阶段交付）

| 文件 | 内容 | 类型 |
|------|------|------|
| `src/runtime/runtime.py` | `RuntimeCore` 类骨架 | 接口（无业务） |
| `src/runtime/context.py` | `RuntimeContext` dataclass | 数据契约 |
| `src/runtime/events.py` | `Event` 基类 / 事件分类枚举 | 数据契约 |
| `docs/runtime.md` | 本文档 | 设计文档 |
| `tests/test_phase_3_7_0_runtime_design.py` | 设计级测试 | 验证 |

---

## 11. 进入下一阶段（业务实现）的前置条件

- [x] RuntimeContext 字段冻结
- [x] 生命周期阶段序列冻结
- [x] Event 基类接口冻结
- [x] Module Port Protocol 形状定义（待 `src/contracts/runtime_integration_schema.py` 落地）
- [x] 不破坏 Phase 3.6.x 任何测试
- [x] Runtime 不 import 任何业务模块
- [x] Memory / Emotion / Personality / Growth 核心逻辑零修改

---

## 12. 与现有模块关系

| 现有模块 | 与 Runtime 关系 |
|----------|-----------------|
| `src/runtime/runtime_core.py`（既有实现） | **不动**。本设计为新一层的抽象，与既有 RuntimeCore 协同而非替换 |
| `src/runtime/runtime_bridge.py` | Runtime 与业务模块的"桥接点"，未来可基于本设计实现 |
| `src/runtime/adapters/*` | 已有 adapter 模式可复用为 Protocol 实现 |
| `src/memory/*` | 通过 `MemoryPort` 暴露能力 |
| `src/emotion/*` | 通过 `EmotionPort` 暴露能力 |
| `src/personality/*` | 通过 `PersonalityPort` 暴露能力 |
| `src/growth/*` | 通过 `GrowthPort` 暴露能力 |
| `src/contracts/*` | Runtime 直接 import 此层（Protocol / Schema） |

---

**版本**: v1.0（Phase 3.7.0 冻结）
**下一次评审**: 业务实现完成后
