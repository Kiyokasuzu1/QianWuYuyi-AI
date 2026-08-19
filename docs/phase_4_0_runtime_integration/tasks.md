# Phase 4.0 Runtime Integration — 任务分解清单

> 规格冻结版 v0.2（对应 spec.md v0.2 / check_list.md v0.2 / SPEC Review 6 点修改后版本）
> 日期：2026-08-08
> 实施顺序：严格按 4.0.1 → 4.0.6；每阶段 Gate 通过后方可进入下一阶段。
> Review 对应：spec.md §0 6 点修改速览表格

---

## 全局前置任务（实施 Phase 4.0 任何子阶段前必须完成）

| # | 任务 | 验证方式 |
|---|---|---|
| G-1 | 从 main 创建独立分支：`phase-4-0-runtime-integration`（**不得直推 main**） | `git branch --show-current` 输出正确分支名 |
| G-2 | 当前分支基线：`python -m pytest tests/ -x --ignore=tests/test_phase_3_*` 全通过（作为回归基准线） | 运行命令，exit code 0 |
| G-3 | 备份当前 `src/` 目录快照（或 commit hash 记录） | `git rev-parse HEAD` 记录到实施日志 |
| G-4 | 确认以下目录无本地修改（硬约束保护目录）：`src/memory/`、`src/growth/`、`src/personality/`、`src/self_model/`、`src/control/`、`src/runtime/policy/` | `git diff --name-only -- src/memory src/growth src/personality src/self_model src/control src/runtime/policy` 输出为空 |
| G-5 | 确认 `config.yaml` 无 API Key 字段修改（diff 检查） | `git diff config.yaml | grep -E "api_key|API_KEY|token" | wc -l` = 0 |

---

## 4.0.1 统一 RuntimeCore（LifecycleExecutor 独立 + Adapter 壳零 Bridge 依赖）

### 目标
`src/runtime/runtime_core.py` 的 `RuntimeCore` 类获得 `process(event, ctx) -> RuntimeContext` 能力，但**不手写 17 阶段**（调度逻辑在独立 `LifecycleExecutor`）；同时 `src/runtime/runtime.py` 改为纯 Adapter 兼容壳层（**严禁依赖 RuntimeBridge**，防循环依赖）。

### 文件变更清单

#### T4.0.1-1：`src/runtime/lifecycle.py`（★新增 P0 文件，REVIEW 修改 2 核心）

| 变更项 | 说明 |
|---|---|
| 1. RuntimeStage 枚举 | 从原 `runtime.py` 提取 `RuntimeStage(IntEnum)` 全量 17 个阶段枚举项（CONTROL_CHECK / RECEIVE_EVENT / MEMORY_RETRIEVAL / EMOTION_UPDATE / GROWTH_EVALUATION / PERSONALITY_UPDATE / PERSONALITY_CONTEXT_BUILD / PERCEPTION_×N / SELF_MODEL_×N / RESPONSE_GENERATION / GUARD_CHAIN / RESPONSE） |
| 2. RUNTIME_LIFECYCLE_ORDER 常量 | `RUNTIME_LIFECYCLE_ORDER: List[RuntimeStage] = [...]` 顺序与原 runtime.py 完全一致（17 项） |
| 3. LifecycleExecutor 类 | **新增类，17 阶段调度唯一实现点**：<br><br>```python<br>class LifecycleExecutor:<br>    """17 阶段调度器（纯调度，不持有业务状态）。<br>    <br>    唯一实现位置：LifecycleExecutor.execute()<br>    RuntimeCore 组合持有，作为私有成员调用。<br>    """<br><br>    def __init__(self, logger=None):<br>        self._logger = logger or logging.getLogger(__name__)<br><br>    def execute(self, core: object, event: Optional[Event], ctx: RuntimeContext) -> RuntimeContext:<br>        """执行完整生命周期（17 阶段）。<br><br>        契约：<br>        - 阶段 1（RECEIVE_EVENT）内部**必须且仅一次**调用 core._on_event(...)（P0-修改3）<br>        - 单个阶段异常必须 fail-soft（logger.warning + ctx 记录，不中断后续阶段）<br>        - 阶段 7-13（PERCEPTION / SELF_MODEL_*）在 core 无对应接口时默认 no-op<br>        """<br>        for stage in RUNTIME_LIFECYCLE_ORDER:<br>            ctx.current_stage = stage<br>            try:<br>                self._dispatch_stage(core, stage, event, ctx)<br>            except Exception as exc:<br>                errors = getattr(ctx, "_phase_errors", None)<br>                if isinstance(errors, dict):<br>                    errors[stage.name] = repr(exc)<br>                self._logger.warning(<br>                    "[LifecycleExecutor] stage=%s 失败（已隔离）: %s",<br>                    stage.name, exc,<br>                )<br>                continue<br>        return ctx<br><br>    def _dispatch_stage(self, core, stage, event, ctx) -> None:<br>        """分派到 core._stage_XX_*(event, ctx)。<br>        <br>        映射表（与 RUNTIME_LIFECYCLE_ORDER 顺序一致）：<br>        CONTROL_CHECK               → core._stage_00_control_check(event, ctx)<br>        RECEIVE_EVENT               → core._stage_01_receive_event(event, ctx)  ← 内部必须调 core._on_event<br>        MEMORY_RETRIEVAL            → core._stage_02_memory_retrieval(event, ctx)<br>        EMOTION_UPDATE              → core._stage_03_emotion_update(event, ctx)<br>        GROWTH_EVALUATION           → core._stage_04_growth_evaluation(event, ctx)<br>        PERSONALITY_UPDATE          → core._stage_05_personality_update(event, ctx)<br>        PERSONALITY_CONTEXT_BUILD   → core._stage_06_personality_context_build(event, ctx)<br>        PERCEPTION_*（4 个）         → core._stage_07_xx ~ _stage_10_xx（不存在则 no-op）<br>        SELF_MODEL_*（3 个）         → core._stage_11_xx ~ _stage_13_xx（不存在则 no-op）<br>        RESPONSE_GENERATION         → core._stage_14_response_generation(event, ctx)<br>        GUARD_CHAIN                 → core._stage_15_guard_chain(event, ctx)<br>        RESPONSE                    → core._stage_16_response(event, ctx)<br>        """<br>        method_name = self._stage_method_name(stage)<br>        method = getattr(core, method_name, None)<br>        if callable(method):<br>            method(event, ctx)<br>        # 未实现 → 默认 no-op（兼容渐进接入）<br><br>    @staticmethod<br>    def _stage_method_name(stage: RuntimeStage) -> str:<br>        mapping = {<br>            "CONTROL_CHECK": "_stage_00_control_check",<br>            "RECEIVE_EVENT": "_stage_01_receive_event",<br>            "MEMORY_RETRIEVAL": "_stage_02_memory_retrieval",<br>            "EMOTION_UPDATE": "_stage_03_emotion_update",<br>            "GROWTH_EVALUATION": "_stage_04_growth_evaluation",<br>            "PERSONALITY_UPDATE": "_stage_05_personality_update",<br>            "PERSONALITY_CONTEXT_BUILD": "_stage_06_personality_context_build",<br>            # 7-13 感知/自我模型<br>            "PERCEPTION_EXTERNAL": "_stage_07_perception_external",<br>            "PERCEPTION_INTERNAL": "_stage_08_perception_internal",<br>            "PERCEPTION_SOCIAL": "_stage_09_perception_social",<br>            "PERCEPTION_TEMPORAL": "_stage_10_perception_temporal",<br>            "SELF_MODEL_REFLECTION": "_stage_11_self_model_reflection",<br>            "SELF_MODEL_INTEGRATION": "_stage_12_self_model_integration",<br>            "SELF_MODEL_CONTINUITY": "_stage_13_self_model_continuity",<br>            # 14-16 输出<br>            "RESPONSE_GENERATION": "_stage_14_response_generation",<br>            "GUARD_CHAIN": "_stage_15_guard_chain",<br>            "RESPONSE": "_stage_16_response",<br>        }<br>        return mapping.get(stage.name, f"_stage_unknown_{stage.name}")<br>``` |
| 4. 模块级导出 | 文件末尾：`__all__ = ["RuntimeStage", "RUNTIME_LIFECYCLE_ORDER", "LifecycleExecutor"]` |

#### T4.0.1-2：`src/runtime/runtime_core.py`（主文件 ★核心，组合持有 LifecycleExecutor）

| 变更项 | 位置 | 详细说明 |
|---|---|---|
| 1. 新增 imports | 文件顶部 | 追加：<br>`from typing import Optional as _Opt, Dict as _Dict, Any as _Any`<br>`from dataclasses import field, asdict`<br>`from src.runtime.context.runtime_context import RuntimeContext`<br>`from src.runtime.events import Event`<br>`from src.runtime.lifecycle import RuntimeStage, RUNTIME_LIFECYCLE_ORDER, LifecycleExecutor` |
| 2. 删除原内嵌 RuntimeStage 定义 | 类定义前区域 | 若之前内嵌了 RuntimeStage，**必须删除**，统一从 lifecycle.py import（避免双定义） |
| 3. 类新增属性 | `RuntimeCore.__init__` 末尾，`_lock` 初始化之后 | 追加：<br>`self._lifecycle = LifecycleExecutor(logger=logger)`  ← 组合持有，P0-修改2<br>`self._phase_runtime_ctx: _Opt[RuntimeContext] = None`<br>`self._last_process_event_type: _Opt[str] = None`<br>`self._last_process_stage: _Opt[str] = None`<br>`self._last_process_phase_errors: _Dict[str, str] = {}`<br>`self._started_process_mode: bool = False`<br>`self._process_lock = threading.RLock()`（与 `self._lock` 分工，`_lock` 管 state，`_process_lock` 管 process() 并发） |
| 4. 新增 `process()` 方法（**单调用 LifecycleExecutor**，P0-修改2 核心） | `RuntimeCore` 类内，`inject_event()` 方法之后 | **签名**：`def process(self, event: _Opt[Event], ctx: _Opt[RuntimeContext] = None) -> RuntimeContext:`<br>**实现（严禁手写 17 阶段 if/elif）**：<br>```python<br>def process(self, event, ctx=None):<br>    """一次完整生命循环调度。<br>    <br>    用户聊天唯一入口（P0-修改3）。<br>    17 阶段调度 **唯一实现** = self._lifecycle.execute（不在此处写分支）。<br>    """<br>    with self._process_lock:<br>        if not self.is_running:<br>            try:<br>                self.start()<br>            except Exception as exc:<br>                logger.warning("[RuntimeCore.process] auto-start 失败（继续）: %s", exc)<br>        ctx = ctx or RuntimeContext()<br>        ctx._current_event = event<br>        ctx._phase_errors = self._last_process_phase_errors  # LifecycleExecutor 直接写同一 dict<br>        self._last_process_phase_errors.clear()<br>        self._last_process_event_type = getattr(event, "type", None) if event else None<br>        # ===== 单调用！禁止在此手写 for RUNTIME_LIFECYCLE_ORDER =====<br>        self._lifecycle.execute(self, event, ctx)<br>        # ===== 上面这一行就是 17 阶段的全部调度 =====<br>        self._last_process_stage = getattr(ctx, "current_stage", None)<br>        if isinstance(self._last_process_stage, RuntimeStage):<br>            self._last_process_stage = self._last_process_stage.name<br>        self._phase_runtime_ctx = ctx<br>        self._started_process_mode = True<br>        return ctx<br>```<br>**关键校验**：此方法内**不得出现** `for stage in RUNTIME_LIFECYCLE_ORDER`、`if stage ==`、`RuntimeStage.xxx ==` 等调度分派代码；否则违反 P0-修改2。 |
| 5. 实现 10 个有效阶段方法（core 提供接口，LifecycleExecutor 回调） | 类内，`process()` 之后（可分块） | **注意**：各方法不得调用不存在的 port/adapter，必须先检查属性存在性再调用。最低要求实现以下 10 个（7-13 阶段默认缺省即 no-op）：<br><br>  **`_stage_00_control_check(event, ctx)`**：检查 `self._control_state`（若存在）；若 `safe_mode`，则 `ctx._control_blocked = True` 并 return（后续阶段据此降级）<br><br>  **`_stage_01_receive_event(event, ctx)`（P0-修改3 核心，必须且仅一次调 _on_event）**：<br>  ```python<br>  def _stage_01_receive_event(self, event, ctx):<br>      """Receive Event 阶段。<br>      <br>      唯一状态变更入口：内部调用 self._on_event 一次。<br>      用户聊天路径：inject_event 不在外层调用，仅此处发生一次 mutate。（P0-修改3）<br>      """<br>      if event is None:<br>          return<br>      # 取用户消息<br>      payload = getattr(event, "payload", None) or {}<br>      if isinstance(payload, dict):<br>          user_msg = payload.get("text") or payload.get("content") or ""<br>      else:<br>          user_msg = str(payload)<br>      ctx.user_message = user_msg<br>      self._last_process_event_type = getattr(event, "type", None)<br>      # ===== P0-修改3：一次用户消息 → 一次 _on_event 调用 =====<br>      event_type_mapping = {<br>          "user_input": "user.input",<br>          "user.input": "user.input",<br>          "system_tick": "system.tick",<br>          "growth_proposal": "growth.proposal",<br>      }<br>      mapped_type = event_type_mapping.get(self._last_process_event_type, self._last_process_event_type or "generic.event")<br>      try:<br>          self._on_event(mapped_type, payload if isinstance(payload, dict) else {"raw": payload})<br>      except Exception as exc:<br>          logger.warning("[RuntimeCore._stage_01] _on_event 失败: %s", exc)<br>  ```<br>  **校验**：`_on_event` 的调用点在此方法内；**严禁**在 process() 外层、bridge、pipeline、api_server 等用户路径调用 `inject_event` / `_on_event`（P0-修改3）。<br><br>  **`_stage_02_memory_retrieval(event, ctx)`**：若有 `self.memory_adapter` 且 callable `retrieve` → `self.memory_adapter.retrieve(ctx)`；否则 fallback：若 `self.memory_store` 存在 → `ctx.retrieved_memories = self.memory_store.get_recent(limit=20)`（取最近 20 条）<br><br>  **`_stage_03_emotion_update(event, ctx)`**：若 `self.emotion_manager` 存在且 event/payload 非空：优先调 `update_from_event(event_type=..., payload=...)`（若有）；否则取 `current_state`（若有）存入 `ctx.emotion_snapshot`<br><br>  **`_stage_04_growth_evaluation(event, ctx)`**：若 `self.growth_adapter` 存在且 callable `evaluate` → 调它；否则 no-op。<br><br>  **`_stage_05_personality_update(event, ctx)`**：若 `self.personality_resolver` 存在且 callable `resolve` → 调它；否则若 `self.personality_adapter` 存在且 callable `snapshot` → `ctx.personality_snapshot = adapter.snapshot()`<br><br>  **`_stage_06_personality_context_build(event, ctx)`**：聚合 personality_snapshot + self_model_store continuity/stability 报告 + identity（若有）→ 生成自然语言摘要字符串存入 `ctx.personality_context_text`（无数据存空字符串，不存 None）。**注意**：identity_context 不在这里注入，留到 4.0.3 独立 Phase（P1-修改6）。<br><br>  **`_stage_14_response_generation(event, ctx)`**：<br>  - 优先：若 `self.response_adapter` 或 adapter_registry 存在 ResponseAdapter → 调 `adapter.generate(ResponseRequest(...))`，reply 存 `ctx._final_reply`<br>  - 降级：若 orch 通过 bridge 注入了 engine ref（属性 `self._orchestrator_engine_ref`）→ 调 `engine.generate(prompt=ctx.user_message, history=...)`，reply 存 `ctx._final_reply`。**此处先不传 identity_context**（留到 4.0.3 Response Context Pipeline 独立处理，P1-修改6）<br>  - 兜底：以上都不可用 → `ctx._final_reply = None`（让上层 fallback）<br><br>  **`_stage_15_guard_chain(event, ctx)`**：默认 no-op；若 `self.guard_chain` 存在且 callable `validate` → 调 `validate(ctx)`，返回 False 时清掉 `ctx._final_reply`<br><br>  **`_stage_16_response(event, ctx)`**：若 `ctx._final_reply` 存在且非空字符串 → 存 `ctx.finalized_reply = ctx._final_reply`；否则 no-op。 |
| 6. 新增诊断 getter | `get_runtime_health_report` 之后 | 新增方法：<br>`def get_last_process_stage(self) -> _Opt[str]: return self._last_process_stage`<br>`def get_last_process_errors(self) -> _Dict[str, str]: return dict(self._last_process_phase_errors)`<br>`def get_last_process_ctx(self) -> _Opt[RuntimeContext]: return self._phase_runtime_ctx`（不改 Authority getter 行为） |

#### T4.0.1-3：`src/runtime/runtime.py`（兼容壳层 Adapter 模式，★零 Bridge 依赖，P0-修改1 核心）

| 变更项 | 位置 | 详细说明 |
|---|---|---|
| 1. 顶部 import 调整（零 Bridge） | 文件顶部 | **删除**任何 runtime_bridge 相关 import（若存在）；**保留**全部现有 imports（保证测试模式能单独运行参考实现）；**新增仅一行**：<br>`from src.runtime.runtime_core import RuntimeCore as _RuntimeCoreImpl`<br>**严禁**：`import / from ... runtime_bridge / get_runtime_bridge / RuntimeBridge` 任何形式（P0-修改1 BLOCKER-8）。 |
| 2. `class RuntimeCore` 修改构造函数（Adapter 模式：self._impl） | `__init__(...)` 方法体 | 原构造函数**不要删除**，提取到 `_run_old_init(...)`；方法体开头增加 Adapter 模式分支：<br><br>```python<br># P0-修改1：Adapter 模式，默认启用（不走 Bridge，不产生循环依赖风险）<br>_ADAPTER_MODE = os.environ.get("YUYI_RUNTIME_LEGACY_ASSEMBLY", "0") != "1"<br>if _ADAPTER_MODE:<br>    # 直接 new Impl，不走 Bridge（零 Bridge 依赖！）<br>    self._impl = _RuntimeCoreImpl()<br>    # 将传入的 21 个 port 参数转发给 impl（若 impl 有 configure_ports 或对应 setter）<br>    self._configure_impl_ports(locals())<br>    # 兼容旧版返回值约定：start() 返回 RuntimeContext<br>    self._started_ctx_placeholder = None<br>    return<br># 自建实例模式（YUYI_RUNTIME_LEGACY_ASSEMBLY=1 时走旧逻辑）<br>self._impl = None<br>self._run_old_init(memory_port, emotion_port, growth_port, personality_port, perception_port, self_model_port, response_port, guard_chain, adapter_registry, event_bus, continuity_strategy, self_model_strategy, logger_port, health_monitor, token_optimizer, persistence_engine, identity_snapshot_port, identity_anchor_port, identity_resolver_port, reflection_port, stability_port, growth_state)<br>```<br>**核心校验**：此处**不得**出现 `get_runtime_bridge()` / `RuntimeBridge` / `bridge = ` 任何字样。 |
| 3. `process()` 方法委托 | `def process(...)` 方法开头 | ```python<br>def process(self, event, ctx=None):<br>    if self._impl is not None:<br>        return self._impl.process(event, ctx)<br>    # 否则走原 runtime.py 自建 17 阶段实现（保留完整，不删除）<br>    ...（原代码保留）<br>``` |
| 4. `inject_event()` 方法委托 | 若存在 inject_event 或新增 | ```python<br>def inject_event(self, event_type, event_data=None):<br>    if self._impl is not None:<br>        return self._impl.inject_event(event_type, event_data)<br>    # 旧模式 fallback（若原 runtime.py 有等价实现）<br>    ...<br>``` |
| 5. `start()` / `shutdown()` / `is_started` 映射 | 对应方法 | `start()`：<br>```python<br>def start(self):<br>    if self._impl is not None:<br>        ok = self._impl.start()<br>        # 兼容旧版语义：start 返回 RuntimeContext<br>        from src.runtime.context.runtime_context import RuntimeContext<br>        self._started_ctx_placeholder = RuntimeContext()<br>        return self._started_ctx_placeholder if ok else None<br>    return self._run_old_start()  # 原实现<br>```<br>`shutdown()`：`if self._impl: return self._impl.stop()`<br>`is_started`（property）：`if self._impl: return self._impl.is_running` |
| 6. RuntimeStage / RUNTIME_LIFECYCLE_ORDER re-export | 文件末尾（类外） | ```python<br>from src.runtime.lifecycle import (<br>    RuntimeStage as _RS_internal,<br>    RUNTIME_LIFECYCLE_ORDER as _RLO_internal,<br>)<br>RuntimeStage = _RS_internal  # 兼容 from src.runtime.runtime import RuntimeStage<br>RUNTIME_LIFECYCLE_ORDER = _RLO_internal<br>``` |
| 7. `_configure_impl_ports()` 私有方法 | 类内新增 | 安全地将 21 个 port 参数转发到 impl（hasattr 检查，没有就忽略，不抛错）：<br>```python<br>def _configure_impl_ports(self, local_vars: dict) -> None:<br>    if self._impl is None:<br>        return<br>    port_attrs = [<br>        "memory_port", "emotion_port", "growth_port", "personality_port",<br>        "perception_port", "self_model_port", "response_port", "guard_chain",<br>        "adapter_registry", "event_bus", "continuity_strategy", "self_model_strategy",<br>        "logger_port", "health_monitor", "token_optimizer", "persistence_engine",<br>        "identity_snapshot_port", "identity_anchor_port", "identity_resolver_port",<br>        "reflection_port", "stability_port", "growth_state",<br>    ]<br>    for attr in port_attrs:<br>        val = local_vars.get(attr)<br>        if val is None:<br>            continue<br>        # impl 有对应 setter 就 set，没有就 skip<br>        setter = f"set_{attr}"<br>        if callable(getattr(self._impl, setter, None)):<br>            try: getattr(self._impl, setter)(val)<br>            except Exception: pass<br>        elif hasattr(self._impl, attr):<br>            try: setattr(self._impl, attr, val)<br>            except Exception: pass<br>``` |

#### T4.0.1-4：`src/runtime/__init__.py`（包级导出，同步 lifecycle）

| 变更项 | 说明 |
|---|---|
| 1. RuntimeStage 与 RUNTIME_LIFECYCLE_ORDER 来源 | 若之前从 stages.py 导出，改为 `from src.runtime.lifecycle import RuntimeStage, RUNTIME_LIFECYCLE_ORDER, LifecycleExecutor` |
| 2. 追加 LifecycleExecutor 到 `__all__` | `__all__` 追加 `"LifecycleExecutor"` |

#### T4.0.1-5：删除旧 `src/runtime/stages.py`（若存在）

| 变更项 | 说明 |
|---|---|
| 删除理由 | 功能已并入 `lifecycle.py`（P0-修改2：LifecycleExecutor 是单调度源），保留 stages.py 会造成双定义风险。 |
| 兼容性处理 | 若 tests/ 中有 `from src.runtime.stages import ...`，全局批量替换为 `from src.runtime.lifecycle import ...` |

### 测试文件（每文件断言数 ≥ 指定）

| 测试文件 | 覆盖要求 | 断言数 ≥ |
|---|---|---|
| `tests/test_phase_4_0_1_lifecycle_executor.py`（★新增 P0） | ① LifecycleExecutor.execute 调用 mock core._on_event **exactly once**（P0-修改3 验证）；② 17 阶段按 RUNTIME_LIFECYCLE_ORDER 顺序回调（单测计数验证）；③ 单阶段抛异常不中断后续阶段（fail-soft 验证）；④ execute() 内部手写调度实现（不在 runtime_core 写） | 5 |
| `tests/test_phase_4_0_1_runtime_core_unified.py` | ① RuntimeCore.process() 内部调用 `self._lifecycle.execute` **exactly once**（mock 计数）；② process() 方法体**不得**手写 `for stage in`（通过 inspect.getsource 字符串断言）；③ 诊断 getter 正常返回；④ 17 阶段枚举一致（与 lifecycle.py 对比） | 5 |
| `tests/test_phase_4_0_1_runtime_adapter_zero_bridge.py`（★新增 P0，BLOCKER-8） | ① `grep -n "runtime_bridge\|get_runtime_bridge\|RuntimeBridge" src/runtime/runtime.py` → 0 行命中（subprocess 执行断言）；② `RuntimeCore(21 参数全量)` 构造不抛 TypeError；③ Adapter 模式下 process() 结果 == self._impl.process() 返回值；④ `YUYI_RUNTIME_LEGACY_ASSEMBLY=1` 环境变量切换到旧模式时，self._impl 为 None | 4 |

### Gate（进入 4.0.2 前必须全部通过）

- [ ] `python -m pytest tests/test_phase_4_0_1_lifecycle_executor.py tests/test_phase_4_0_1_runtime_core_unified.py tests/test_phase_4_0_1_runtime_adapter_zero_bridge.py -v` 100% 通过
- [ ] `python -m pytest tests/runtime/test_runtime_core.py tests/test_phase_3_7_3_runtime_assembly.py tests/test_runtime_unification.py tests/test_phase_4_0_1_runtime_core_unified.py -v` 无**新增**失败
- [ ] `python -m pytest tests/ -x --ignore=tests/test_phase_3_*` 全通过（回归检查）
- [ ] BLOCKER-1~8（check_list.md §0）**全部**满足（含 BLOCKER-8 runtime.py 零 Bridge 依赖）
- [ ] 架构约束：`grep -c "for stage in RUNTIME_LIFECYCLE_ORDER" src/runtime/runtime_core.py` = 0；`grep -c "RuntimeStage\." src/runtime/runtime_core.py` ≤ 5（只允许 import + stage.name 访问，不允许分支判断）

---

## 4.0.2 接通生产链（用户入口唯一 process；零提前 inject_event；Runtime-first）

### 目标（REVIEW P0-修改3 核心）
- 删除 api_server / RuntimePipeline / Bridge 三处**用户路径**的 `inject_event` 调用（后台 tick 路径保留不动）
- 用户聊天**唯一入口**：`RuntimeCore.process(event)`；阶段 1 内部走 `_on_event` exactly once
- OrchestratorRuntimeBridge 简化为纯 process() 路径
- RuntimePipeline runtime-first 模式接通

### 文件变更清单

#### T4.0.2-1：`src/orchestrator_runtime_bridge.py`（简化为纯 process 路径）

| 变更项 | 位置 | 详细说明 |
|---|---|---|
| 1. 删除 `inject_event` 鸭子分支（P0-修改3） | `_try_runtime()` 方法体 | **删除**原 `has_inject` 分支（类型 B 的 inject_event 调用）；**删除**类型判断逻辑；**改为纯 process-first 路径**：<br><br>```python<br>def _try_runtime(self, user_message: str) -> _Opt[str]:<br>    rt = self._runtime<br>    if rt is None:<br>        self._last_runtime_error = "no_runtime_core"<br>        return None<br>    # 双保险开关<br>    if os.environ.get("YUYI_RUNTIME_PROCESS_DISABLED") == "1":<br>        self._last_runtime_error = "process_disabled_env"<br>        return None<br>    config_runtime = getattr(self._config or {}, "runtime", {}) if isinstance(self._config, dict) else {}<br>    if not config_runtime.get("process_enabled", True):<br>        self._last_runtime_error = "process_disabled_config"<br>        return None<br>    has_process = callable(getattr(rt, "process", None))<br>    if not has_process:<br>        self._last_runtime_error = f"no_process_method:{type(rt).__name__}"<br>        return None<br>    try:<br>        from src.runtime.events import Event<br>        event = Event(<br>            type="user_input",<br>            source="user",<br>            payload={<br>                "text": user_message,<br>                "content": user_message,<br>                "user_id": getattr(self._orch, "target_user_id", None) or "default",<br>            },<br>        )<br>        # ===== P0-修改3：这里不调 inject_event！_on_event 由 process 内部阶段 1 调 =====<br>        ctx = rt.process(event)<br>        self._last_ctx = ctx<br>        self._last_runtime_error = None<br>        final = getattr(ctx, "_final_reply", None) or getattr(ctx, "finalized_reply", None)<br>        if final and isinstance(final, str) and final.strip():<br>            self._last_runtime_mode = "full_process"<br>            return final<br>        # Runtime 跑了但空回复 → state_only 模式（状态更新了但回复需要 legacy）<br>        self._last_runtime_mode = "state_only"<br>        self._last_runtime_error = "empty_final_reply_after_process"<br>        return None<br>    except Exception as exc:<br>        self._last_runtime_error = f"process_failed:{exc!r}"<br>        logger.warning("[OrchestratorRuntimeBridge] Runtime.process() 失败: %s, fallback legacy", exc)<br>        return None<br>```<br>**校验**：`_try_runtime` 方法体内 grep `inject_event` → 0 命中。 |
| 2. `handle_message()` source 标记逻辑（修复被 legacy_generate 覆盖问题） | 方法体 | 保存 bridge_source，在调用 legacy_generate 之后根据需要恢复：<br>```python<br>bridge_source = None<br>runtime_reply = self._try_runtime(user_message)<br>if runtime_reply is not None:<br>    self._orch._last_reply_source = "runtime"<br>    self._orch._runtime_call_count = getattr(self._orch, "_runtime_call_count", 0) + 1<br>    return runtime_reply<br># runtime 没产出回复 → 确定 bridge_source<br>if self._last_runtime_mode == "state_only":<br>    bridge_source = "runtime_state+legacy"<br>    self._orch._runtime_state_count = getattr(self._orch, "_runtime_state_count", 0) + 1<br>else:<br>    bridge_source = "legacy"<br>    self._orch._legacy_call_count = getattr(self._orch, "_legacy_call_count", 0) + 1<br># 调 legacy（内部会覆盖 _last_reply_source）<br>legacy_reply = self._legacy_generate(user_message, ...)<br># 恢复 source（legacy_generate 内部会写 "legacy"）<br>if bridge_source and self._orch:<br>    self._orch._last_reply_source = bridge_source<br>return legacy_reply<br>``` |

#### T4.0.2-2：`api_server.py`（删除两处用户路径 inject_event，P0-修改3）

| 变更项 | 位置 | 详细说明 |
|---|---|---|
| 1. 删除第一处（RuntimePipeline 入口前） | `_process_via_pipeline()` 函数体开头 | **删除**任何形式的：<br>`_runtime_bridge.on_user_message(user_id, session_id, effective_user_message)`<br>`_runtime_bridge.get_runtime_core().inject_event("user.input", ...)`<br>等用户消息事件注入。**不删除其他业务逻辑**。 |
| 2. 删除第二处（orchestrator.process fallback 分支内） | `orchestrator.process()` 分支内 | 同形式删除用户消息 inject_event 调用。 |
| **校验** | 完成后全局搜索 | `grep -n "inject_event.*user" api_server.py` → 0 行命中（用户路径零 inject）；后台 tick 路径 `inject_event.*system.tick` 若存在则保留（不删）。 |

#### T4.0.2-3：`src/runtime/runtime_pipeline.py`（runtime-first 改造；用户路径严禁 inject_event）

| 变更项 | 位置 | 详细说明 |
|---|---|---|
| 1. 删除入口 inject_event（P0-修改3） | `run(input_data)` 最开头 | **删除**原计划中的 inject_event 调用块。**用户消息路径不得在 process() 外层发生任何状态 mutate**。 |
| 2. Step 5 改为 runtime-first | `run()` 原 Step 5（直接 orchestrator.process） | ```python<br># Step 5: Runtime-first（P0-修改3：唯一入口 process）<br>final_reply = self._try_runtime_process(<br>    effective_user_message,<br>    user_id=user_id,<br>    session_id=session_id,<br>)<br>if not final_reply:<br>    # fallback: orchestrator.process（旧链路）<br>    final_reply = self._orchestrator.process(effective_user_message, ...) if self._orchestrator else ""<br>if not final_reply:<br>    final_reply = "嗯……让我想想。"  # 兜底，与现有一致<br>``` |
| 3. 新增 `_try_runtime_process()` 方法 | `run()` 之后新增 | ```python<br>def _try_runtime_process(self, user_message, user_id="default", session_id=None):<br>    # 开关双保险<br>    if os.environ.get("YUYI_RUNTIME_PROCESS_DISABLED") == "1":<br>        return None<br>    try:<br>        from src.runtime.runtime_bridge import get_runtime_bridge<br>        rb = get_runtime_bridge()<br>        if rb is None or not rb.is_initialized():<br>            return None<br>        # ===== 走 bridge_process（T4.0.2-4 实现） =====<br>        return rb.bridge_process(user_message, user_id, session_id)<br>    except Exception as exc:<br>        logger.warning("[RuntimePipeline] _try_runtime_process 失败: %s", exc)<br>        return None<br>``` |

#### T4.0.2-4：`src/runtime/runtime_bridge.py`（新增 bridge_process；不调 inject）

| 变更项 | 位置 | 详细说明 |
|---|---|---|
| 1. 新增 `bridge_process()` 方法 | 类内，`inject_event()` 之后 | ```python<br>def bridge_process(self, user_message: str, user_id: str, session_id: _Opt[str] = None) -> _Opt[str]:<br>    """用户聊天统一入口。<br>    <br>    P0-修改3：内部**绝不**调用 inject_event。<br>    _on_event 由 RuntimeCore.process() → 阶段 1 → _stage_01_receive_event exactly once 触发。<br>    """<br>    if not self.is_initialized():<br>        return None<br>    rt = self.get_runtime_core()<br>    if rt is None or not callable(getattr(rt, "process", None)):<br>        return None<br>    from src.runtime.events import Event<br>    event = Event(<br>        type="user_input",<br>        source="user",<br>        payload={<br>            "text": user_message, "content": user_message,<br>            "user_id": user_id, "session_id": session_id,<br>        },<br>    )<br>    try:<br>        ctx = rt.process(event)  # 内部阶段 1 会调 _on_event 一次<br>        final = getattr(ctx, "_final_reply", None) or getattr(ctx, "finalized_reply", None)<br>        return final if (final and isinstance(final, str) and final.strip()) else None<br>    except Exception as exc:<br>        logger.warning("[RuntimeBridge] bridge_process 失败: %s", exc)<br>        return None<br>```<br>**校验**：方法体内 grep `inject_event` → 0 命中。 |

### 测试文件

| 测试文件 | 覆盖要求 | 断言数 ≥ |
|---|---|---|
| `tests/test_phase_4_0_2_process_connected.py` | ① Bridge._try_runtime 内部对 mock rt.inject_event 调用次数 = 0（P0-修改3 核心）；② RuntimePipeline.run() 对 RuntimeCore.inject_event("user.input") 调用次数 = 0；③ runtime-first 模式：process 有回复时不 fallback orchestrator；④ source/mode 三态（runtime / runtime_state+legacy / legacy）正确 | 5 |
| `tests/test_phase_4_0_2_no_duplicate_event.py`（★核心计数，P0-修改3） | 连续 3 条用户消息 → RuntimeCore 上 `_on_event("user.input", ...)` 计数 **exactly 3**（不多不少）；同时 RuntimeCore.inject_event("user.input") 计数 = 0（用户路径零 inject） | 2 |

### Gate

- [ ] `python -m pytest tests/test_phase_4_0_2* -v` 100% 通过
- [ ] `python -m pytest tests/ -x --ignore=tests/test_phase_3_*` 全通过
- [ ] 手动 smoke：启动后发 3 条消息 → 日志中 `_on_event.*user.input` 精确出现 3 次；`inject_event.*user.input` 出现 0 次（grep 验证）
- [ ] BLOCKER-1~8（含 BLOCKER-8 runtime.py 零 Bridge）**全部**满足

---

## 4.0.3 Response Context Pipeline（identity_context 独立 Phase，P1-修改6）

### 目标（REVIEW P1-修改6 独立）
与 Runtime 接通解耦，独立处理：
1. 修复 `engine.generate(identity_context=...)` TypeError；
2. identity_context 真的注入 system prompt；
3. ResponseAdapter 上下文链（history / chat_memories / life_events / identity_context）完整传递。

**为何独立**：Runtime 接通失败时，能清晰区分是「Runtime 逻辑问题」还是「Prompt/Identity 注入问题」，故障定位分层。

### 文件变更清单

#### T4.0.3-1：`src/engine.py`（★核心：签名扩展 + 两处 prompt builder 消费）

| 变更项 | 位置 | 详细说明 |
|---|---|---|
| 1. `generate()` 签名扩展 | 方法定义行 | **原**：`def generate(self, prompt, history=None, context_prompt_blocks=None, experience_context=None, **kwargs)`<br>**新**：`def generate(self, prompt, history=None, context_prompt_blocks=None, experience_context=None, identity_context=None, **kwargs)`<br>（identity_context 放在最后一个显式参数，默认 None） |
| 2. `_build_messages_original()` 签名 + 消费 identity_context | 方法定义行 + system prompt 末尾 | 签名同样加 `identity_context=None`；在 system prompt 拼接的最末尾（人格段之后，user prompt 之前）追加：<br>```python<br>if identity_context and isinstance(identity_context, str) and identity_context.strip():<br>    identity_block = (<br>        "\n\n=== 身份约束 ===\n"<br>        f"{identity_context.strip()}\n"<br>        "=== /身份约束 ===\n"<br>    )<br>    for m in messages:<br>        if m.get("role") == "system":<br>            m["content"] = m.get("content", "") + identity_block<br>            break<br>    else:<br>        messages.insert(0, {"role": "system", "content": identity_block.strip()})<br>``` |
| 3. `_build_messages_opt()` 签名 + 同上逻辑 | 对应方法 | 与 T4.0.3-1#2 完全相同（两处 build_messages 都要改）。 |
| 4. `_mock_response()` / 其他 generate 变体 | 按需 | 若有显式签名也要同步加 `identity_context=None`；**kwargs 兜底的不改。** |

#### T4.0.3-2：`src/orchestrator.py` — `legacy_generate()` 与 `generate_initiative()`（补传三参数）

| 变更项 | 位置 | 详细说明 |
|---|---|---|
| 1. `legacy_generate()` engine.generate 调用点 | 方法内 | ```python<br>identity_ctx = None<br>exp_ctx = None<br>ctx_blocks = None<br>try:<br>    if hasattr(self, "_get_identity_context"):<br>        identity_ctx = self._get_identity_context()<br>    if hasattr(self, "self_model_store") and hasattr(self.self_model_store, "get_experience_context"):<br>        exp_ctx = self.self_model_store.get_experience_context()<br>    if hasattr(self, "_build_context_prompt_blocks"):<br>        ctx_blocks = self._build_context_prompt_blocks()<br>except Exception as exc:<br>    logger.debug("[Orchestrator.legacy_generate] 上下文获取失败（降级）: %s", exc)<br>reply = engine.generate(<br>    prompt=user_message,<br>    history=chat_history,<br>    context_prompt_blocks=ctx_blocks,<br>    experience_context=exp_ctx,<br>    identity_context=identity_ctx,  # 4.0.3 新增<br>)<br>``` |
| 2. `generate_initiative()` engine.generate 调用点 | 同上 | 同样补传 identity_context / experience_context / context_prompt_blocks（参数来源同上，try/except 包一下）。 |

#### T4.0.3-3：`src/runtime/adapters/impl/response_adapter_impl.py`（ResponseAdapter 补传）

| 变更项 | 位置 | 详细说明 |
|---|---|---|
| 1. ResponseRequest 扩展（若需要） | 文件顶部 dataclass | 在 `ResponseRequest` 中追加 `identity_context: Optional[str] = None`（若已有则跳过）。 |
| 2. generate() 内 engine.generate 调用 | 方法内 | ```python<br>reply = engine.generate(<br>    prompt=request.user_message,<br>    history=request.history,<br>    context_prompt_blocks=request.context_prompt_blocks,<br>    experience_context=request.experience_context,<br>    identity_context=getattr(request, "identity_context", None),  # 4.0.3 新增<br>)<br>``` |

#### T4.0.3-4：`src/runtime/runtime_core.py` — `_stage_14_response_generation`（同步传 identity）

| 变更项 | 位置 | 详细说明 |
|---|---|---|
| 1. 补传 identity_context 到 engine.generate | `_stage_14_response_generation` 降级路径中 | 取 `ctx.identity_context_text`（若有）传入 engine.generate；若无传 None。（此处仅当 response_adapter 缺失时走降级 engine 路径。） |

### 测试文件

| 测试文件 | 覆盖要求 | 断言数 ≥ |
|---|---|---|
| `tests/test_phase_4_0_3_identity_context_engine.py` | ① engine.generate 签名含 identity_context（inspect.signature 验证）；② identity_context 非空时，system prompt 末尾含 `=== 身份约束 ===` 字符串块；③ identity_context=None 时 prompt 结构不变；④ Orchestrator.legacy_generate 不再 TypeError；⑤ generate_initiative 不再 TypeError | 5 |

### Gate

- [ ] `python -m pytest tests/test_phase_4_0_3* -v` 100% 通过
- [ ] `python -m pytest tests/test_orchestrator_initiative.py tests/test_legacy_generate.py -v` 无新失败
- [ ] `python -m pytest tests/ -x --ignore=tests/test_phase_3_*` 全通过
- [ ] BLOCKER-1~8 全部满足

---

## 4.0.4 Memory 事务化（跨领域 Storage Guard + SafeVectorAdapter，P1-修改4/5）

### 目标
不改 `src/memory/**` 的前提下：
1. 原子写保护 memory.json → 锁放 `src/common/storage/`（跨领域复用，P1-修改4）
2. VectorMemory 不被污染 → 升级为 `SafeVectorMemoryAdapter`（所有调用方必经，P1-修改5）
3. MemoryCreatedEvent 仅在 memory_store.add 成功后发布

### 文件变更清单

#### T4.0.4-1：`src/common/storage/atomic_write.py`（★新增，P1-修改4：跨领域存储层）

| 变更项 | 说明 |
|---|---|
| 文件内容 | ```python<br>"""跨领域原子写守卫（文件级锁）。<br><br>位置：src/common/storage/ （不是 src/runtime/utils，P1-修改4）<br>可被 MemoryStore / Snapshot / Audit / Config 等任何需要 load→modify→save 原子性的模块复用。<br>"""<br>import threading<br>import os<br>from typing import Any, Dict, Optional<br>from contextlib import contextmanager<br><br>_LOCKS: Dict[int, threading.RLock] = {}<br>_LOCKS_GUARD = threading.RLock()<br><br><br>def _get_lock_for_id(lock_id: int) -> threading.RLock:<br>    """按对象 id / 文件 inode 分配全局唯一 RLock。"""<br>    with _LOCKS_GUARD:<br>        if lock_id not in _LOCKS:<br>            _LOCKS[lock_id] = threading.RLock()<br>        return _LOCKS[lock_id]<br><br><br>@contextmanager<br>def file_atomic_write_lock(target: Any, timeout: float = 30.0):<br>    """包裹 load → modify → save 完整序列的文件级原子写锁。<br><br>    Args:<br>        target: 被锁对象（MemoryStore / 文件路径字符串 / 任意 Python 对象）。<br>                若为 str（文件路径），用 os.path.abspath 做 key；否则用 id(target)。<br>        timeout: 获取锁超时秒数（默认 30s，疑似死锁抛 RuntimeError）。<br><br>    Usage（MemoryStore 写入）:<br>        with file_atomic_write_lock(self.memory_store):<br>            result = self.memory_store.add(record)<br>            if result is not None:<br>                vector_memory.add_memory(record)  # 仅在成功后<br>                event_bus.publish(MemoryCreatedEvent(...))<br><br>    Usage（文件路径级锁）:<br>        with file_atomic_write_lock("/path/to/snapshot.json"):<br>            data = json.load(open(...))<br>            data["x"] = y<br>            json.dump(data, open(...))<br>    """<br>    if target is None:<br>        yield None<br>        return<br>    if isinstance(target, str):<br>        key = hash(os.path.abspath(target))<br>    else:<br>        key = id(target)<br>    lock = _get_lock_for_id(key)<br>    acquired = lock.acquire(timeout=timeout)<br>    if not acquired:<br>        raise RuntimeError(f"file_atomic_write_lock: 获取锁超时 ({timeout}s)，疑似死锁")<br>    try:<br>        yield lock<br>    finally:<br>        try:<br>            lock.release()<br>        except RuntimeError:<br>            # 释放未持有锁时忽略（上下文意外中断的边缘场景）<br>            pass<br>``` |
| 1. 包初始化 | `src/common/__init__.py` 与 `src/common/storage/__init__.py` | 若不存在则新建空文件；`src/common/storage/__init__.py` 追加：<br>`from src.common.storage.atomic_write import file_atomic_write_lock`<br>`__all__ = ["file_atomic_write_lock"]` |

#### T4.0.4-2：`src/infrastructure/vector/safe_vector_adapter.py`（★新增 P1-修改5：SafeVectorMemoryAdapter）

| 变更项 | 说明 |
|---|---|
| 文件内容 | ```python<br>"""SafeVectorMemoryAdapter — 所有 VectorMemory 调用的唯一安全包装。<br><br>P1-修改5：不要只在 orchestrator 写一个 _safe_add_vector_memory 函数。<br>所有对 VectorMemory 的 add_memory / add_memories / delete 等调用必须经过本 Adapter。<br><br>结构：<br>    VectorMemory  ←  原始实现（不改 src/memory）<br>          ↑<br>    SafeVectorMemoryAdapter  ←  前置校验：role 白名单 / 内容长度 / 污染标签<br>          ↑<br>    所有调用方（Orchestrator / MemoryAdapter / Growth / Admin ...）<br>"""<br>import logging<br>from typing import Any, Iterable, List, Optional<br><br>logger = logging.getLogger(__name__)<br><br># ===== 安全常量（可通过构造函数参数覆盖） =====<br>DEFAULT_MAX_CONTENT_LENGTH = 4000  # 与 PollutionGuard 对齐<br>DEFAULT_ALLOWED_ROLES = {"user", "assistant", "system", "memory", "thought"}  # role 白名单<br>POLLUTION_TAGS = ["<｜end▁of▁sentence｜>", "<s>", "</s>", "<END>", "MEMORY_INJECT:", "SYSTEM_PROMPT_OVERRIDE:"]<br><br><br>class SafeVectorMemoryAdapter:<br>    """VectorMemory 的安全包装。<br><br>    提供前置校验：<br>    1. content 超长（> max_content_length）→ 拒绝并 warn<br>    2. role 不在白名单 → 拒绝<br>    3. 内容含注入标签（POLLUTION_TAGS） → 拒绝<br>    4. content 非字符串 / 空字符串 → 拒绝<br><br>    通过校验后**才** delegate 到内部 VectorMemory 实例。<br>    """<br><br>    def __init__(<br>        self,<br>        vector_memory: Any,<br>        *,<br>        max_content_length: int = DEFAULT_MAX_CONTENT_LENGTH,<br>        allowed_roles: Optional[set] = None,<br>    ) -> None:<br>        self._vm = vector_memory<br>        self._max_len = max_content_length<br>        self._allowed_roles = allowed_roles or set(DEFAULT_ALLOWED_ROLES)<br>        # 审计计数<br>        self.accepted_count: int = 0<br>        self.rejected_count: int = 0<br>        self.last_rejection_reason: Optional[str] = None<br><br>    # ===== 内部校验 =====<br>    def _validate_record(self, record: Any) -> Optional[str]:<br>        """返回 None = 通过；返回 str = 拒绝理由。"""<br>        content = getattr(record, "content", None)<br>        if content is None:<br>            return "content is None"<br>        if not isinstance(content, str):<br>            try: content = str(content)<br>            except Exception: return "content not stringifiable"<br>        if not content.strip():<br>            return "content empty after strip"<br>        if len(content) > self._max_len:<br>            return f"content too long ({len(content)} > {self._max_len})"<br>        role = getattr(record, "role", None)<br>        if role is not None and str(role) not in self._allowed_roles:<br>            return f"role '{role}' not in whitelist {sorted(self._allowed_roles)}"<br>        for tag in POLLUTION_TAGS:<br>            if tag in content:<br>                return f"content contains pollution tag '{tag}'"<br>        return None<br><br>    # ===== 对外 API（与 VectorMemory 对齐） =====<br>    def add_memory(self, record: Any) -> bool:<br>        """安全地添加单条记忆向量。"""<br>        reason = self._validate_record(record)<br>        if reason is not None:<br>            self.rejected_count += 1<br>            self.last_rejection_reason = reason<br>            logger.warning(<br>                "[SafeVectorMemoryAdapter] 拒绝 add_memory（%s）。record_id=%s",<br>                reason, getattr(record, "id", None),<br>            )<br>            return False<br>        try:<br>            if self._vm is None or not callable(getattr(self._vm, "add_memory", None)):<br>                return False<br>            result = self._vm.add_memory(record)<br>            self.accepted_count += 1<br>            return bool(result) if result is not None else True<br>        except Exception as exc:<br>            self.rejected_count += 1<br>            self.last_rejection_reason = f"vm_exception:{exc!r}"<br>            logger.warning("[SafeVectorMemoryAdapter] add_memory 代理异常: %s", exc)<br>            return False<br><br>    def add_memories(self, records: Iterable[Any]) -> int:<br>        """安全批量添加；返回成功条数。"""<br>        success = 0<br>        for rec in records:<br>            if self.add_memory(rec):<br>                success += 1<br>        return success<br><br>    def search(self, *args, **kwargs):<br>        """search 只读，无校验，直接代理。"""<br>        if self._vm is None: return []<br>        fn = getattr(self._vm, "search", None)<br>        return fn(*args, **kwargs) if callable(fn) else []<br><br>    # 其他 VectorMemory 方法（get_recent / delete / clear）按需透明代理，无校验<br>    def __getattr__(self, name: str):<br>        if name.startswith("_") or self._vm is None:<br>            raise AttributeError(name)<br>        return getattr(self._vm, name)<br><br>    # ===== 审计 getter =====<br>    def get_safety_stats(self) -> dict:<br>        return {<br>            "accepted": self.accepted_count,<br>            "rejected": self.rejected_count,<br>            "last_rejection_reason": self.last_rejection_reason,<br>        }<br>``` |
| 包初始化 | `src/infrastructure/__init__.py` 和 `src/infrastructure/vector/__init__.py` | 不存在则新建；vector/__init__.py 追加 `from src.infrastructure.vector.safe_vector_adapter import SafeVectorMemoryAdapter` + `__all__` 导出。 |

#### T4.0.4-3：`src/orchestrator.py` Step 10（MEMORY TRANSACTION 重写，使用新位置 + SafeAdapter）

| 变更项 | 位置 | 详细说明 |
|---|---|---|
| 1. imports 替换 | 文件顶部 | **删除**（若存在）`from src.runtime.utils.atomic_memory_write_guard import memory_write_lock`<br>**新增**：<br>`from src.common.storage.atomic_write import file_atomic_write_lock`<br>`from src.infrastructure.vector.safe_vector_adapter import SafeVectorMemoryAdapter` |
| 2. `__init__` 注入 SafeVectorMemoryAdapter | `Orchestrator.__init__` 中 vector_memory 初始化后 | ```python<br># P1-修改5：所有 VectorMemory 调用必经 SafeVectorMemoryAdapter<br>raw_vm = getattr(self, "vector_memory", None)<br>if raw_vm is not None and not isinstance(raw_vm, SafeVectorMemoryAdapter):<br>    self.vector_memory = SafeVectorMemoryAdapter(raw_vm)<br>``` |
| 3. 重写 Step 10 记忆写入序列（★核心事务） | `process()` 方法 Step 10 | ```python<br># ===== Step 10: Persist user message (MEMORY TRANSACTION，P1-修改4/5) =====<br>memory_result = None<br>try:<br>    # P1-修改4：跨领域 file_atomic_write_lock（不是 runtime.utils）<br>    with file_atomic_write_lock(self.memory_store):<br>        # 1) 规整内容（二次保险）<br>        safe_content = self._normalize_memory_content(user_message)<br>        if safe_content != getattr(memory_record, "content", ""):<br>            try: memory_record = dataclasses.replace(memory_record, content=safe_content)<br>            except Exception: pass<br>        # 2) MemoryStore 写入（PollutionGuard 在此内部执行，失败返回 None）<br>        memory_result = self.memory_store.add(memory_record)<br>        if memory_result is not None:<br>            # 3) 成功 → SafeVectorMemoryAdapter.add_memory（内置校验，P1-修改5）<br>            try:<br>                self.vector_memory.add_memory(memory_record)<br>            except Exception as ve:<br>                logger.warning("[Orchestrator] SafeVectorMemoryAdapter.add_memory 失败: %s", ve)<br>            # 4) 成功 → MemoryCreatedEvent（仅在 add 成功后发布！）<br>            try:<br>                if hasattr(self, "_event_bus") and self._event_bus and callable(getattr(self._event_bus, "publish", None)):<br>                    self._event_bus.publish(MemoryCreatedEvent(memory_id=memory_record.id, record=memory_record))<br>            except Exception as ee:<br>                logger.warning("[Orchestrator] MemoryCreatedEvent 发布失败: %s", ee)<br>            # 5) 审计日志（成功）<br>            self._last_memory_write_success = True<br>            try:<br>                self._record_audit_log("memory.created", status="success", detail={"memory_id": memory_record.id, "length": len(memory_record.content)})<br>            except Exception: pass<br>        else:<br>            self._last_memory_write_success = False<br>            logger.warning("[Orchestrator] MemoryStore.add 返回 None，记忆被拒绝。id=%s len=%d", getattr(memory_record, "id", None), len(getattr(memory_record, "content", "") or ""))<br>            try:<br>                self._record_audit_log("memory.created", status="rejected", detail={"reason": "pollution_guard_or_duplicate", "memory_id": getattr(memory_record, "id", None)})<br>            except Exception: pass<br>except Exception as outer:<br>    self._last_memory_write_success = False<br>    logger.exception("[Orchestrator] Step 10 Memory Transaction 异常: %s", outer)<br>``` |
| 4. 诊断属性 | `__init__` 末尾 | `self._last_memory_write_success: Optional[bool] = None` |

#### T4.0.4-4：`src/runtime/adapters/memory_adapter.py`（store_experience 使用锁 + SafeAdapter）

| 变更项 | 位置 | 详细说明 |
|---|---|---|
| 1. imports + 锁包裹 | `store_experience()` 方法 | ```python<br>from src.common.storage.atomic_write import file_atomic_write_lock<br>...<br>with file_atomic_write_lock(self._store):<br>    result = self._store.add(memory_entry)<br>    if result is None:<br>        logger.warning("[MemoryAdapter] store_experience 被 PollutionGuard 拒绝: id=%s", memory_entry.id)<br>        return<br># 仅成功后追加向量（若持有 vector adapter）<br>safe_vm = getattr(self, "_safe_vector_memory", None)<br>if safe_vm is not None and callable(getattr(safe_vm, "add_memory", None)):<br>    try: safe_vm.add_memory(memory_entry)<br>    except Exception as ve: logger.warning("[MemoryAdapter] SafeVectorAdapter.add 失败: %s", ve)<br>``` |

### 测试文件

| 测试文件 | 覆盖要求 | 断言数 ≥ |
|---|---|---|
| `tests/test_phase_4_0_4_atomic_write.py` | ① file_atomic_write_lock 上下文正常 with / 不抛错；② None target 安全 yield；③ 并发 100 线程同一 store 写入，临界区串行化（通过计数器验证）；④ str 路径锁 vs id(target) 锁不冲突 | 4 |
| `tests/test_phase_4_0_4_safe_vector_adapter.py`（★P1-修改5 核心） | ① 超长 content 被 SafeVectorMemoryAdapter.add_memory 拒绝（返回 False，rejected_count++）；② 空 content / None content 拒绝；③ 含污染标签（`<｜end▁of▁sentence｜>`）拒绝；④ 通过校验的记录 delegate 到 raw_vm.add_memory exactly once；⑤ get_safety_stats() 计数正确 | 5 |
| `tests/test_phase_4_0_4_memory_transaction.py` | ① memory_store.add 返回 None → vector_memory.add_memory 调用次数 = 0；② memory_store.add 成功 → add_memory 调用次数 = 1 且 MemoryCreatedEvent 发布次数 = 1；③ PollutionGuard 拒绝时仍不中断聊天（正常返回回复） | 3 |

### Gate

- [ ] `python -m pytest tests/test_phase_4_0_4* -v` 100% 通过
- [ ] `python -m pytest tests/test_memory_normalizer.py tests/test_memory_store*.py -v` 无新失败
- [ ] `python -m pytest tests/ -x --ignore=tests/test_phase_3_*` 全通过
- [ ] BLOCKER-1~8 全部满足
- [ ] 手动 smoke：发一条 >4000 字符消息 → 日志显示 `MemoryStore.add 返回 None` + `SafeVectorMemoryAdapter.*拒绝`；memory.json 无该条；向量库查询不到该条

---

## 4.0.5 Runtime 诊断面板（可观测性）

### 目标
`/runtime/overview` 返回 12+ 个诊断字段，回答「羽依到底有没有活」。

### 文件变更清单

#### T4.0.5-1：`src/admin/runtime_provider.py`

| 变更项 | 位置 | 详细说明 |
|---|---|---|
| 1. orchestrator ref 注入 | `__init__` + setter | `self._orchestrator_ref = None`<br>`def set_orchestrator_ref(self, orch) -> None: self._orchestrator_ref = orch` |
| 2. stats getter 新增 | 类内方法 | 取 orch 的 8 个诊断 getter（last_reply_source / last_runtime_mode / last_runtime_error / last_legacy_reason / runtime_call_count / runtime_state_count / legacy_call_count / runtime_status） |
| 3. `get_runtime_overview()` 返回 dict 扩展 | 方法末尾 runtime 段追加 | 新增 key：`orchestrator_runtime_status`、`last_reply_source`、`last_runtime_mode`、`last_runtime_error`、`last_legacy_reason`、`runtime_call_count`、`runtime_state_count`、`legacy_call_count`、`runtime_mode`（派生）、`last_event`、`last_phase`、`memory_write_success`、`growth_triggered`（共 13 项） |

#### T4.0.5-2：`src/control/api/routes.py` + `src/admin/dashboard/runtime_router.py`

| 变更项 | 说明 |
|---|---|
| 确保 `_to_json_safe(data)` 不丢失新增字段（已存在则无需改）；`/runtime/status` 最小扩展 `last_runtime_error` + `orchestrator_runtime_status` |

#### T4.0.5-3：`api_server.py` 启动段

| 变更项 | 位置 | 详细说明 |
|---|---|---|
| orch ref 注入 provider | `_orchestrator = Orchestrator(...)` 之后 | 调用 `get_runtime_provider().set_orchestrator_ref(_orchestrator)`，try/except 包一下，失败不启动阻断。 |

### 测试文件

| 测试文件 | 覆盖要求 | 断言数 ≥ |
|---|---|---|
| `tests/test_phase_4_0_5_diagnostic_fields.py` | Flask test_client GET /runtime/overview → JSON 中存在至少 12 个新增键；一次聊天后 runtime/legacy 其一计数 >0；orch=None 时返回 200（不 500） | 3 |

### Gate

- [ ] `python -m pytest tests/test_phase_4_0_5* -v` 100% 通过
- [ ] `python -m pytest tests/test_server_api_gateway.py tests/test_admin_api_*.py -v` 无新失败
- [ ] 手动 smoke：启动 → `curl /api/v1/runtime/overview` → 12+ 字段存在且合理
- [ ] `python -m pytest tests/ -x --ignore=tests/test_phase_3_*` 全通过
- [ ] BLOCKER-1~8 全部满足

---

## 4.0.6 全链路 E2E 测试

### 目标
端到端验证 4.0.1–4.0.5 正确集成；无 BLOCKER 失败。

### 文件变更（最小，新增测试文件为主）

#### T4.0.6-1：`tests/test_phase_4_0_6_e2e.py`（★新增核心）

| 测试类 / 用例 | 验证内容 |
|---|---|
| `TestE2ERuntimeProcessPath` | mock LLM 启动最小 Flask app，POST `/v1/chat/completions` → ① RuntimeCore.process() 被调用 exactly once（mock 计数）；② 返回 200 + 非空 choices[0].message.content；③ last_reply_source = "runtime"，last_runtime_mode = "full_process" |
| `TestE2EFallbackPath` | process 返回空 → 最终响应仍非空（fallback）；source ∈ {"runtime_state+legacy", "legacy"} |
| `TestE2ESwitchesOff` | `config.runtime.enabled=false` + `YUYI_RUNTIME_PROCESS_DISABLED=1` → process() 调用次数 = 0；聊天仍正常返回 |
| `TestE2EIdentityContextInjected` | orch._get_identity_context 返回固定字符串 → engine._last_messages 的 system prompt 末尾含 `=== 身份约束 ===` 块 |
| `TestE2EMemoryTransaction` | 构造 PollutionGuard 会拒绝的超长消息 → ① memory_store.add 返回 None 后 vector_memory.add_memory 未调用；② MemoryCreatedEvent 未发布；③ 聊天仍正常返回（不中断） |
| `TestE2EDiagnosticOverviewAfterChat` | 一次聊天后 GET /runtime/overview → runtime_mode ∈ {full, state_only, legacy}，*_call_count ≥ 1 |
| `TestE2EConcurrentStability` | 20 线程并发聊天（ThreadPoolExecutor）→ 200 比例 100%（无挂起无超时） |

### Gate（Sign-off 级别，任何一条不通过即整体回滚）

- [ ] `python -m pytest tests/test_phase_4_0_6_e2e.py -v` 100% 通过
- [ ] `python -m pytest tests/ -x --ignore=tests/test_phase_3_*` 100% 通过（或失败数 ≤ 4 且非新增）
- [ ] 手动 smoke：真实启动 api_server → curl POST 聊天 → 返回非空 + `/runtime/overview` 正确反映本次调用
- [ ] BLOCKER-1~8（check_list.md §0）**全部**通过
- [ ] check_list.md 中 4.0.1-F 至 4.0.5-F 全部勾选通过
- [ ] 性能基准：关闭所有 Runtime 开关时，p95 聊天延迟相比 Phase 4.0 前增幅 < 10%

---

## 实施注意事项（所有阶段通用）

1. **严格锁顺序**：RuntimeCore._lock 与 file_atomic_write_lock 同时存在时，先取 RuntimeCore._lock 再取 file_atomic_write_lock，避免死锁。
2. **log 级别**：新增异常场景一律 `logger.warning`（不是 error），避免生产告警误触发。
3. **向后兼容**：任何新增参数必须带默认 `None` / `False`；任何新增方法不得影响现有 authority getter 返回值语义。
4. **禁止硬编码**：不要硬编码 4000 字符等 magic number；用 `self.MAX_MEMORY_CONTENT_LENGTH` / `SafeVectorMemoryAdapter(MAX=...)` 常量。
5. **单元测试**：用户规则——每一个 4.0.X 都必须有对应的 `test_phase_4_0_X*.py` 测试文件；Gate 通过后方可进入下一阶段。
6. **硬约束保护**：任何阶段不得修改 `src/memory/**`、`src/growth/**`、`src/personality/**`、`src/self_model/**`、`src/control/**`、`src/runtime/policy/**`（新增的 `src/common/` 和 `src/infrastructure/` 不在禁止范围内）。
7. **分支策略**：所有修改在分支 `phase-4-0-runtime-integration` 上进行；每个 Phase 通过 Gate 后可做一个 commit；不得直推 main。
