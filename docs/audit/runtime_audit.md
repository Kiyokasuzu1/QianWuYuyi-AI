# Runtime Lifecycle Audit Report

**审计阶段：** Phase A.3.4
**审计范围：** `src/runtime/` + `src/orchestrator.py` + Runtime 生命周期
**审计目标：** 验证 Runtime 生命周期是否完整、可追踪、不会因为单点失败导致整体崩溃
**审计原则：** 不重构、不破坏现有功能；只识别问题、给出建议、必要时小修复

---

## 1. Runtime 生命周期阶段

**完整生命周期顺序（来自 `src/runtime/runtime.py` 第 110-131 行）：**

```
START
  ↓
LOAD_STATE
  ↓
RECEIVE_EVENT
  ↓
MEMORY_RETRIEVAL
  ↓
EMOTION_UPDATE
  ↓
GROWTH_EVALUATION
  ↓
PERSONALITY_UPDATE
  ↓
PERSONALITY_CONTEXT_BUILD
  ↓
PERCEPTION_OBSERVATION (默认 no-op)
  ↓
PERCEPTION_ANALYSIS (默认 no-op)
  ↓
SELF_MODEL_BUILD (默认 no-op)
  ↓
SELF_MODEL_EVOLUTION (默认 no-op)
  ↓
SELF_MODEL_REFLECTION (默认 no-op)
  ↓
SELF_MODEL_VALIDATION (默认 no-op)
  ↓
SELF_MODEL_PERSISTENCE (默认 no-op)
  ↓
RESPONSE_GENERATION
  ↓
GUARD_CHAIN
  ↓
RESPONSE
  ↓
PERSISTENCE
  ↓
SHUTDOWN
```

**总阶段数：** 20 个

**与用户期望的对照：**

| 用户期望 | Runtime 实际 | 是否满足 |
|---|---|---|
| Input Event | `RECEIVE_EVENT` | ✅ |
| Runtime Core | 完整 | ✅ |
| Event Bus | `EventEnvelope` + `EventType` (src/events) | ✅ |
| Memory Retrieval | `MEMORY_RETRIEVAL` | ✅ |
| Persona Resolver | `PERSONALITY_UPDATE` + `PERSONALITY_CONTEXT_BUILD` | ✅ |
| LLM Response | `RESPONSE_GENERATION` | ✅ |
| Memory Reflection | 不在主链路，需要在 PERSISTENCE 之后异步触发 | ⚠️ |
| Growth Event | `GROWTH_EVALUATION` | ✅ |

---

## 2. 关键步骤日志检查

| 阶段 | 是否有日志 | 日志位置 | 评估 |
|---|---|---|---|
| START | ✅ | RuntimeCore.start() | 充足 |
| LOAD_STATE | ✅ | RuntimeCore.start() | 充足 |
| RECEIVE_EVENT | ✅ | `publish_event(MessageReceivedEvent)` | 充足 |
| MEMORY_RETRIEVAL | ✅ | Adapter logs | 充足 |
| EMOTION_UPDATE | ✅ | EmotionManager.process_event | 充足 |
| GROWTH_EVALUATION | ✅ | GrowthPipeline.incremental_update | 充足 |
| PERSONALITY_UPDATE | ✅ | PersonalityResolver.resolve | 充足 |
| PERSONALITY_CONTEXT_BUILD | ✅ | RuntimeContext.assemble_context | 充足 |
| PERCEPTION_* | ⚠️ 默认 no-op | 无日志（设计如此） | 可接受 |
| SELF_MODEL_* | ⚠️ 默认 no-op | 无日志（设计如此） | 可接受 |
| RESPONSE_GENERATION | ✅ | ResponseEngine.generate | 充足 |
| GUARD_CHAIN | ✅ | GuardChain.run | 充足 |
| RESPONSE | ✅ | `publish_event(MessageRespondedEvent)` | 充足 |
| PERSISTENCE | ✅ | runtime_state.json | 充足 |
| SHUTDOWN | ✅ | RuntimeCore.stop | 充足 |

**评估：** 所有关键步骤均有日志，异常不会完全静默。no-op 阶段无日志属设计合理（默认未启用时不需要噪声）。

---

## 3. 异常隔离检查

### 3.1 Runtime 内部异常隔离

**代码位置：** `src/runtime/runtime.py`

| 异常源 | 是否被隔离 | 隔离机制 |
|---|---|---|
| Memory Adapter 失败 | ✅ | Adapter 异常被 try/except 捕获，记录到 `stage_errors` |
| Emotion Adapter 失败 | ✅ | 同上 |
| Growth Adapter 失败 | ✅ | 同上 |
| Personality Adapter 失败 | ✅ | 同上 |
| SelfModel 阶段失败 | ✅ | 默认 no-op，未注入时不执行 |
| Response Generation 失败 | ✅ | Runtime 返回兜底文本 |
| Stage Hook 失败 | ✅ | `_invoke_hook` 捕获异常到 `stage_errors` |
| Guard Chain 失败 | ✅ | Runtime 记录异常并降级 |

**评估：** Runtime 内异常隔离完整，模块失败不会导致整个服务崩溃。

### 3.2 Orchestrator → Runtime 桥接异常隔离

**代码位置：** `src/orchestrator.py` 第 813-829 行

```python
try:
    if self._runtime_bridge is not None:
        runtime_reply = self._runtime_bridge.handle_message(user_message)
        if runtime_reply is not None and isinstance(runtime_reply, str) and runtime_reply.strip():
            return runtime_reply
        # runtime 路径返回空 → 记录原因,走 legacy
        self._last_runtime_error = ...
except Exception as exc:  # noqa: BLE001
    self._last_runtime_error = repr(exc)
    print(f"[Orchestrator] Runtime 路径失败: {exc}")

# 2) Legacy fallback
return self.legacy_generate(...)
```

**评估：** Runtime 路径失败时自动降级到 legacy_generate 路径，**双层兜底**完整。

### 3.3 LLM 调用失败隔离

**代码位置：** `src/engine.py` 第 53-61 行

```python
try:
    response = self.client.chat.completions.create(...)
    return response.choices[0].message.content
except Exception as e:
    print(f"[DeepSeek] 调用失败: {e}")
    return f"抱歉，我遇到了一点问题：{str(e)}"
```

**评估：** LLM 失败时返回兜底文本，不会让服务崩溃。

### 3.4 Orchestrator 子模块异常隔离

| 子模块 | 异常隔离 | 评估 |
|---|---|---|
| Memory Extraction | ✅ 异常被 try/except 隔离 | 充足 |
| Vector Sync | ✅ 单独 try/except 隔离 | 充足 |
| Emotion Pre/Post | ✅ 单独 try/except 隔离 | 充足 |
| Relationship Post | ✅ 单独 try/except 隔离 | 充足 |
| Audit Log | ✅ 单独 try/except 隔离 | 充足 |
| Growth Proposal | ✅ 单独 try/except 隔离 | 充足 |
| Experience Recovery | ✅ 单独 try/except 隔离（Phase A.1） | 充足 |
| Persistence Hook | ✅ 单独 try/except 隔离（Phase 6.2） | 充足 |

**整体评估：** 异常隔离**完整**，模块失败不会导致服务崩溃。

---

## 4. Runtime Health Check 检查

### 4.1 RuntimeCore 内置 Health Check

**代码位置：** `src/runtime/runtime.py` 第 1062 行

```python
self._last_health = self._health_check_all_safe()
```

**已存在的方法：**
- `_health_check_all_safe()` - 完整 health check
- `last_health` (property) - 公开访问接口
- `is_started` (property) - 启动状态

**评估：** RuntimeCore 已有 health check，**不需要新增**。

### 4.2 Orchestrator Runtime 状态查询

**代码位置：** `src/orchestrator.py` 第 924-944 行

```python
def is_runtime_enabled(self) -> bool: ...
def get_runtime_status(self) -> str: ...
def get_last_reply_source(self) -> Optional[str]: ...
def get_runtime_call_count(self) -> int: ...
def get_legacy_call_count(self) -> int: ...
def get_last_runtime_error(self) -> Optional[str]: ...
def runtime_stats(self) -> Dict[str, Any]: ...
```

**评估：** Orchestrator 已有完整的 runtime stats 暴露接口。

### 4.3 API 层 Runtime 状态端点

**位置：** `src/runtime/pipeline_server.py`（Phase 6.6 之前已添加 `GET /runtime/status`）

```python
{
    "runtime": "ready",
    "pipeline": true,
    "persistence": true,
    "token_optimizer": true
}
```

**评估：** API 层 health check 已存在，**不需要新增**。

---

## 5. 问题识别

### 🟢 低风险（Low Risk）

#### L1. Memory Reflection 不在 Runtime 主链路

**位置：** `src/runtime/runtime.py` 第 110-131 行
**问题描述：** Runtime 生命周期中没有显式的 `MEMORY_REFLECTION` 阶段。当前实现是：
- `MemoryExtractor` + `MemoryVerifier` 在 `Orchestrator._handle_message` 内调用
- `MemoryConsolidationEngine` 在后台独立运行

**风险：** 如果 MemoryExtractor 在 Orchestrator 中失败，反射不会发生。但因为有 try/except 隔离，不会影响主流程。

**修复建议：** 不在 Phase A.3 范围。

#### L2. GROWTH_EVALUATION 失败时不会重试

**位置：** RuntimeCore 处理 GROWTH_EVALUATION
**问题描述：** 如果 GrowthAdapter 失败，仅记录到 stage_errors，不会触发重试。

**风险：** 重要成长事件可能被遗漏。

**修复建议：** 不在 Phase A.3 范围。

#### L3. PERCEPTION/SELF_MODEL 默认 no-op 缺少"已跳过"日志

**位置：** RuntimeCore 处理 PERCEPTION_*, SELF_MODEL_*
**问题描述：** 默认 no-op 阶段无日志，无法从日志中确认这些阶段是否被有意跳过。

**风险：** 调试时容易误判为"该阶段未执行"。

**修复建议：** 不在 Phase A.3 范围（属于可读性优化，不影响功能）。

---

### 🟡 中风险（Medium Risk）

#### M1. Runtime 主链路日志缺少 correlation_id

**位置：** 整个 Runtime + Orchestrator
**问题描述：** 当前日志缺少 `correlation_id`（虽然 orchestrator 内部生成了 `conversation_id`），跨模块追踪一条消息的完整生命周期较难。

**风险：** 调试复杂问题时（如某条消息卡在哪个阶段）效率较低。

**修复建议：** 不在 Phase A.3 范围（属于运维优化）。

---

## 6. 与用户期望的对照

| 用户期望 | 现状 | 是否满足 |
|---|---|---|
| 所有关键步骤都有日志 | ✅ | 满足 |
| 异常可追踪 | ✅ `last_health` + `last_runtime_error` | 满足 |
| 模块失败不会导致服务崩溃 | ✅ 所有模块有 try/except 隔离 | 满足 |
| Runtime health check 存在 | ✅ RuntimeCore + Orchestrator + API 三层 | 满足 |

---

## 7. 修复建议优先级

| 优先级 | 修复项 | 预计影响 | 是否在 A.3 范围 |
|---|---|---|---|
| P0 | 创建 `tests/test_runtime_health.py` 端到端健康检查测试 | 高 | ✅ 是 |
| P1 | 增加 correlation_id 跨模块追踪 | 中 | ❌ 否（运维优化） |
| P1 | PERCEPTION/SELF_MODEL 阶段加 "skipped (no-op)" 日志 | 低 | ❌ 否（可读性优化） |
| P2 | GROWTH_EVALUATION 失败重试 | 中 | ❌ 否 |

**Phase A.3 范围：** 仅创建 `tests/test_runtime_health.py` 端到端健康检查测试，**不修改任何业务代码**。

---

## 8. Runtime 生命周期流程图

```
[用户消息] (QQ/AstrBot)
  ↓
[api_server.py / pipeline_server.py]
  ↓
Orchestrator.process()
  ├→ Step 0: publish_event(MessageReceivedEvent) + record_audit_log
  ├→ Step 1: UserResolver.resolve (用户身份)
  ├→ Step 2: MemoryStore.load() + VectorMemory.search() (记忆召回)
  ├→ Step 3: RuntimeContext.assemble_context (上下文组装)
  ├→ Step 4: PersonalityResolver.resolve() (人格解析)
  ├→ Step 5: 情绪上下文 (从 assembled_context)
  ├→ Step 6: 关系上下文
  ├→ Step 7: 情绪事件检测 (EmotionEventDetector)
  ├→ Step 8: Engine.generate (LLM 回复)
  ├→ Step 9-14: history / memory / emotion / relationship / event / persistence
  └→ Return: reply (字符串)
  ↓
[AstrBot/QQ] 回复给用户
```

**所有关键步骤的失败都被 try/except 隔离，不会导致服务崩溃。**

---

## 9. 结论

| 维度 | 评估 |
|---|---|
| Runtime 生命周期是否完整 | ✅ 是（20 阶段） |
| 所有关键步骤是否有日志 | ✅ 是 |
| 异常是否可以追踪 | ✅ 是（last_health / last_runtime_error） |
| 模块失败是否导致服务崩溃 | ✅ 否（双层兜底） |
| Runtime Health Check 是否存在 | ✅ 是（三层） |
| 是否需要 Phase A.3 修改代码 | ❌ 否（仅创建健康检查测试） |

**Phase A.3 仅做的事：**
1. ✅ 本审计报告
2. ✅ `tests/test_runtime_health.py` 端到端健康检查测试
3. ❌ 不修改任何 Runtime 业务代码

---

## 附录 A：Runtime Health Check 层级

| 层级 | 接口 | 状态 |
|---|---|---|
| RuntimeCore | `last_health` / `_health_check_all_safe()` | ✅ 已存在 |
| Orchestrator | `is_runtime_enabled()` / `runtime_stats()` | ✅ 已存在 |
| API Layer | `GET /runtime/status` | ✅ 已存在 |
| Database Layer | runtime_state.json 持久化 | ✅ 已存在 |

**审计日期：** 2026-08-02
**审计者：** Phase A.3.4 自动审计
