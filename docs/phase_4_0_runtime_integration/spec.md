# Phase 4.0 Runtime Integration — 规格书

> 目标：让羽依第一次真正通过完整生命循环产生一次回复。
> 版本：v0.2（SPEC Review 结果：通过，已修改 6 个关键点后冻结；进入实现前需用户确认冻结签名。）
> 日期：2026-08-08
> 状态：SPEC FROZEN CANDIDATE（等待 Reviewer 确认 → 进入 4.0.1 实现）
> 审查结论：P0 级修改 3 条 + P1 级修改 3 条，全部已在规格中落实。

---

## 0. SPEC Review 6 点修改速览

| # | 级别 | 审查意见 | 规格落地点 |
|---|---|---|---|
| 1 | P0 | runtime.py 兼容壳不要依赖 RuntimeBridge（防循环依赖） | §2.1 / §3.1-3：Adapter 模式 self._impl，严禁 import Bridge |
| 2 | P0 | process() 不要复制 17 阶段 → 抽 LifecycleExecutor 独立文件 | §2.1 / §3.1-1：新增 src/runtime/lifecycle.py，RuntimeCore 组合持有 |
| 3 | P0 | 用户输入不提前 inject_event；唯一入口 process；阶段 1 内部 _on_event 一次 | §2.2 / §3.2 生产链：RuntimePipeline / RuntimeBridge 删前置 inject，仅 Receive 阶段内部走 _on_event |
| 4 | P1 | Memory 写锁放 src/common/storage/（不是 runtime/utils，跨领域复用） | §3.4-1：新增 src/common/storage/atomic_write.py（file_atomic_write_lock） |
| 5 | P1 | VectorMemory 保护升级为 Adapter（不只是 orchestrator 里一个 _safe 函数） | §3.4-2：新增 src/infrastructure/vector/safe_vector_adapter.py（SafeVectorMemoryAdapter 包装所有调用方） |
| 6 | P1 | identity_context 独立 Phase（从 Runtime 接通解耦，故障定位分层） | §3.3 Response Context Pipeline 独立 4.0.3；4.0.2 只负责 Runtime 生产链接通 |

---

## 1. 背景与问题陈述

### 1.1 当前症状

项目到达"架构接管"临界点：
- 概念层完整（Memory/Growth/Emotion/Identity/Runtime/Snapshot 各模块都已实现）
- UI/Audit/Memory/Personality 各表面数据可见
- 大量单测通过
- 但**实际执行链路**仍停留在 Orchestrator.legacy_generate → engine.generate()

Runtime 只"收事件"，不"产回复"，相当于"大脑各分区激活但丘脑没有接上皮层"。

### 1.2 根因（已由排查报告确证）

P0-① 级别的架构分裂：项目存在两份同名 `RuntimeCore` 类，它们是"两个不同接口共用一个名字"，方法集交集几乎为空：

| 维度 | `src/runtime/runtime_core.py`（ModuleBase 版，当前生产运行） | `src/runtime/runtime.py`（Phase 3.7.3 装配版，实际是死代码） |
|---|---|---|
| 基类 | `ModuleBase` | 无 |
| __init__ 签名 | `(config: Optional[Dict])` | 21 个 ports/adapters 参数 |
| 事件入口 | `inject_event(event_type, event_data)` | **无** |
| 回复入口 | **无** | `process(event, ctx) -> RuntimeContext`（17 阶段） |
| 生命周期 | `start() -> bool` / `stop() -> bool` / `is_running` | `start() -> RuntimeContext` / `shutdown()` / `is_started` |
| 后台线程 | tick scheduler（60s 心跳，状态衰减 + 决策） | **无** |
| Authority getters | 8 个 `get_*_store` + `get_snapshot` + `get_runtime_health_report` | **全缺** |
| 状态持有 | `SelfState` / `WorldState` / `ExperienceBuilder` | 不持有（通过 Adapters） |

生产初始化顺序（[api_server.py:198-213](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/api_server.py#L198-L213)）：
```
_init_runtime_bridge(config)    # RuntimeBridge 持 runtime_core.py 版
Orchestrator(config=config)     # 通过 RuntimeBridge 共享拿到类型B
                                # → OrchestratorRuntimeBridge 探测：has_process=False
                                # → 走类型B 分支（只inject_event，回复走legacy）
```

结果：runtime.py 的 17 阶段在生产中从未执行。

### 1.3 设计约束（不可违反）

来自 `project_memory.md` 与 `user_profile.md`：

1. 不修改 `src/memory/**`、`src/growth/**`、`src/personality/**`、`src/self_model/**`、`src/control/**`、`src/runtime/policy/**`（硬约束）
2. 所有代码改动保持向后兼容，不破坏现有 API
3. **不要删除任何一个** RuntimeCore；合并采用"迁入 + 兼容壳"策略
4. 默认行为不变：`config.runtime.enabled=false` 时仍走旧链路；新链路需显式启用或渐进切换
5. 单例模式：全局只允许一个 RuntimeCore 实例（RuntimeBridge 已保证）
6. 新增功能必须附带单元测试
7. 不要修改 `config.yaml` 中的 API Key
8. 代码风格保持 PEP 8
9. 修改核心模块前先评估影响范围（本规格即评估文档）

---

## 2. 总体设计

### 2.1 合并策略：迁入而非替换（SPEC REVIEW 修正版）

> **不删除任何一个文件；runtime.py 兼容壳不依赖 RuntimeBridge（避免循环依赖风险，P0-修改1）。**

```
Phase 4.0.1 后项目结构：

src/runtime/
  lifecycle.py            ★★ 新增 P0（原 stages.py 功能升级）
        RuntimeStage 枚举
        RUNTIME_LIFECYCLE_ORDER 常量
        LifecycleExecutor.execute(core, event, ctx)  ← 17 阶段调度唯一实现
        |
        | 被 RuntimeCore 持有，作为私有组合成员
        ↓
  runtime_core.py           ★ 唯一 RuntimeCore（身体 + 思考接口合一）
        - 继承 ModuleBase（身体）
        - 持有的组合成员：
            self._lifecycle: LifecycleExecutor
        - 对外接口：
            inject_event(event_type, event_data)     # 后台/tick 入口（保留不删）
            tick()                                    # 心跳（保留不删）
            process(event, ctx=None) -> RuntimeContext # 一次思考
                |
                +→ 内部调用 self._lifecycle.execute(self, event, ctx)
                   阶段 1（Receive Event）内部调 self._on_event()
                   → 一次用户消息只发生一次状态更新（P0-修改3）
        |
        | RuntimeBridge 始终指向 runtime_core.py（不变）
        ↓
  runtime.py                → 兼容壳 Adapter 模式（零 Bridge 依赖）
        class RuntimeCore:
            def __init__(self, *args, **kwargs):
                from src.runtime.runtime_core import RuntimeCore as _Impl
                self._impl = _Impl(*args, **kwargs)   # 直接 new Impl（不走 Bridge）
            def process(e, c=None): return self._impl.process(e, c)
            def inject_event(t, d): return self._impl.inject_event(t, d)
            def start(): return self._impl.start()
            def shutdown(): return self._impl.stop()
            # 所有原 21 个 port 参数照常接收，转发 _impl.configure_ports()
        - 仍然允许 `from src.runtime.runtime import RuntimeCore`（完全向后兼容）
        - **严禁** import 或调用 RuntimeBridge（P0-修改1，防循环依赖）

  runtime_bridge.py         → 不变（仍指向 runtime_core.py RuntimeCore）
  orchestrator_runtime_bridge.py → 简化：移除鸭子类型探测，只走 process() 路径
```

**为什么迁入 runtime_core.py 而不是反过来：**
- RuntimeBridge 已经绑死 runtime_core.py；改 RuntimeBridge 会波及 8 个 Authority getter
- `api_server.py` 的初始化顺序已经是 RuntimeBridge 先创建
- 现有的 tick 线程、inject_event、SelfState/WorldState/ExperienceBuilder 不能丢
- 需要的是"给生命系统管理器加上一次完整思考过程的能力"，不是反过来把思考过程改成事件循环

**为什么抽出 LifecycleExecutor（P0-修改2）：**
- 17 阶段调度是"纯调度"，不属于 RuntimeCore 生命管理器本体
- 未来 Phase 5/6/7 扩展阶段时，新增阶段只需改 LifecycleExecutor，不污染 RuntimeCore
- RuntimeCore 继续做"身体"：事件总线/tick/state/Authority getter；
- LifecycleExecutor 做"神经系统调度器"：阶段顺序与分派逻辑
- 两个版本的 RuntimeCore 复用同一套 execute()，不再有"双生命周期"未来风险

**为什么 process() 内部第 1 阶段才走 _on_event（P0-修改3）：**
- 旧模式：inject_event → process(event) = 同一条消息修改两次状态（emotion ×2，world_state ×2）
- 新模式：**用户聊天走唯一入口 RuntimeCore.process(event)**
  - 第 0 阶段 Control Check
  - 第 1 阶段 Receive Event → 统一**内部**调用 self._on_event(event) → 状态更新一次
  - 后续阶段读取状态，不再重复 mutate
- inject_event() 保留但**仅用于后台事件**：
  - tick 产生的 autonomous 事件
  - 外部模块（Growth/Personality）的异步回调
  - 非用户触发的系统事件
- 语义清晰：`inject_event = 只改状态不产回复`，`process = 改状态 + 产回复`，两条入口各司其职

### 2.2 全局调用链目标态（SPEC REVIEW 修正：用户消息唯一入口 process，不提前 inject_event，P0-修改3）

```
===== 用户聊天路径（只走 process，一次状态更新）=====

用户 HTTP 请求
  ↓
api_server /v1/chat/completions
  ↓
RuntimePipeline.run({"user_message": msg})
  ↓
RuntimeBridge.bridge_process(user_message, …)   ★ 新增：封装 Event 构造 → 调 process
  ↓
RuntimeCore.process(event: Event, ctx=None)     ★ 唯一入口（不再前置 inject_event）
  |
  +→ self._lifecycle.execute(self, event, ctx)   ★ LifecycleExecutor（P0-修改2）
        ↓
        阶段 0 CONTROL_CHECK
        阶段 1 RECEIVE_EVENT                      ★ 内部才调用 self._on_event(event)
                                                  → 仅发生一次状态 mutate（P0-修改3）
        阶段 2 MEMORY_RETRIEVAL     ← self.memory_adapter.retrieve()
        阶段 3 EMOTION_UPDATE       ← self.emotion_manager
        阶段 4 GROWTH_EVALUATION    ← self.growth_adapter
        阶段 5 PERSONALITY_UPDATE   ← self.personality_resolver
        阶段 6 PERSONALITY_CONTEXT_BUILD
        阶段 7-13 （PERCEPTION / SELF_MODEL 系列）默认 no-op
        阶段 14 RESPONSE_GENERATION ← ResponseAdapter / engine.generate
                                       含 identity_context 注入（P1-修改6 独立阶段）
        阶段 15 GUARD_CHAIN
        阶段 16 RESPONSE 阶段 hook
        ↓
ctx._final_reply (str) 或 None（让上层 fallback）
  ↓
RuntimePipeline：fallback legacy（若 _final_reply 为空）+ 持久化 + 事件 sink
  ↓
HTTP 响应


===== 后台路径（只走 inject_event，不产回复）=====

tick scheduler / Growth proposal callback / System internal hook
  ↓
RuntimeBridge.inject_event(event_type, event_data)   保留（仅做状态 mutate）
  ↓
RuntimeCore.inject_event(event_type, event_data)
  ↓
self._on_event(...) → 状态更新（一次）
```

**关键点**：
- Phase 4.0 默认只激活 0-6 以及 14-16（与现有 legacy 功能对齐）；Phase 7-13 默认 no-op，**不改变默认响应质量**
- Runtime 生成失败时仍可 fallback 到 legacy（**降级保底**，不可破坏）
- 「用户聊天 process 链」与「后台 inject_event 链」两条路径**彻底分离**，语义清晰，杜绝一次输入两次 mutate（emotion ×2 / world_state ×2 / experience ×2）风险

### 2.3 向后兼容策略（必须满足）

| 场景 | 4.0 前行为 | 4.0 后行为 |
|---|---|---|
| `from src.runtime.runtime_core import RuntimeCore` | 拿到 ModuleBase 版（无 process） | 拿到同一类（新增 process 方法） |
| `from src.runtime.runtime import RuntimeCore` | 拿到 Phase 3.7.3 装配版（有 process） | 拿到兼容壳；实际仍可 work（或软迁移提示） |
| `RuntimeBridge.initialize()` → `get_memory_store()` 等 | 返回 runtime_core.py 实例 | **不变** |
| `rt.inject_event(...)` | 可用 | **不变**（仍走 _on_event 状态更新 + tick 决策） |
| `config.runtime.enabled=false` | 走 orchestrator.process | **不变**（仍走旧链路） |
| 单元测试中 `from src.runtime.runtime import RuntimeCore; rt.process(event)` | 测试通过（17 阶段） | **测试仍通过**（兼容壳层委托） |
| 现有 Health/Dashboard 接口 | 读写正常 | **不变** |

> **核心原则：旧的公共 API 一个都不撤；新的 process() 能力只在 RuntimeCore 类上做新增方法与内部数据结构补充。**

---

## 3. 各子阶段规格要点

### 3.1 4.0.1 统一 RuntimeCore（SPEC REVIEW 修正：抽 LifecycleExecutor + runtime.py 零 Bridge）

**目标**：
- `RuntimeCore`（runtime_core.py）获得 `process(event, ctx=None) -> RuntimeContext` 思考接口；
- 17 阶段调度逻辑**下沉到独立 `LifecycleExecutor`**（避免未来"双生命周期"，P0-修改2）；
- `runtime.py RuntimeCore` 改为 Adapter 兼容壳，`self._impl = RuntimeCoreImpl()`，**严禁依赖 RuntimeBridge**（P0-修改1，防循环依赖）。

**具体变更点：**

1. **新增 `src/runtime/lifecycle.py`（P0-修改2，替代原 stages.py）**：
   - 定义 `RuntimeStage(IntEnum)`（17 项，与原 runtime.py 完全一致的字符串/数字映射）；
   - 定义 `RUNTIME_LIFECYCLE_ORDER: List[RuntimeStage]`（阶段顺序单源真相）；
   - 定义 `LifecycleExecutor` 类：
     - `execute(core, event, ctx) -> None`（17 阶段调度唯一实现）；
     - `invoke_stage(core, stage, event, ctx)`（阶段分派到 core 上对应 `_stage_XX_*` 方法）；
     - 单个阶段 fail → 记录 logger.warning + 写 `core._last_process_phase_errors[stage]` → continue（下一阶段），不中断整条链；
2. **`runtime_core.py RuntimeCore`**：
   - 顶部 import：`RuntimeContext / Event / LifecycleExecutor / RuntimeStage / RUNTIME_LIFECYCLE_ORDER`；
   - `__init__` 末尾追加：
     - `self._lifecycle = LifecycleExecutor()`（组合，非继承）；
     - `self._phase_runtime_ctx / _last_process_event_type / _last_process_stage / _last_process_phase_errors / _started_process_mode / _process_lock`；
   - 新增 `process(event, ctx=None) -> RuntimeContext`：
     - ① 取 process_lock → ② 未启动则 start → ③ ctx or RuntimeContext() → ④ `self._lifecycle.execute(self, event, ctx)` → ⑤ 记录诊断 → ⑥ return ctx；
   - 新增阶段级动作接口方法（供 LifecycleExecutor 调用）：
     - 00 control_check / **01 receive_event（内部 `self._on_event(event_type, payload)` 一次状态 mutate）** / 02 memory_retrieval / 03 emotion_update / 04 growth_evaluation / 05 personality_update / 06 personality_context_build / 14 response_generation / 15 guard_chain / 16 response；
     - 7-13（PERCEPTION_* / SELF_MODEL_*）默认空实现 no-op（不抛错）；
   - 新增诊断 getter：`get_last_process_stage / get_last_process_errors / get_last_process_ctx`（不影响现有 Authority getter）。
3. **`runtime.py RuntimeCore` 改为 Adapter 兼容壳（P0-修改1：零 Bridge 依赖）**：
   - 顶部 import：**严禁 import RuntimeBridge / get_runtime_bridge**；直接 `from src.runtime.runtime_core import RuntimeCore as _Impl`；
   - `class RuntimeCore`：构造签名与原 21 port 参数完全一致；
   - `__init__` 只做：`self._impl = _Impl(config={})` + `self._impl.configure_ports(memory_port=memory_port, emotion_port=emotion_port, ..., growth_state=growth_state)`（即原 21 参数完整转发给 _impl）；
   - 公开方法/属性：process / inject_event / start / shutdown / get_*_store 等全部转发 self._impl.*；`is_started` 映射 `self._impl.is_running`；
   - RuntimeStage / RUNTIME_LIFECYCLE_ORDER 顶部 from lifecycle import re-export；
   - **保留**旧 runtime.py 的自建 17 阶段代码在私有的 `_legacy_*` 方法区（不调用但不删，作为对比参考或调试开关）。
4. **`src/runtime/__init__.py`**：包级导出 `RuntimeCore` 仍指向 runtime_core.py（不变）；`__all__` 追加 `RuntimeStage / RUNTIME_LIFECYCLE_ORDER`（若之前没导）。

---

### 3.2 4.0.2 接通生产链（Runtime-first；唯一入口 process，不提前 inject_event，P0-修改3）

**目标**：
- 用户聊天：HTTP → RuntimePipeline → RuntimeBridge.bridge_process → RuntimeCore.process（唯一入口，阶段 1 才走 _on_event）；
- 去重：**不**再提前调任何 `inject_event("user.input")`（api_server 删、RuntimePipeline 删、Bridge.bridge_process 内部也不调），避免重复 mutate 状态；
- OrchestratorRuntimeBridge 简化为 process-first；RuntimePipeline runtime-first 改造；inject_event 仅用于后台事件/tick/系统回调。

**具体变更点：**

1. **OrchestratorRuntimeBridge 简化：**
   - `_try_runtime(user_message)`：**不再调 `rt.inject_event(...)`**；只构造 Event 然后 `ctx = rt.process(event)`；
   - handle_message 的 last_reply_source / last_runtime_mode 三态逻辑保留（runtime / runtime_state+legacy / legacy）。
2. **RuntimePipeline.run()：**
   - **删除**原本"调用 orchestrator 之前 inject_event 一次"的代码（P0-修改3：用户聊天唯一入口 process，不前置 inject）；
   - Step 5 runtime-first 改造：先 `_try_runtime_process(...)` 有回复就用；否则 fallback orchestrator.process；都空再兜底文本；
3. **api_server.py 去重：**
   - 删除 `_process_via_pipeline` 前后的 `_runtime_bridge.on_user_message(...)` 调用；后台 tick 路径的 inject_event 保留不删；
4. **RuntimeBridge 新增 bridge_process：**
   - 签名：`bridge_process(user_message, user_id, session_id=None) -> Optional[str]`；
   - **内部绝不调 inject_event（P0-修改3）**；直接 new Event 调 `rt.process(event)`，取 `ctx._final_reply` 返回非空字符串或 None。
5. **RuntimeCore 侧补充保证：**
   - `_stage_01_receive_event` 中对 `user_input` 类型事件统一走 `self._on_event("user.input", payload)` 一次；对其他类型 event 映射到对应 event_type；确保 process 路径下 world_state / emotion_manager / experience_builder 也写入状态，但仅此一次。

---

### 3.3 4.0.3 Response Context Pipeline（独立 Phase，P1-修改6：Identity 解耦）

**目标**：**完全不涉及 Runtime 接通**的独立修复——消除 `engine.generate(identity_context=...)` TypeError；让 Identity Context 真的注入到 system prompt；修复 identity_context 在 engine → response 链路上的断裂。此阶段单独验证，避免 Runtime 接通失败时无法判断故障层。

**具体变更点：**

1. **engine.py**：
   - `generate()` 显式加 `identity_context: Optional[str] = None`（experience_context 之后，最后显式参数）；
   - `_build_messages_original / _build_messages_opt` 签名同步加 `identity_context=None`；消费逻辑在 system prompt 末尾追加 `=== 身份约束 ===\n{identity_context}\n=== /身份约束 ===` 块；空 None/空白完全跳过，不增 token，向后兼容；
   - `_mock_response` 若显式签名也要同步扩参（默认 None）。
2. **Orchestrator 侧**：
   - `legacy_generate` / `generate_initiative` 已写 `engine.generate(identity_context=...)` 两行，**不删除**，engine 升级后自然不再 TypeError。
3. **Bridge._legacy_generate（orchestrator_runtime_bridge.py）**：
   - engine.generate 调用点补传三字段：`experience_context / identity_context / context_prompt_blocks`（从 orch 取，try/except 兜底 None）；
4. **ResponseAdapterImpl（若存在 engine.generate 调用）**：
   - 从 `request.identity_context` 取到并传入；没有就 None（不崩溃）。

---

### 3.4 4.0.4 Memory 事务化（SPEC REVIEW 修正：锁层位置 → common/storage + SafeVectorMemoryAdapter，P1-修改4/5）

**目标**：不改 `src/memory/**` 内部实现，在**调用方** / **Adapter 层**修复：
1. MemoryStore 文件并发读写冲突 → 原子写（P1-修改4：放 common/storage，未来所有持久化都能复用）
2. MemoryStore 拒绝后 VectorMemory 仍写入 → 数据层不一致 → SafeVectorMemoryAdapter（P1-修改5：不是 orchestrator 局部函数，而是 Adapter 全路径保护）
3. MemoryCreatedEvent 发布时机修正：只在 MemoryStore.add 成功后发布（事件代表事实）

**具体变更点：**

1. **新增 `src/common/storage/atomic_write.py`（P1-修改4，替代原 runtime/utils 位置）**：
   - 通用 `@contextmanager file_atomic_write_lock(obj, *, timeout_s=30)`；通过 `id(obj)` 建立 `WeakValueDictionary[int, RLock]`（未来 GC 友好）；超时保护 + __exit__ 内吞重复 release 异常；
   - 同步创建 `src/common/__init__.py` / `src/common/storage/__init__.py`（若不存在），`__all__` 导出 `file_atomic_write_lock`；
   - **Memory / Snapshot / Audit / Config 未来都可复用**，架构上不属 Runtime 领域。
2. **新增 `src/infrastructure/vector/safe_vector_adapter.py`（P1-修改5：全路径保护，替代 orchestrator 内 _safe_add_vector_memory）**：
   - `class SafeVectorMemoryAdapter`：构造接收 `inner_vector: VectorMemory`；
   - `add_memory(record)` → 前置校验（role ∈ 白名单 / content len ≥ 5 / 不含污染标签）→ 不通过则 warning + return False；通过才 `inner_vector.add_memory(record)`；
   - search / delete / clear 等其他方法直接转发 inner_vector；
   - **所有调用方统一 wrap 初始化**（Orchestrator / MemoryAdapter / 其他），不再直接访问底层 VectorMemory。
3. **Orchestrator Step 10 重写：**
   - imports：`from src.common.storage.atomic_write import file_atomic_write_lock`（不是 runtime/utils）；
   - 初始化：`self._raw_vector_memory = VectorMemory(...)` → `self.vector_memory = SafeVectorMemoryAdapter(self._raw_vector_memory)`；
   - Step 10：`with file_atomic_write_lock(self.memory_store)` 包裹：规整 → memory_store.add → success 时**才** SafeVectorMemoryAdapter.add_memory + MemoryCreatedEvent 发布 + 审计 success；rejection 时 warning + 审计 rejected，**不调 vector，不发 MemoryCreatedEvent**。
4. **Runtime MemoryAdapter.store_experience：**
   - 同样 `with file_atomic_write_lock(self._store)`；store 内 VectorMemory 若有引用也统一 SafeVectorMemoryAdapter wrap。

---

### 3.5 4.0.5 Runtime 诊断面板

**目标**：`/api/v1/runtime/overview` 返回能回答"羽依到底有没有活"的最小字段集。

**具体变更点：**

1. **RuntimeProvider（src/admin/runtime_provider.py）**：
   - 新增 `set_orchestrator_ref(orch)` 方法（api_server.py 初始化 orchestrator 后调用）
   - 新增 `get_orchestrator_runtime_stats() -> Dict` 方法，内部：
     - 若 orch 存在：`orch.runtime_stats()` 聚合（现有方法已存在于 orchestrator.py:1109-1120，只是没人调用）
     - 若 orch 不存在：返回 `{}` 空
2. **Gateway api_runtime_overview（src/control/api/routes.py:329-365）**：
   - 在现有 `data["runtime"]` 对象中**追加**以下键（不删除现有键）：
     - `orchestrator_runtime_status` → `orch.get_runtime_status()`
     - `last_reply_source` → `orch.get_last_reply_source()`
     - `last_runtime_mode` → `orch.get_last_runtime_mode()`
     - `last_runtime_error` → `orch.get_last_runtime_error()`
     - `last_legacy_reason` → `orch.get_last_legacy_reason()`
     - `runtime_call_count` → `orch.get_runtime_call_count()`
     - `runtime_state_count` → `orch.get_runtime_state_count()`
     - `legacy_call_count` → `orch.get_legacy_call_count()`
     - `runtime_mode`（新增）：枚举 `"full"`（走 process 且有回复）/ `"legacy"`（兜底）/ `"state_only"`（仅状态更新）/ `"offline"`（Runtime 不可用）
     - `last_event`（新增）：从 RuntimeBridge 的 world_state.recent_events 取最近一条 event_type（若存在）
     - `last_phase`（新增）：从 RuntimeCore 的 `_last_process_stage` 属性取，若从未 process 则为 `null`
     - `memory_write_success`（新增，最后一次写记忆是否成功）
     - `growth_triggered`（新增，最近 5 分钟内是否有 Growth 提案）
3. **/api/v1/runtime/status** 也追加 `last_runtime_error` / `orchestrator_runtime_status` 两个最小字段（不破坏现有结构）
4. **Dashboard（src/admin/dashboard/runtime_router.py）**：
   - `RuntimeDashboardProvider` 也接入 orchestrator_ref，`/api/dashboard/v2/runtime/status` 追加 `last_reply_source` / `last_runtime_mode` 两个诊断字段（方便管理面板看到）

---

### 3.6 4.0.6 全链路测试

**目标**：端到端验证 4.0.1–4.0.5，包含：
1. 单元测试：各阶段变更点的行为 + 边界
2. 集成测试：一次聊天是否真的经过 RuntimeCore.process() 各阶段并返回 reply
3. 回归测试：关闭 `runtime.enabled` 时是否仍走旧链路
4. 可观测性测试：`/runtime/overview` 是否真的返回最新诊断字段

---

## 4. 变更的文件清单（按子阶段分组，SPEC REVIEW 修正版）

只列将被修改的文件（不含测试文件；测试文件在 tasks.md 中单独列出）。标注 ★ = 核心变更，NEW = 新增文件。

**4.0.1 统一 RuntimeCore**
- `src/runtime/lifecycle.py` ★ NEW（P0-修改2：RuntimeStage / RUNTIME_LIFECYCLE_ORDER / LifecycleExecutor）
- `src/runtime/runtime_core.py` ★（新增 process + 阶段级动作接口 + 诊断 getter + 组合 _lifecycle）
- `src/runtime/runtime.py` ★（P0-修改1：改为 Adapter 兼容壳，self._impl = _Impl；严禁 import RuntimeBridge）
- `src/runtime/__init__.py`（追加 RuntimeStage/RUNTIME_LIFECYCLE_ORDER 导出）

**4.0.2 接通生产链**
- `src/orchestrator_runtime_bridge.py` ★（_try_runtime 简化，不再调 inject_event；handle_message 三态标记保留）
- `src/runtime/runtime_bridge.py` → 新增 `bridge_process()` 方法，内部绝不能调 inject_event
- `src/runtime/runtime_pipeline.py` ★（Step 5 改为 runtime-first；删前置 inject_event 调用）
- `api_server.py` → 删除两处 `_runtime_bridge.on_user_message(...)` 调用（chat 路径），后台 tick 保留

**4.0.3 Response Context Pipeline**
- `src/engine.py` ★（generate() 扩 identity_context 显式参数；两处 _build_messages_* 消费注入）
- `src/orchestrator.py`（若需检查现有 identity_context 传参一致性则微调；若无则不改）
- `src/orchestrator_runtime_bridge.py` → _legacy_generate 补传 experience_context / identity_context / context_prompt_blocks
- `src/runtime/adapters/impl/response_adapter_impl.py` → 若调用 engine.generate 也补 identity_context

**4.0.4 Memory 事务化**
- `src/common/__init__.py` NEW（P1-修改4：如果不存在）
- `src/common/storage/__init__.py` NEW
- `src/common/storage/atomic_write.py` ★ NEW（file_atomic_write_lock，替代原 runtime/utils 位置）
- `src/infrastructure/__init__.py` NEW（P1-修改5：如果不存在）
- `src/infrastructure/vector/__init__.py` NEW
- `src/infrastructure/vector/safe_vector_adapter.py` ★ NEW（SafeVectorMemoryAdapter，替代 orchestrator._safe_add_vector_memory）
- `src/orchestrator.py` ★（Step 10 重写：file_atomic_write_lock + memory_store.add 检查返回值 + 向量库和事件仅成功发布 + 诊断 _last_memory_write_success）
- `src/runtime/adapters/memory_adapter.py` → store_experience 进入相同 file_atomic_write_lock 上下文

**4.0.5 Runtime 诊断面板**
- `src/admin/runtime_provider.py` → 新增 orchestrator_ref 注入 + get_orchestrator_runtime_stats + runtime 段落 12 字段追加
- `src/control/api/routes.py` → /runtime/overview 与 /runtime/status 追加诊断字段
- `src/admin/dashboard/runtime_router.py` → /dashboard/v2/runtime/status 追加 last_reply_source + last_runtime_mode
- `api_server.py` → 初始化 orchestrator 后调用 provider.set_orchestrator_ref(orchestrator)

---

## 5. 风险评估（SPEC REVIEW 修正版）

| 风险 | 等级 | 说明 | 缓解策略 |
|---|---|---|---|
| process() 与 inject_event/tick 并发竞态 | **高** | process() 访问 SelfState/WorldState，tick 也会。**但 P0-修改3 已减少一次重复 mutate，风险实际比 v0.1 低。** | process() 用独立 `_process_lock`（RLock）；_on_event 内再用 RuntimeCore._lock 保护 state；锁顺序严格：先 _process_lock，后 _lock，再 memory_write_lock |
| runtime.py Adapter 模式下，旧 100+ 处 `from src.runtime.runtime import RuntimeCore(... , memory_port=...)` 测试失败 | **中** | P0-修改1 禁止 import Bridge，self._impl 直接 new 模式。但旧 port 参数必须 1:1 转发给 _impl.configure_ports，否则 TypeError | _impl 上新增 `configure_ports(**ports)`，缺失属性时安全忽略（hasattr 检查 + getattr 兼容）；测试提供 "Legacy RuntimeCore 构造参数覆盖测试"专项 |
| LifecycleExecutor 阶段 1 _on_event 调用缺失 | **高** | P0-修改3 的核心就是这里。若漏调或 event_type 映射错，world_state 不收用户消息 → 后续表面过但记忆链断裂 | 单元测试：new LifecycleExecutor + mock RuntimeCore，执行 execute 后断言 mock._on_event 被调用 exactly once，参数对；再用真实 RuntimeCore 跑一遍 |
| SafeVectorMemoryAdapter 被绕过，某处仍直接 new VectorMemory() | **中** | P1-修改5 目标是"全路径保护"，若有一个调用方没 wrap 就等于白做 | 代码搜索 `VectorMemory(` 和 `vector_memory.add_memory` 所有调用点；单元测试 `test_vector_init_all_use_safe_wrapper` 检查 import 路径 |
| file_atomic_write_lock 用 WeakValueDictionary key=id(obj) 被复用（GC 回收 id 后新对象拿同 lock） | **低** | 极端场景可能出现（极罕见），但理论上存在 | 改用 store 的绝对路径字符串作为 key（若 store 有 path 属性）；同时实现中注释这个边界情况 |
| 双路径都失效（Runtime.process 抛错 + legacy fallback 也挂） | **中** | 部署初期最大风险 | 部署开关：`config.runtime.enabled=false`（总关）+ `config.runtime.process_enabled=false`（关 process 调用）+ `YUYI_RUNTIME_PROCESS_DISABLED=1`（环境变量保险），三层互备 |
| Response Context 与 Runtime 接通混排导致故障分层不清（v0.1 原设计） | **中** | 本已由 P1-修改6 拆分 4.0.2 / 4.0.3 两阶段根除 | Gate 通过规则：4.0.2 完成后才允许改 engine.py 的 identity_context；4.0.3 期间不许改 Runtime/Bridge/Orchestrator 生产链代码 |

---

## 6. 版本治理策略

- 分支名：`phase-4-0-runtime-integration`（独立分支，不得直推 main）
- 发布顺序（严格递增，Gate 过后才能进下一段）：
  ```
  4.0.1 RuntimeCore 统一（LifecycleExecutor + Adapter壳）
     ↓ Gate: 空流程能跑（process(event) 返回 ctx, 诊断字段全）
  4.0.2 接通生产链（唯一入口 process，无重复 inject_event）
     ↓ Gate: 第一次 Runtime 真正产出非空回复一次
  4.0.3 Response Context Pipeline（identity_context → engine）
     ↓ Gate: legacy_generate / generate_initiative 均不再 TypeError
  4.0.4 Memory 事务（common锁 + SafeVectorAdapter + 事件事实化）
     ↓ Gate: 100 线程并发写入 0 丢失；拒绝时向量库 0 污染
  4.0.5 Dashboard 诊断面板（12 字段可观测）
     ↓ Gate: /runtime/overview 返回 runtime_mode / last_phase / last_reply_source
  4.0.6 E2E（闭环验证 + 性能基准）
     ↓ Sign-off
  ```
- 回滚开关：
  - `config.runtime.enabled`（总开关，=false 时走旧 orchestrator.process 链路，与 process() 能力无关）
  - `config.runtime.process_enabled`（新增，=false 时 RuntimePipeline 不调 process，直接 fallback orchestrator.process）
  - `YUYI_RUNTIME_PROCESS_DISABLED=1`（环境变量，最高优先级，跳过 process 代码路径）
- 灰度策略：
  - 先部署 4.0.1 + 4.0.5（有 process 能力，有诊断面板，但 config.process_enabled=false 不调用）
  - 确认 `/runtime/overview` 12 字段、日志、tick 均正常后，再 process_enabled=true 切生产链
