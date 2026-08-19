# -*- coding: utf-8 -*-
"""
src/orchestrator_runtime_bridge.py

Phase 3.8.5: Orchestrator ↔ RuntimeCore 桥接

职责:
- 让 Orchestrator.process() 优先走 RuntimeCore.process() (Phase 3.7.3+ 13-阶段)
- RuntimeCore 失败时降级到 legacy `engine.generate()` 路径
- 保持 Orchestrator 既有行为兼容,所有现有调用代码无需修改

约束:
- 不修改 src/memory / src/emotion / src/growth / src/personality 核心模块
- 不修改 src/contracts/*
- 不修改 Orchestrator.process() 原签名 (user_message: str) -> str
- 独立于 src/runtime/runtime_bridge.py,避免耦合

使用:
    bridge = OrchestratorRuntimeBridge(orchestrator)
    reply = bridge.handle_message("hi")
    # 优先 RuntimeCore.process() → final_reply
    # Runtime 失败 → legacy_generate() 兜底
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class OrchestratorRuntimeBridge:
    """Orchestrator 与 RuntimeCore 的桥接（Phase 3.8.5 / v1.0）。

    设计:
    - 不持有 Orchestrator 的所有子系统（避免重复初始化）;
      启动时让 Orchestrator 把 self.engine / self.personality_resolver 暴露给本类
    - handle_message() 走 RuntimeCore.process(),失败 fallback 到 legacy
    """

    def __init__(
        self,
        orchestrator: Any,
        runtime_core: Optional[Any] = None,
        response_guard_chain: Optional[Any] = None,
    ) -> None:
        self._orch = orchestrator
        self._runtime = runtime_core
        self._guard_chain = response_guard_chain
        # 状态统计
        self._runtime_call_count = 0           # Runtime.process() 成功次数（类型A）
        self._runtime_state_count = 0          # Runtime.inject_event() 状态更新次数（类型B）
        self._legacy_call_count = 0            # legacy 生成回复次数
        self._last_runtime_error: Optional[str] = None
        self._last_legacy_reason: Optional[str] = None
        self._last_ctx: Optional[Any] = None
        # "runtime" (类型A完整) / "runtime_state+legacy" (类型B状态+legacy回复) / "legacy" (纯legacy)
        self._last_reply_source: Optional[str] = None
        self._last_runtime_mode: Optional[str] = None  # "full_process" / "state_only" / None
        # Phase 4.0.2: __init__ 传入 runtime_core 时也注入 engine ref
        if runtime_core is not None:
            self._inject_engine_ref(runtime_core)

    # --------------------------------------------------------
    # 注入 / 配置
    # --------------------------------------------------------
    def set_runtime_core(self, runtime_core: Any) -> None:
        """运行时注入 RuntimeCore（覆盖 __init__ 传入值）。

        Phase 4.0.2 升级:注入后自动把 orchestrator.engine 引用透传到
        runtime_core._orchestrator_engine_ref，让 Stage14 无需
        ResponseAdapter 也能生成回复。
        """
        self._runtime = runtime_core
        self._inject_engine_ref(runtime_core)

    @property
    def runtime_core(self) -> Optional[Any]:
        """P4.3-A: 当前注入的 RuntimeCore 引用（只读）。

        Orchestrator 经此读取 v3.5.27 current relationship 权威实例
        （runtime_core.relationship_state_runtime）——与 runtime 链
        同源、同一实例。返回引用，不复制。
        """
        return self._runtime

    def set_guard_chain(self, guard_chain: Any) -> None:
        self._guard_chain = guard_chain

    def _inject_engine_ref(self, runtime_core: Any) -> None:
        """Phase 4.0.2: 把 Orchestrator.engine 引用注入 RuntimeCore。

        设计原则（fail-soft，最小侵入）：
        - runtime_core=None / 无对应属性 → 静默跳过
        - 已有非空引用 → 不覆盖（保持外部手动注入优先）
        - 任何属性访问异常被隔离
        """
        if runtime_core is None:
            return
        orch = self._orch
        if orch is None:
            return
        engine = getattr(orch, "engine", None)
        if engine is not None:
            # 候选属性名: runtime_core 中 Stage14 会读 self._orchestrator_engine_ref
            target_attrs = [
                "_orchestrator_engine_ref",
                "orchestrator_engine_ref",
            ]
            for attr in target_attrs:
                try:
                    if hasattr(runtime_core, attr):
                        existing = getattr(runtime_core, attr, None)
                        if existing is None:
                            setattr(runtime_core, attr, engine)
                            logger.debug(
                                "[OrchestratorRuntimeBridge] engine ref 已注入: .%s",
                                attr,
                            )
                            break
                except Exception:
                    continue
        # P4.2-IMPL-C7: 注入 orchestrator 的 v0.6 long_term RelationshipState 引用，
        # 让 RuntimeCore 能以 v0.6 为 long_term 权威构建 RelationshipSnapshot
        # （同进程同实例，无持久化双写）。fail-soft：orch 无该属性 → 跳过，
        # RuntimeCore 回退到 C5 语义（long_term 由 relationship_state_runtime 推导）。
        long_term_ref = None
        rel_wrapper = getattr(orch, "relationship_state", None)
        if rel_wrapper is not None:
            long_term_ref = getattr(rel_wrapper, "_impl", None)
        if long_term_ref is not None:
            for attr in (
                "_orchestrator_long_term_state_ref",
                "orchestrator_long_term_state_ref",
            ):
                try:
                    if hasattr(runtime_core, attr):
                        existing = getattr(runtime_core, attr, None)
                        if existing is None:
                            setattr(runtime_core, attr, long_term_ref)
                            logger.debug(
                                "[OrchestratorRuntimeBridge] v0.6 long_term ref 已注入: .%s",
                                attr,
                            )
                            break
                except Exception:
                    continue

    # --------------------------------------------------------
    # 主入口
    # --------------------------------------------------------
    def handle_message(self, user_message: str) -> str:
        """处理一条用户消息,按 Runtime 可用性选择路径。

        流程:
        1) 尝试 Runtime 路径 (_try_runtime)
           - 类型 A (process): 返回 final_reply → 直接返回, source="runtime"
           - 类型 B (inject_event): Runtime 更新状态但不生成回复 → 走 legacy,
             source="runtime_state+legacy", 状态计数 +1
           - 失败 / 未知 → 走 legacy, source="legacy"
        2) legacy 路径:直接调 orchestrator.engine.generate(...)
        """
        self._last_runtime_mode = None

        # 1) 尝试 Runtime
        if self._runtime is not None:
            reply = self._try_runtime(user_message)
            if reply is not None:
                # 类型 A：Runtime.process() 生成了完整回复
                self._runtime_call_count += 1
                self._last_reply_source = "runtime"
                return reply

        # 2) 进入 legacy 路径前，判断 Runtime 是否已经做了状态更新（类型B）
        runtime_state_done = (
            self._last_runtime_mode == "state_only"
            and self._last_runtime_error is None
        )
        if runtime_state_done:
            self._runtime_state_count += 1

        # 3) legacy fallback
        self._last_legacy_reason = (
            "state_only_needs_legacy_reply"
            if runtime_state_done
            else (self._last_runtime_error or "no_runtime_core")
        )
        self._legacy_call_count += 1
        self._last_reply_source = (
            "runtime_state+legacy" if runtime_state_done else "legacy"
        )
        return self._legacy_generate(user_message)

    # --------------------------------------------------------
    # 内部: Runtime 路径
    # Phase 4.0.2 SPEC: 不再鸭子类型判断 Runtime 类型。
    #   Runtime 必须有 process(event, ctx) → 直接调用。
    #   任何 AttributeError / Exception → except 块 fallback legacy。
    #   禁止: hasattr(runtime, "process") 这种判断。
    # --------------------------------------------------------
    def _try_runtime(self, user_message: str) -> Optional[str]:
        """Phase 4.0.2: Runtime 路径（无鸭子类型，直接 process）。

        Phase 4.0.1 后 Runtime 必须提供 process(event, ctx) 统一入口；
        RuntimeCore.process() 内部通过 LifecycleExecutor 执行 17 阶段，
        包括 Stage 1（_on_event exactly once）→ 状态更新只发生一次。

        Returns:
            成功时返回回复 str；任何情况返回 None → 外层自动走 legacy 兜底
        """
        rt = self._runtime
        if rt is None:
            self._last_runtime_error = "no_runtime_core"
            return None

        # ===== Phase 4.0.2 SPEC: 直接调用 runtime.process()，不做 hasattr 判断 =====
        try:
            from src.runtime.events import Event

            event = Event(
                type="user_input",
                source="user",
                payload={"text": user_message, "content": user_message},
            )
            # Runtime 必须有 process()；没有时 AttributeError 会被外层 except 捕获
            # 进入 legacy fallback 路径
            ctx = rt.process(event)
            self._last_ctx = ctx
            self._last_runtime_error = None
            final = getattr(ctx, "finalized_reply", None)
            if not (isinstance(final, str) and final.strip()):
                final = getattr(ctx, "_final_reply", None)
            if final and isinstance(final, str) and final.strip():
                self._last_runtime_mode = "full_process"
                return final
            # process 跑了但没生成 final_reply → 记录原因，回落到 legacy
            self._last_runtime_error = "empty_final_reply_after_process"
            return None
        except Exception as exc:  # noqa: BLE001
            self._last_runtime_error = f"process_failed:{exc!r}"
            logger.warning(
                "[OrchestratorRuntimeBridge] Runtime.process() 路径失败: %s, fallback legacy",
                exc,
            )
            return None

    # --------------------------------------------------------
    # 内部: Legacy 路径（直接调 orchestrator.engine.generate）
    # --------------------------------------------------------
    def _legacy_generate(self, user_message: str) -> str:
        """Legacy fallback:直接走 Orchestrator.engine.generate(...)。"""
        orch = self._orch
        engine = getattr(orch, "engine", None)
        if engine is None:
            return "抱歉,我遇到了一些问题,请稍后再试。"
        # 复用 Orchestrator 已有的 personality / self_model 上下文抽取
        try:
            personality_context = (
                self._extract_legacy_personality(orch)
            )
            emotion_ctx = self._extract_legacy_emotion(orch)
            relationship_ctx = self._extract_legacy_relationship(orch)
            self_model_ctx = (
                orch.get_self_model_context()
                if hasattr(orch, "get_self_model_context")
                else None
            )
            history = getattr(orch, "history", [])
            chat_memories = self._extract_legacy_chat_memories(orch, user_message)
            reply = engine.generate(
                user_message=user_message,
                history=history,
                chat_memories=chat_memories,
                life_events=[],
                personality_context=personality_context,
                resolved_behavior=None,
                self_model_context=self_model_ctx,
                emotion_context=emotion_ctx,
                relationship_context=relationship_ctx,
                context_prompt_blocks=[],
            )
            return reply
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[OrchestratorRuntimeBridge] legacy 路径失败: %s", exc
            )
            return "抱歉,我遇到了一些问题,请稍后再试。"

    # --------------------------------------------------------
    # Legacy 上下文抽取（不修改 Orchestrator 内部代码,只用 getattr 拉取）
    # --------------------------------------------------------
    @staticmethod
    def _extract_legacy_personality(orch: Any) -> Any:
        try:
            pr = getattr(orch, "personality_resolver", None)
            if pr is None:
                return None
            personality = pr.resolve()
            if hasattr(orch, "_get_personality_context"):
                return orch._get_personality_context(personality)
        except Exception:
            return None
        return None

    @staticmethod
    def _extract_legacy_emotion(orch: Any) -> Any:
        try:
            em = getattr(orch, "emotion_manager", None)
            if em is None:
                return {}
            # 简单契约:取 emotion_manager 的当前 state
            state = getattr(em, "get_state", None)
            if callable(state):
                return state() or {}
        except Exception:
            return {}
        return {}

    @staticmethod
    def _extract_legacy_relationship(orch: Any) -> Any:
        try:
            rel = getattr(orch, "relationship_state", None)
            if rel is not None and hasattr(rel, "get"):
                return rel.get() or {}
        except Exception:
            return {}
        return {}

    @staticmethod
    def _extract_legacy_chat_memories(orch: Any, user_message: str) -> list:
        """从 memory_store / vector_memory 抽取相关 chat_memories。"""
        try:
            memories = []
            ms = getattr(orch, "memory_store", None)
            if ms is not None and hasattr(ms, "load"):
                loaded = ms.load()
                if loaded:
                    memories.extend(loaded)
            vm = getattr(orch, "vector_memory", None)
            if vm is not None and hasattr(vm, "search"):
                results = vm.search(user_message, top_k=5) or []
                for r in results:
                    if r not in memories:
                        memories.append(r)
            return memories
        except Exception:
            return []

    # --------------------------------------------------------
    # 状态查询
    # --------------------------------------------------------
    @property
    def runtime_call_count(self) -> int:
        """Runtime.process() 完整生成回复的次数（类型A）。"""
        return self._runtime_call_count

    @property
    def runtime_state_count(self) -> int:
        """Runtime.inject_event() 仅更新状态的次数（类型B）。"""
        return self._runtime_state_count

    @property
    def legacy_call_count(self) -> int:
        """legacy 生成回复的次数。"""
        return self._legacy_call_count

    @property
    def last_runtime_error(self) -> Optional[str]:
        return self._last_runtime_error

    @property
    def last_legacy_reason(self) -> Optional[str]:
        return self._last_legacy_reason

    @property
    def last_ctx(self) -> Optional[Any]:
        return self._last_ctx

    @property
    def last_reply_source(self) -> Optional[str]:
        """
        最近一次回复来源：
        - "runtime"             : Runtime.process() 完整生成（类型A）
        - "runtime_state+legacy": Runtime 更新状态后由 legacy 生成回复（类型B）
        - "legacy"              : 纯 legacy 生成（Runtime 不可用）
        """
        return self._last_reply_source

    @property
    def last_runtime_mode(self) -> Optional[str]:
        """最近一次 Runtime 路径模式: "full_process" / "state_only" / None。"""
        return self._last_runtime_mode


__all__ = ["OrchestratorRuntimeBridge"]
