# Phase 4.0 Runtime Integration — 验收检查清单

> 规格冻结版 v0.2（对应 spec.md v0.2 / SPEC Review 6 点修改后版本）
> 日期：2026-08-08
> 实施顺序：严格按 4.0.1 → 4.0.6；每阶段 BLOCKER/P0 必须全部通过后才可进入下一阶段。
> 审查对应：§0 6 点修改速览表格（spec.md）

---

## 0. 全局验收条件（任何阶段未通过都 BLOCKER）

- [ ] **BLOCKER-1**：`config.runtime.enabled=false` 时，聊天链路与 Phase 4.0 之前**行为完全一致**（HTTP API、回复文本、记忆写入、事件发布都可通过现有回归测试）
- [ ] **BLOCKER-2**：`config.runtime.process_enabled=false` 时，RuntimePipeline 不调用 `RuntimeCore.process()`，仍走旧 `orchestrator.process` 链路
- [ ] **BLOCKER-3**：环境变量 `YUYI_RUNTIME_PROCESS_DISABLED=1` 时，直接跳过任何 process() 调用，等同于双开关双保险
- [ ] **BLOCKER-4**：所有现有测试 `python -m pytest tests/ -x --ignore=tests/test_phase_3_*`（排除旧 Phase 专项）**100% 通过**，不因 process() 合并增加任何破坏性改动
- [ ] **BLOCKER-5**：代码风格 `flake8 src/ --select=E,W,F --max-line-length=120` 0 错误（PEP 8）
- [ ] **BLOCKER-6**：无 config.yaml 中 API Key 的任何修改（diff 检查）
- [ ] **BLOCKER-7**：未修改 `src/memory/**`、`src/growth/**`、`src/personality/**`、`src/self_model/**`、`src/control/**`、`src/runtime/policy/**` 目录下的任何文件（diff 检查；新增的 src/common 与 src/infrastructure 不算）
- [ ] **BLOCKER-8（REVIEW 新增 P0-修改1）**：`src/runtime/runtime.py` 中不得有任何 import / reference RuntimeBridge（含字符串搜索 `runtime_bridge / get_runtime_bridge / RuntimeBridge` 均 0 命中）；存在即 BLOCKER 回滚

---

## 1. 4.0.1 统一 RuntimeCore（LifecycleExecutor + Adapter壳）

### 1.1 功能验收

- [ ] 4.0.1-F1：`from src.runtime.runtime_core import RuntimeCore` → 类仍继承 `ModuleBase`，`__init__(config=None)` 签名不变
- [ ] 4.0.1-F2：`RuntimeCore` 类新增 `process(event, ctx=None) -> RuntimeContext` 方法，返回值有 `_final_reply` 属性挂载能力
- [ ] 4.0.1-F3：`RuntimeCore` 仍保留 `inject_event(event_type, event_data) -> None` 方法，语义不变（走 `_on_event`）
- [ ] 4.0.1-F4：`RuntimeCore` 仍保留 `is_running` / `start()` / `stop()` / 8 个 Authority getter（完全不变）
- [ ] 4.0.1-F5（REVIEW P0-修改2 核心：LifecycleExecutor 独立）：
  - `src/runtime/lifecycle.py` 文件存在，导出 `RuntimeStage / RUNTIME_LIFECYCLE_ORDER / LifecycleExecutor`
  - `RuntimeCore.process()` 内部不是手写 17 阶段，而是 `self._lifecycle.execute(self, event, ctx)` 单调用（单测可通过 mock _lifecycle.execute 验证被 exactly once 调用）
  - 17 阶段调度的唯一实现点在 `LifecycleExecutor`；runtime_core.py 只提供阶段级 `_stage_XX_*` 接口方法（不写 if/elif 17 分支在 RuntimeCore）
- [ ] 4.0.1-F6（REVIEW P0-修改3 _on_event 单调用）：
  - `LifecycleExecutor` + mock RuntimeCore 执行后，`mock._on_event` 被调用 exactly once（不是 0 次，不是 2 次）
  - 调用参数：event_type 映射正确（user_input → "user.input"），payload 含 text/content
- [ ] 4.0.1-F7（REVIEW P0-修改1 零 Bridge 依赖 runtime.py）：
  - `from src.runtime.runtime import RuntimeCore` → 兼容壳可 import 不报错；
  - `RuntimeCore(memory_port=..., emotion_port=..., growth_port=..., personality_port=..., perception_port=..., self_model_port=..., response_port=..., guard_chain=..., adapter_registry=..., event_bus=..., continuity_strategy=..., self_model_strategy=..., logger_port=..., health_monitor=..., token_optimizer=..., persistence_engine=..., identity_snapshot_port=..., identity_anchor_port=..., identity_resolver_port=..., reflection_port=..., stability_port=..., growth_state=...)` 全参数构造不抛 TypeError（21 参数全兼容）；
  - `grep -n "runtime_bridge\|get_runtime_bridge\|RuntimeBridge" src/runtime/runtime.py` → 0 行命中（BLOCKER-8 再重复一次）
- [ ] 4.0.1-F8：runtime.py 兼容壳 process/inject_event/start/shutdown 均转调 `self._impl.*`；`is_started` 属性映射 `self._impl.is_running`
- [ ] 4.0.1-F9：process() 17 阶段中，7-13 在无 adapter 时默认 no-op（不抛异常，仅记录 `_last_process_phase_errors[stage] = "no-op"` 或空）
- [ ] 4.0.1-F10：runtime_core.py RuntimeCore 上新增 3 个诊断 getter：`get_last_process_stage / get_last_process_errors / get_last_process_ctx`

### 1.2 边界验收

- [ ] 4.0.1-B1：并发 `process()` + `inject_event()` + `tick()` 触发时，不出现 AttributeError / KeyError / 数据撕裂（process_lock / _lock 分层保护）
- [ ] 4.0.1-B2：`event=None` / `event 无 type 字段` 场景 fail-soft：仍返回 ctx，不崩溃（LifecycleExecutor 或 RuntimeCore 层 fail-soft 隔离）
- [ ] 4.0.1-B3：未 `start()` 就 `process()` 时自动调 `start()`（与 runtime.py 原语义一致）
- [ ] 4.0.1-B4：`RuntimeStage(name_string)` 在 runtime_core / lifecycle / runtime.py 三处的字符串枚举值完全一致（CONTROL_CHECK / RECEIVE_EVENT / ... / RESPONSE），无错位差一

### 1.3 测试验收

- [ ] 4.0.1-T1：存在 `tests/test_phase_4_0_1_lifecycle_executor.py`，覆盖：LifecycleExecutor.execute → mock core 上 _on_event exactly once、17 阶段顺序正确、单阶段异常不中断其他阶段（至少 4 断言）
- [ ] 4.0.1-T2：存在 `tests/test_phase_4_0_1_runtime_adapter_zero_bridge.py`，覆盖：grep runtime.py 0 命中 Bridge、21 参数构造不 TypeError、process() 结果等于 _impl.process() 返回（至少 3 断言）
- [ ] 4.0.1-T3：至少 13 条 4.0.1 专项测试全部通过（`pytest tests/test_phase_4_0_1_*.py -v` 100% pass）
- [ ] 4.0.1-T4：`pytest tests/runtime/test_runtime_core.py tests/test_phase_3_7_3_runtime_assembly.py tests/test_runtime_unification.py` 无**新增**失败（预存 RUNTIME_VERSION='4.7' 白名单失败不算）

### 1.4 回滚条件

- BLOCKER-1~8 任一失败
- Authority getter 任一行为改变
- 现有 tests 出现 3+ 新失败用例（非新增测试）
- 4.0.1-F5（LifecycleExecutor 不是唯一调度实现）或 F6（_on_event 单调用）不满足（P0 架构修改没落地，直接回滚）

### 1.5 影响范围评估

| 模块 | 影响级别 | 说明 |
|---|---|---|
| RuntimeBridge / api_server 初始化 | 低 | 仍指向 runtime_core.py，行为不变 |
| Orchestrator Authority 获取 | 低 | Authority getter 不变 |
| inject_event / tick 后台路径 | 中 | 保留；process 路径走另一个 RLock，可能影响 tick 延迟（benchmark 观察） |
| runtime.py 现有 100+ 处测试 | 中高 | Adapter 模式 + configure_ports 转发，需要专项覆盖（F7/T2） |
| runtime_core.py 代码增长 | 中 | 新增 process + 10 个 _stage_XX 接口，但调度逻辑在 lifecycle.py（F5 要求），增量应可控 |

---

## 2. 4.0.2 接通生产链（唯一入口 process；不重复 inject_event；Runtime-first）

### 2.1 功能验收（REVIEW P0-修改3 严格对应）

- [ ] 4.0.2-F1：OrchestratorRuntimeBridge._try_runtime(user_message) **不再调用** `rt.inject_event`（grep + mock 计数双验证）
- [ ] 4.0.2-F2：api_server.py 的 chat 两条路径（_process_via_pipeline + orchestrator.process fallback）均**不在 api_server 层**调用 `_runtime_bridge.on_user_message / inject_event / get_runtime_core().inject_event`（原 L481/L495 两处 delete 校验）；后台 tick 路径保留 inject_event 不删
- [ ] 4.0.2-F3：RuntimePipeline.run() **完全**不调 inject_event（任何形式都不），用户聊天唯一入口是 RuntimeBridge.bridge_process → RuntimeCore.process（若 _on_event 调用，必须发生在 process 内部阶段 1，不能在 Pipeline 外层）
- [ ] 4.0.2-F4：RuntimeBridge.bridge_process(user_message, user_id, session_id=None) 可用；**内部绝不调 inject_event**（P0-修改3）
- [ ] 4.0.2-F5：RuntimePipeline.run() runtime-first 模式：先调 Runtime.process；回复非空时**不再** fallback orchestrator.process；回复空时 fallback orchestrator.process；都空时兜底文本
- [ ] 4.0.2-F6：`last_reply_source / last_runtime_mode` 三态正确：
  - Runtime process 有回复 → `"runtime" / "full_process"`
  - process 跑了但空回复 → `"runtime_state+legacy" / "state_only"`
  - Runtime 完全不可用 → `"legacy"` / `None`

### 2.2 边界验收（REVIEW P0-修改3 核心计数验证）

- [ ] 4.0.2-B1（P0 核心）：连续 3 条用户消息 → RuntimeCore 上 `_on_event("user.input", ...)` 计数 = 3（不多不少，**exactly 3**）；同时 inject_event("user.input") 计数 = 0（用户聊天路径绝不能走 inject）
- [ ] 4.0.2-B2：`process_enabled=false + YUYI_RUNTIME_PROCESS_DISABLED=1` → RuntimePipeline 不访问 RuntimeCore.process，也不触发 _on_event
- [ ] 4.0.2-B3：RuntimeBridge 完全未初始化场景 → 仍 fallback，不崩溃

### 2.3 测试验收

- [ ] 4.0.2-T1：`tests/test_phase_4_0_2_process_connected.py`，至少 4 断言覆盖 F1/F3/F5/F6
- [ ] 4.0.2-T2（P0 修改3 核心）：`tests/test_no_duplicate_on_event_call.py`，3 条消息后 mock_runtime_core._on_event.call_count == 3 且 inject_event.call_count == 0（聊天路径）
- [ ] 4.0.2-T3：`pytest tests/test_phase_4_0_2* tests/test_no_duplicate_on_event_call.py -v` 100% 通过

### 2.4 回滚条件

- BLOCKER-1~8 任一失败
- 出现 P0 级消息丢弃（HTTP 200 但回复为空字符串概率 > 1%）
- _on_event 调用次数 / 消息数 > 1.1（重复注入未根除；或 inject_event 仍在聊天路径被调用 > 0）
- F3（RuntimePipeline 仍调 inject_event）或 F4（bridge_process 调 inject_event）不满足 → 4.0.2 直接回滚

### 2.5 影响范围评估

| 模块 | 影响级别 | 说明 |
|---|---|---|
| api_server /v1/chat/completions | 高 | 删除两个 chat 路径的 inject 调用点，需手动验证一次 |
| RuntimePipeline | 高 | Step 5 runtime-first；删前置 inject；_on_event 调用地点转移到 process 内部 |
| OrchestratorRuntimeBridge | 高 | 鸭子探测移除 + handle_message 简化 + 不再调 inject |
| Fallback 路径 | 中 | 新增 runtime-first 双层 fallback，需检查不嵌套死循环 |

---

## 3. 4.0.3 Response Context Pipeline（独立阶段，不涉 Runtime 接通，REVIEW P1-修改6）

### 3.1 功能验收

- [ ] 4.0.3-F1：`engine.generate()` 签名显式含 `identity_context: Optional[str] = None`，位置在 `experience_context` 之后
- [ ] 4.0.3-F2：`_build_messages_original` 中 identity_context 非空时，system prompt 末尾追加 `=== 身份约束 ===\n{identity_context}\n=== /身份约束 ===\n`
- [ ] 4.0.3-F3：`_build_messages_opt` 同上消费 identity_context
- [ ] 4.0.3-F4：identity_context=None/空时，两处 _build_messages 的 messages 结构**与 Phase 4.0 前完全一致**（diff 长度 0 个额外 token）
- [ ] 4.0.3-F5：`Orchestrator.legacy_generate` 调 engine.generate(identity_context=...) 不再 TypeError（不用 **kwargs mock，用真实签名验证）
- [ ] 4.0.3-F6：`Orchestrator.generate_initiative` 同上不再 TypeError
- [ ] 4.0.3-F7：Bridge._legacy_generate 补传 `identity_context / experience_context / context_prompt_blocks` 三个字段或至少前两者（try/except 兜底 None）
- [ ] 4.0.3-F8：ResponseAdapterImpl 中 engine.generate 调用时也从 request 取 identity_context 传入（若存在该调用）

### 3.2 边界验收

- [ ] 4.0.3-B1：identity_context 长度 0 / 纯空白 → 消费逻辑完全跳过，不插额外换行
- [ ] 4.0.3-B2：identity_context 含 `=== 身份约束 ===` 字符串（嵌套）时不破坏 prompt（可正则检查块闭合）
- [ ] 4.0.3-B3：_mock_response 下 identity_context 可传入（不报错）

### 3.3 测试验收

- [ ] 4.0.3-T1：`tests/test_phase_4_0_3_identity_context_engine.py`，覆盖 F1/F2/F4/F5/F6 ≥ 5 断言
- [ ] 4.0.3-T2（REVIEW P1-修改6 核心：阶段隔离）：**本阶段内 diff 的文件集** 不包含 `api_server.py / runtime_bridge.py / orchestrator_runtime_bridge.py / runtime_pipeline.py / runtime_core.py / runtime.py / lifecycle.py`（这些是 4.0.2 的文件；4.0.3 只许改 engine、orch 的 identity_context 小修、Bridge._legacy_generate 参数、ResponseAdapterImpl；**阶段隔离 = 故障能定位层**）
- [ ] 4.0.3-T3：`pytest tests/test_phase_4_0_3* -v` 100% 通过
- [ ] 4.0.3-T4：旧的 tests/test_orchestrator_initiative.py / tests/test_legacy_generate.py 无新失败

### 3.4 回滚条件

- BLOCKER-1~8 任一失败
- engine.generate 任何调用点出现新 TypeError
- prompt 结构变更导致 LLM 回复质量明显下降（A/B：identity_context=None 时与 Phase 4.0 前对比）
- T2（阶段隔离）不满足 → 4.0.3 回滚，按阶段边界重新拆分

### 3.5 影响范围评估

| 模块 | 影响级别 | 说明 |
|---|---|---|
| src/engine.py | 中 | 显式加参数 + 两处 _build_messages 消费；None 时完全向后兼容 |
| src/orchestrator.py legacy_generate / initiative | 低 | 不删现有 identity_context 传入；行为从崩溃 → 正常生效 |
| Bridge._legacy_generate | 中低 | 补传 2-3 字段 |
| ResponseAdapterImpl | 低 | 接口扩展（若存在调用） |

---

## 4. 4.0.4 Memory 事务（SPEC REVIEW 修正版：common/storage 位置 + SafeVectorMemoryAdapter）

### 4.1 功能验收（REVIEW P1-修改4 / 修改5 严格对应）

- [ ] 4.0.4-F1（REVIEW P1-修改4）：
  - `src/common/storage/atomic_write.py` 文件存在（不在 runtime/utils）；
  - 对外导出 `file_atomic_write_lock(obj, *, timeout_s=30)` 上下文管理器；
  - `src/runtime/utils/atomic_memory_write_guard.py` **不存在**（或已彻底删除，不允许两种并存）
- [ ] 4.0.4-F2：Orchestrator Step 10 中 memory_store.add 赋给 `result` 变量，后续 VectorMemory.add_memory / MemoryCreatedEvent 发布 / audit.success 仅在 `result is not None` 时执行
- [ ] 4.0.4-F3：PollutionGuard 拒绝 memory 时，MemoryCreatedEvent 发布次数 = 0（事件事实化）
- [ ] 4.0.4-F4：memory_store.add 返回 None 时 audit log 记录 `memory.created rejected` 且日志级别为 WARNING
- [ ] 4.0.4-F5：MemoryAdapter.store_experience 调 `self._store.add(...)` 时也进入相同 `file_atomic_write_lock(self._store)` 上下文
- [ ] 4.0.4-F6（REVIEW P1-修改5 核心：SafeVectorMemoryAdapter 全路径）：
  - `src/infrastructure/vector/safe_vector_adapter.py` 存在，含 `class SafeVectorMemoryAdapter(inner_vector)`；
  - add_memory 前置校验（role 白名单 / len(content)≥5 / 不含污染标签）都通过才 `inner.add_memory(record)`，任一失败 warning + return False；
  - Orchestrator 初始化时 `self.vector_memory = SafeVectorMemoryAdapter(self._raw_vector_memory)`；
  - grep `VectorMemory(` 除了 `_raw_vector_memory = VectorMemory(...)` 这一初始化点外，其他调用方都用 SafeVectorMemoryAdapter 包装（0 处裸 new 使用）

### 4.2 边界验收

- [ ] 4.0.4-B1：100 线程 × 同时 memory_store.add（不同 id），最终记录数 = success 数（0 丢失）
- [ ] 4.0.4-B2：add 抛异常时，锁仍然释放（上下文 __exit__ 中 finally 处理），不出现死锁
- [ ] 4.0.4-B3：memory_store.add 返回 None → mock vector_memory.add_memory 调用次数 = 0（0 污染）
- [ ] 4.0.4-B4：MemoryCreatedEvent 发布次数 = memory_store.add success 次数（mock 计数精确相等）
- [ ] 4.0.4-B5：WeakValueDictionary key 边界：GC 大量 store 实例后重新创建不出现 ID 复用死锁（若实现选 path 字符串 key 则此条自动 pass）

### 4.3 测试验收

- [ ] 4.0.4-T1：`tests/test_phase_4_0_4_memory_atomic.py` 覆盖 F1/F2/F3/F5/F6 ≥ 5 断言
- [ ] 4.0.4-T2：多线程并发测试（100 线程 add 不同 id）无丢失
- [ ] 4.0.4-T3："拒绝 0 污染向量库"测试，vector_memory.add_memory.call_count == 0（当 memory_store.add 返回 None）
- [ ] 4.0.4-T4：`pytest tests/test_phase_4_0_4* -v` 100% 通过
- [ ] 4.0.4-T5：`pytest tests/test_memory_normalizer.py tests/test_memory_store*.py` 无新失败

### 4.4 回滚条件

- BLOCKER-1~8 任一失败
- 多线程下仍出现 > 1% 记录丢失
- F1（atomic_write 放错位置）或 F6（SafeVectorMemoryAdapter 未被所有调用方使用）不满足 → P1 架构修改未落地，本阶段直接回滚
- 30 秒 600 次写入后进程仍不响应（疑似死锁）

### 4.5 影响范围评估

| 模块 | 影响级别 | 说明 |
|---|---|---|
| src/orchestrator.py Step 10 | 中高 | 记忆写入序列重写，需要覆盖所有异常分支 |
| src/runtime/adapters/memory_adapter.py | 中 | store_experience 加 lock 包装 |
| src/common / src/infrastructure（新增） | 新增 | 不影响现有模块；未来 Snapshot/Audit 可复用 |
| 记忆写入性能 | 中 | 增加锁 ~0.1ms 等待；聊天频率下可忽略 |

---

## 5. 4.0.5 Runtime 诊断面板

### 5.1 功能验收

- [ ] 4.0.5-F1：RuntimeProvider.set_orchestrator_ref(orch) 存在，api_server.py 初始化 orch 后被调用
- [ ] 4.0.5-F2：`GET /api/v1/runtime/overview` 返回 JSON 中 data["runtime"] 新增 12 键：
  orchestrator_runtime_status / last_reply_source / last_runtime_mode / last_runtime_error / last_legacy_reason / runtime_call_count / runtime_state_count / legacy_call_count / runtime_mode / last_event / last_phase / memory_write_success / growth_triggered（≥12 个）
- [ ] 4.0.5-F3：`GET /api/v1/runtime/status` 追加至少 `last_runtime_error / orchestrator_runtime_status`
- [ ] 4.0.5-F4：`GET /api/dashboard/v2/runtime/status` 追加至少 `last_reply_source / last_runtime_mode`
- [ ] 4.0.5-F5：发送一次聊天后，上述 last_* / *_count 字段立即更新（非每次请求重新 new orch）

### 5.2 边界验收

- [ ] 4.0.5-B1：orchestrator_ref=None → 接口仍 200，新增字段为 null / 0（不 500）
- [ ] 4.0.5-B2：orch.runtime_stats() 抛异常 → provider 吞掉，字段为 null（可观测性不阻塞聊天）
- [ ] 4.0.5-B3：config.runtime.enabled=false → runtime_mode="offline"

### 5.3 测试验收

- [ ] 4.0.5-T1：`tests/test_phase_4_0_5_diagnostic_fields.py`（Flask test_client GET /runtime/overview 断言 12+ 新键存在；一次聊天后 runtime_call_count/legacy_count 其一 > 0；orch=None 仍 200）
- [ ] 4.0.5-T2：`pytest tests/test_phase_4_0_5* -v` 100% 通过
- [ ] 4.0.5-T3：`pytest tests/test_server_api_gateway.py tests/test_admin_api_*.py` 无新失败

### 5.4 回滚条件

- BLOCKER-1~8 任一失败
- `/api/v1/runtime/overview` 返回 500（即使 orch 未注入也应 200 + null 字段）
- 新增字段在 orch 未初始化场景下阻塞启动

### 5.5 影响范围评估

| 模块 | 影响级别 | 说明 |
|---|---|---|
| src/admin/runtime_provider.py | 中 | orchestrator_ref + stats 聚合 |
| src/control/api/routes.py | 中 | overview/status 扩展 |
| src/admin/dashboard/runtime_router.py | 中低 | 扩展 2 字段 |
| api_server.py 启动 | 低 | 增加 1 次 provider 注入 |

---

## 6. 4.0.6 全链路测试（Sign-off 级）

### 6.1 功能验收

- [ ] 4.0.6-F1：E2E mock LLM 下，一次聊天完整调用栈通过 RuntimeCore.process() 各阶段（阶段 hook 计数，17 阶段均触发）
- [ ] 4.0.6-F2：process 返回的 `ctx._final_reply` 非空，被作为最终 HTTP 响应返回
- [ ] 4.0.6-F3：Runtime.process 返回空时，最终 HTTP 响应仍由 fallback 生成（非空）
- [ ] 4.0.6-F4：config.runtime.enabled=false → 走旧 orchestrator.process（Runtime.process 调用次数 = 0）
- [ ] 4.0.6-F5：config.runtime.process_enabled=false → Runtime.process 调用次数 = 0
- [ ] 4.0.6-F6：_get_identity_context 固定返回"羽依必须温柔"→ engine._last_messages 的 system prompt 末尾含 `=== 身份约束 ===` 块
- [ ] 4.0.6-F7：PollutionGuard pass → memory.json 与 VectorMemory 同步写入；PollutionGuard reject → 两者都 0 写入（无不一致）
- [ ] 4.0.6-F8：聊天一次后 `/runtime/overview` runtime_mode 不是 "offline"，last_reply_source 非空

### 6.2 边界验收

- [ ] 4.0.6-B1：Runtime.process 任一阶段抛 AttributeError / TypeError → 仍 fallback 不崩溃
- [ ] 4.0.6-B2：20 线程并发聊天 → HTTP 200 比例 100%（不挂起不超时）
- [ ] 4.0.6-B3：关闭所有 Runtime 开关后，p95 聊天延迟相比 Phase 4.0 前增幅 < 10%

### 6.3 测试验收

- [ ] 4.0.6-T1：`tests/test_phase_4_0_6_e2e.py` 覆盖 F1~F8 全部 8 条
- [ ] 4.0.6-T2：`pytest tests/test_phase_4_0_6_e2e.py -v` 100% 通过
- [ ] 4.0.6-T3：全量 pytest `python -m pytest tests/ -x` 100% 通过（允许 test_phase_3_* 等历史专项单独跑）
- [ ] 4.0.6-T4：手动 smoke：真实启动 api_server → `curl -X POST /v1/chat/completions ...` → 非空回复 + `/runtime/overview` 正确反映本次调用

### 6.4 Sign-off 回滚条件（任一触发即整体回滚到 main）

- BLOCKER-1~8 任一失败
- 4.0.6-F1~F8 任一不满足
- 全量 pytest 失败 > 5 个
- 手动 smoke：5 次聊天尝试全部兜底或空（功能瘫痪）

---

## 7. 最终 Sign-off 条件

```
BLOCKER 1~8     = 8 项 全部勾选
+
阶段功能 4.0.1-F → 4.0.5-F = 约 50 项全部勾选
+
阶段边界 4.0.1-B → 4.0.5-B = 约 18 项全部勾选
+
阶段测试 4.0.1-T → 4.0.5-T = 约 20 项全部勾选
+
4.0.6 E2E F/B/T 合计 15 项全部勾选
= 100% → Sign-off。
```

> 若规格需再修改，更新 spec.md → 重审对应 check_list.md + tasks.md 条目后再进入实现。
