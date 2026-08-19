"""Phase 4.0.1: LifecycleExecutor —— 17 阶段调度唯一实现点。

SPEC v0.2 §3.1-1 / §2.1:
- 从 runtime_core.py / runtime.py 中**完全抽离**阶段调度逻辑，形成独立文件
- RuntimeCore 组合持有 self._lifecycle = LifecycleExecutor()
- RuntimeCore.process() 改为**单调用**：`ctx = self._lifecycle.execute(self, event, ctx)`
- 未来新增 Stage 17/18/19（Dream / Reflection / Self Goal）**只改本文件**，不污染 RuntimeCore

关键契约（本文件 = 唯一权威来源，冻结后不得随意更改）：
  C-1 (P0-修改3): Stage 1 RECEIVE_EVENT 内部**必须且仅一次**调用 core._on_event(...)
      禁止在 process() 之前/之外再调 inject_event() 给用户消息路径
  C-2 (风险点1): 17 阶段 → 方法名映射使用统一命名约定，LifecycleExecutor
      按此规则分派；若 RuntimeCore 缺失对应方法，视为 no-op（不抛错）
  C-3: 单个阶段异常 fail-soft：记录到 ctx._phase_errors + logger.warning，
      不中断下一阶段执行
  C-4: Stage 7-13（Perception / SelfModel 系列）默认 no-op，对应方法不存在时静默跳过

阶段 → 方法名映射（FROZEN CONTRACT，17 个 = 17 断言覆盖）：
  0. CONTROL_CHECK            → _stage_00_control_check
  1. RECEIVE_EVENT            → _stage_01_receive_event   + (C-1) 后调用 core._on_event
  2. MEMORY_RETRIEVAL         → _stage_02_memory_retrieval
  3. EMOTION_UPDATE           → _stage_03_emotion_update
  4. GROWTH_EVALUATION        → _stage_04_growth_evaluation
  5. PERSONALITY_UPDATE       → _stage_05_personality_update
  6. PERSONALITY_CONTEXT_BUILD→ _stage_06_personality_context_build
  7. PERCEPTION_OBSERVATION   → _stage_07_perception_observation   (no-op 默认)
  8. PERCEPTION_ANALYSIS      → _stage_08_perception_analysis      (no-op 默认)
  9. SELF_MODEL_BUILD         → _stage_09_self_model_build         (no-op 默认)
 10. SELF_MODEL_EVOLUTION     → _stage_10_self_model_evolution     (no-op 默认)
 11. SELF_MODEL_REFLECTION    → _stage_11_self_model_reflection    (no-op 默认)
 12. SELF_MODEL_VALIDATION    → _stage_12_self_model_validation    (no-op 默认)
 13. SELF_MODEL_PERSISTENCE   → _stage_13_self_model_persistence   (no-op 默认)
 14. RESPONSE_GENERATION      → _stage_14_response_generation
 15. GUARD_CHAIN              → _stage_15_guard_chain
 16. RESPONSE                 → _stage_16_response
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

# ============================================================
# 1. Re-export: RuntimeStage / RUNTIME_LIFECYCLE_ORDER 从 stages.py
#    保证 stages.py 仍是枚举单源真相；lifecycle_executor.py 仅做调度实现
# ============================================================
from src.runtime.stages import (  # noqa: F401  (re-export 给调用方)
    RUNTIME_LIFECYCLE_ORDER,
    RUNTIME_LIFECYCLE_ORDER_FULL,
    RuntimeStage,
)
from src.runtime.context.runtime_context import RuntimeContext
from src.runtime.events import Event


logger = logging.getLogger(__name__)


# ============================================================
# 2. FROZEN CONTRACT: Stage → Method Name 映射
#    tests/test_runtime_stage_contract.py 会用 17 断言锁住此表
# ============================================================
# 顺序严格与 RUNTIME_LIFECYCLE_ORDER 对齐（17 项）
STAGE_TO_METHOD_NAME: List[tuple] = [
    # (stage_enum, method_name)
    (RuntimeStage.CONTROL_CHECK, "_stage_00_control_check"),
    (RuntimeStage.RECEIVE_EVENT, "_stage_01_receive_event"),
    (RuntimeStage.MEMORY_RETRIEVAL, "_stage_02_memory_retrieval"),
    (RuntimeStage.EMOTION_UPDATE, "_stage_03_emotion_update"),
    (RuntimeStage.GROWTH_EVALUATION, "_stage_04_growth_evaluation"),
    (RuntimeStage.PERSONALITY_UPDATE, "_stage_05_personality_update"),
    (RuntimeStage.PERSONALITY_CONTEXT_BUILD, "_stage_06_personality_context_build"),
    (RuntimeStage.PERCEPTION_OBSERVATION, "_stage_07_perception_observation"),
    (RuntimeStage.PERCEPTION_ANALYSIS, "_stage_08_perception_analysis"),
    (RuntimeStage.SELF_MODEL_BUILD, "_stage_09_self_model_build"),
    (RuntimeStage.SELF_MODEL_EVOLUTION, "_stage_10_self_model_evolution"),
    (RuntimeStage.SELF_MODEL_REFLECTION, "_stage_11_self_model_reflection"),
    (RuntimeStage.SELF_MODEL_VALIDATION, "_stage_12_self_model_validation"),
    (RuntimeStage.SELF_MODEL_PERSISTENCE, "_stage_13_self_model_persistence"),
    (RuntimeStage.RESPONSE_GENERATION, "_stage_14_response_generation"),
    (RuntimeStage.GUARD_CHAIN, "_stage_15_guard_chain"),
    (RuntimeStage.RESPONSE, "_stage_16_response"),
]

# 反向索引：stage enum → method_name（运行时分派用）
_STAGE_TO_METHOD: dict = {s: name for s, name in STAGE_TO_METHOD_NAME}


class LifecycleExecutor:
    """17 阶段调度器（纯调度，不持有业务状态）。

    唯一实现位置：LifecycleExecutor.execute() —— 17 阶段调度**只允许存在这里**。
    RuntimeCore 组合持有 self._lifecycle = LifecycleExecutor() 作为私有成员。

    架构收益（SPEC v0.2 §2.1）：
    - 未来 Phase 5 加阶段（Dream/Reflection/Self Goal）只改本文件
    - RuntimeCore 不再膨胀（只负责提供 stage 级接口方法）
    - 单元测试可以直接 mock RuntimeCore，单独验证调度逻辑
    """

    def __init__(self, logger_instance: Optional[logging.Logger] = None) -> None:
        self._logger = logger_instance or logging.getLogger(__name__)
        # 观测计数（用于诊断/test 断言）
        self.execution_count: int = 0
        # 最近一次 execute 按顺序访问过的 stage 名列表
        self.last_stage_order: List[str] = []

    # --------------------------------------------------------
    # 主入口：execute(core, event, ctx)
    # --------------------------------------------------------
    def execute(
        self,
        core: Any,
        event: Optional[Event],
        ctx: RuntimeContext,
    ) -> RuntimeContext:
        """执行完整生命周期（17 阶段）。

        契约：
        - Stage 1（RECEIVE_EVENT）调用完 stage 方法后**必须且仅一次**调用
          core._on_event(...)（P0-修改3：解决重复事件注入的数据污染）
        - 单个阶段异常必须 fail-soft（logger.warning + ctx 记录，不中断）
        - 阶段 7-13（PERCEPTION / SELF_MODEL_*）在 core 无对应接口时默认 no-op

        Args:
            core: RuntimeCore 实例（duck-typed，不强制 import 具体类，避免环）
            event: 输入事件（允许 None，对应 tick/心跳等）
            ctx: RuntimeContext（允许外部注入已有 ctx；若为 None 则在此新建）

        Returns:
            填充了阶段结果的 RuntimeContext
        """
        # 0) 初始化 ctx 安全网
        if ctx is None:
            ctx = RuntimeContext()
        # 初始化阶段错误表（RuntimeCore 侧也有同名引用，此处保证 ctx 侧也有）
        if not hasattr(ctx, "_phase_errors") or not isinstance(
            getattr(ctx, "_phase_errors"), dict,
        ):
            try:
                ctx._phase_errors = {}  # type: ignore[attr-defined]
            except Exception:
                pass
        self.last_stage_order = []
        self.execution_count += 1

        # 1) 遍历 17 个阶段（与 RUNTIME_LIFECYCLE_ORDER 严格对齐 = 17 项）
        for stage in RUNTIME_LIFECYCLE_ORDER:
            # a) 设置上下文当前阶段（供 stage 方法读取 / 诊断面板显示）
            try:
                ctx.current_stage = stage  # type: ignore[attr-defined]
            except Exception:
                pass
            self.last_stage_order.append(stage.name)

            # Phase 4.0.2 Gate1: 每阶段日志
            stage_index = RUNTIME_LIFECYCLE_ORDER.index(stage)
            logger.info(
                "Stage %d %s", stage_index, stage.name,
            )

            # b) 分派执行
            try:
                self._dispatch_stage(core, stage, event, ctx)
            except Exception as exc:  # noqa: BLE001  (fail-soft)
                # 记录错误到两份：ctx._phase_errors（跨组件共享）+ core 侧 error 表
                err_repr = repr(exc)
                try:
                    errors = ctx._phase_errors  # type: ignore[attr-defined]
                    if isinstance(errors, dict):
                        errors[stage.name] = err_repr
                except Exception:
                    pass
                # 同步写到 core._last_process_phase_errors（若存在），保持向后兼容
                try:
                    core_errors = getattr(core, "_last_process_phase_errors", None)
                    if isinstance(core_errors, dict):
                        core_errors[stage.name] = err_repr
                except Exception:
                    pass
                self._logger.warning(
                    "[LifecycleExecutor] stage=%s 失败（已隔离，不中断）: %s",
                    stage.name, exc,
                )
                continue  # fail-soft: 继续下一阶段

        return ctx

    # --------------------------------------------------------
    # 内部：单阶段分派（统一入口，唯一实现点）
    # --------------------------------------------------------
    def _dispatch_stage(
        self,
        core: Any,
        stage: RuntimeStage,
        event: Optional[Event],
        ctx: RuntimeContext,
    ) -> None:
        """分派一个阶段到 core 的具体方法。

        逻辑：
        1. 查表 _STAGE_TO_METHOD[stage] → method_name
        2. 若 core 有对应可调用方法 → getattr + 调用 (event, ctx)
        3. 若无 → 视为 no-op（不抛错，阶段 7-13 就是这种情况）
        4. **Stage 1 特殊处理（P0-修改3 C-1）**：调用完 _stage_01_receive_event
           后，额外调用 core._on_event(...) exactly once

        Args:
            core: duck-typed RuntimeCore 实例
            stage: 当前阶段枚举
            event: 输入事件（透传给 stage 方法）
            ctx: 运行时上下文（透传）

        Raises:
            任何阶段方法抛出的异常（由 execute() 外层 try/except 捕获）
        """
        method_name = _STAGE_TO_METHOD.get(stage)
        if method_name is None:
            # 未知阶段（不应出现，除非 stages.py 新增枚举但忘了更新冻结表）
            self._logger.debug(
                "[LifecycleExecutor] stage=%s 不在 STAGE_TO_METHOD 映射，视为 no-op",
                stage,
            )
            return

        # --- 执行阶段方法（存在则调用，不存在 = no-op）---
        method = getattr(core, method_name, None)
        if callable(method):
            method(event, ctx)
        # 否则：静默 no-op（Stage 7-13 正常路径，或未来 RuntimeCore 未实现的阶段）

        # --- C-1 契约：Stage 1 RECEIVE_EVENT 后 exactly once 调 _on_event ---
        if stage == RuntimeStage.RECEIVE_EVENT:
            self._invoke_on_event_exactly_once(core, event, ctx)

    # --------------------------------------------------------
    # P0-修改3: 用户消息不提前 inject_event
    # Stage 1 完成后 exactly once 调 _on_event → 状态变化只发生一次
    # --------------------------------------------------------
    def _invoke_on_event_exactly_once(
        self,
        core: Any,
        event: Optional[Event],
        ctx: RuntimeContext,
    ) -> None:
        """Stage 1 内部调用 core._on_event() exactly once（解决数据污染）。

        规格要求：
        - 旧链路：inject_event() → emotion×2 + world_state×2 + experience×2（错误）
        - 新链路：process() → Stage 1 Receive → _on_event exactly once → 状态变化×1（正确）

        语义上：_on_event 负责把 event 造成的状态改变（self_state/world_state/
        experience_builder 等）落地。之前放在 process() 之外调用时，process 的
        Stage 1 又会做一次事件提取 → 双重状态变化。

        Args:
            core: RuntimeCore 实例（必须实现 _on_event(any_event_obj)）
            event: 原始 Event 对象（可能是新 runtime.events.Event）
            ctx: RuntimeContext（用于把结果挂到 ctx._on_event_called 上做 test 断言）
        """
        on_event = getattr(core, "_on_event", None)
        if not callable(on_event):
            # core 无 _on_event 方法（可能是 mock / 极简实例）→ 记个标记就返回
            try:
                ctx._on_event_called = False  # type: ignore[attr-defined]
            except Exception:
                pass
            return

        # 把 Event 适配成 _on_event 期望的 duck-typed 对象：
        #   必须有 .event_type 和 .data 两个属性（runtime_core.py _on_event 签名）
        adapted: Any = None
        if event is not None:
            # Event.type → .event_type；Event.payload → .data
            try:
                adapted = type(
                    "_RuntimeEventAdapter",
                    (object,),
                    {
                        "event_type": getattr(event, "type", "unknown"),
                        "data": getattr(event, "payload", {}) or {},
                    },
                )()
            except Exception:
                adapted = None

        try:
            if adapted is not None:
                on_event(adapted)
            else:
                # event=None 时（例如 tick 场景），造一条空事件
                on_event(type("obj", (object,), {"event_type": "tick", "data": {}})())
        except Exception as exc:
            # _on_event 内部异常 → 记录但不中断阶段链（fail-soft）
            self._logger.warning(
                "[LifecycleExecutor] core._on_event() 异常（已隔离）: %s", exc,
            )
        finally:
            # 无论成功失败都打标记（test 断言 exactly once 用）
            try:
                called_count = getattr(ctx, "_on_event_call_count", 0)
                ctx._on_event_call_count = called_count + 1  # type: ignore[attr-defined]
                ctx._on_event_called = True  # type: ignore[attr-defined]
            except Exception:
                pass
