# -*- coding: utf-8 -*-
"""
src/orchestrator/self_model_orchestrator.py

Phase 5.0-A: SelfModelOrchestrator —— Orchestrator 内的 SelfModel 5 阶段编排器。

职责:
- 在每次 Orchestrator.process() 完成后,串接触发 SelfModel 五阶段:
  Build → Evolution → Reflection → Validation → Persistence
- 任一阶段独立 try/except 隔离,失败不影响其他阶段,更不影响用户回复
- 默认 no-op:五个子系统全部 None 时仍可工作(什么都不做)
- 不修改 ResponseEngine.generate()
- 不修改 RuntimeContext schema
- 不引入 LLM SDK
- 不依赖 src.personality 业务核心
- 不持有 Orchestrator 引用(单向依赖:Orchestrator → SelfModelOrchestrator)

约束:
- 全部子系统可注入,默认 None 仍向后兼容
- 返回 dict 结构固定:
  {
    "build": Optional[SelfModelSnapshot],
    "evolution": Optional[SelfModelEvolutionResult],
    "reflection": Optional[ReflectionRecord],
    "validation": Optional[ConsistencyReport],
    "persistence": Optional[bool],
    "errors": List[str],
  }
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


SELF_MODEL_ORCHESTRATOR_SCHEMA_VERSION = "1.0"


# ============================================================
# SelfModelOrchestrator
# ============================================================
class SelfModelOrchestrator:
    """SelfModel 五阶段编排器(Phase 5.0-A / v1.0)。

    使用方式:
        smo = SelfModelOrchestrator(
            foundation=SelfModelFoundation(identity_id="yuyi_default"),
            evolution_engine=SelfModelEvolutionEngine(),
            reflection_engine=SelfModelReflectionEngine(),
            persistence_runtime=PersistenceRuntime(),
            identity_id="yuyi_default",
        )
        # 在 Orchestrator.process() 末尾调用
        result = smo.run_after_event(context={...})

    关键不变量:
    - 任一阶段失败 → 仅记录到 errors,不影响其他阶段
    - 任一阶段失败 → 不影响 Orchestrator 返回 reply
    - 全部 None 注入 → 全部 no-op,返回空 result + 0 error
    - 线程安全(可重入 lock)
    - 不修改任何输入(只读 context)
    """

    name: str = "self_model_orchestrator"
    schema_version: str = SELF_MODEL_ORCHESTRATOR_SCHEMA_VERSION

    def __init__(
        self,
        foundation: Optional[Any] = None,
        evolution_engine: Optional[Any] = None,
        reflection_engine: Optional[Any] = None,
        consistency_checker: Optional[Any] = None,
        persistence_runtime: Optional[Any] = None,
        identity_id: str = "yuyi_default",
    ) -> None:
        self._lock = threading.RLock()
        self._foundation = foundation
        self._evolution = evolution_engine
        self._reflection = reflection_engine
        self._checker = consistency_checker
        self._persistence = persistence_runtime
        self._identity_id = str(identity_id or "yuyi_default")

        # 统计
        self._run_count: int = 0
        self._last_run_at: Optional[str] = None
        self._last_errors: List[str] = []
        self._errors_total: int = 0
        self._last_old_snapshot: Optional[Any] = None
        self._last_new_snapshot: Optional[Any] = None

    # --------------------------------------------------------
    # 属性
    # --------------------------------------------------------
    @property
    def identity_id(self) -> str:
        return self._identity_id

    @property
    def foundation(self) -> Optional[Any]:
        return self._foundation

    @property
    def evolution_engine(self) -> Optional[Any]:
        return self._evolution

    @property
    def reflection_engine(self) -> Optional[Any]:
        return self._reflection

    @property
    def consistency_checker(self) -> Optional[Any]:
        return self._checker

    @property
    def persistence_runtime(self) -> Optional[Any]:
        return self._persistence

    @property
    def run_count(self) -> int:
        return self._run_count

    @property
    def last_errors(self) -> List[str]:
        with self._lock:
            return list(self._last_errors)

    @property
    def errors_total(self) -> int:
        """自创建以来所有 run 中累计的错误数。"""
        with self._lock:
            return self._errors_total

    @property
    def last_old_snapshot(self) -> Optional[Any]:
        return self._last_old_snapshot

    @property
    def last_new_snapshot(self) -> Optional[Any]:
        return self._last_new_snapshot

    # --------------------------------------------------------
    # 主入口: run_after_event
    # --------------------------------------------------------
    def run_after_event(
        self,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """在每次事件处理后被 Orchestrator 调用。

        串接 5 阶段,任一阶段异常被隔离到 errors 列表。

        参数:
        - context: 当前事件的运行时上下文 dict,可包含:
            - trait_states: Dict
            - growth_records: List
            - relationship_state: Dict
            - current_state: Dict
            - identity_overrides: Dict
            - extra_entries: List
            - proposals: List[GrowthProposal]
            - reflections: List[ReflectionRecord]
            - manual_changes: List
          所有字段都是可选,context 可为 None。

        返回:
        - dict,固定 keys:build / evolution / reflection / validation / persistence / errors
        - 任何阶段未配置/失败 → 对应值为 None
        - 全部成功 → errors 为 []
        """
        with self._lock:
            self._run_count += 1
            errors: List[str] = []
            ctx = context if isinstance(context, dict) else {}

            # 1) Build
            new_snapshot = None
            if self._foundation is not None:
                try:
                    inputs = self._build_inputs_from_context(ctx)
                    new_snapshot = self._foundation.build(inputs=inputs)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"build: {exc!r}")
                    logger.warning("SelfModel build 失败(已隔离): %s", exc)
            else:
                # 未配置 foundation,尝试 foundation 已有的 snapshot
                try:
                    if self._foundation is not None:
                        new_snapshot = self._foundation.snapshot()
                except Exception:  # noqa: BLE001
                    new_snapshot = None

            # 1.1 退化:即使 foundation 为 None,若 evolution 需要 snapshot,
            #     可以跳过该阶段(由 evolution 自身处理 None)
            old_snapshot = self._safe_get_current_snapshot()

            # 2) Evolution
            evolution_result = None
            if self._evolution is not None and new_snapshot is not None:
                try:
                    proposals = ctx.get("proposals") or []
                    reflections = ctx.get("reflections") or []
                    manual_changes = ctx.get("manual_changes") or []
                    evolution_result = self._evolution.evolve(
                        snapshot=new_snapshot,
                        proposals=list(proposals) if isinstance(proposals, list) else None,
                        reflections=list(reflections) if isinstance(reflections, list) else None,
                        manual_changes=list(manual_changes) if isinstance(manual_changes, list) else None,
                    )
                    # 演化后的新 snapshot 替换
                    if evolution_result is not None:
                        new_snap = getattr(evolution_result, "new_snapshot", None)
                        if new_snap is not None:
                            new_snapshot = new_snap
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"evolution: {exc!r}")
                    logger.warning("SelfModel evolution 失败(已隔离): %s", exc)
            else:
                # 未配置 evolution,或 build 失败导致 new_snapshot 为 None
                pass

            # 3) Reflection(Phase 4.7)
            reflection_record = None
            if self._reflection is not None and new_snapshot is not None:
                try:
                    reflect_method = getattr(self._reflection, "reflect", None)
                    if reflect_method is not None:
                        evolution_history = self._safe_get_evolution_history(ctx)
                        reflection_record = reflect_method(
                            old_snapshot=old_snapshot,
                            new_snapshot=new_snapshot,
                            evolution_history=evolution_history,
                        )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"reflection: {exc!r}")
                    logger.warning("SelfModel reflection 失败(已隔离): %s", exc)

            # 4) Validation(Phase 4.7 ConsistencyChecker)
            validation_report = None
            if self._checker is not None and new_snapshot is not None:
                try:
                    check_method = getattr(self._checker, "check", None)
                    if check_method is not None:
                        evolution_history = self._safe_get_evolution_history(ctx)
                        validation_report = check_method(
                            snapshot=new_snapshot,
                            evolution_history=evolution_history,
                        )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"validation: {exc!r}")
                    logger.warning("SelfModel validation 失败(已隔离): %s", exc)

            # 5) Persistence
            persistence_result = None
            if self._persistence is not None and evolution_result is not None:
                try:
                    persist_method = getattr(self._persistence, "persist_evolution", None)
                    if persist_method is not None:
                        persistence_result = bool(persist_method(evolution_result))
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"persistence: {exc!r}")
                    logger.warning("SelfModel persistence 失败(已隔离): %s", exc)
            elif self._persistence is not None and new_snapshot is not None:
                # 即使没有 evolution_result,也尝试落盘当前 snapshot
                try:
                    store_method = getattr(self._persistence, "save_snapshot", None)
                    if store_method is not None:
                        persistence_result = bool(store_method(new_snapshot))
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"persistence: {exc!r}")
                    logger.warning("SelfModel persistence 失败(已隔离): %s", exc)

            # 记录 last_*
            self._last_errors = list(errors)
            self._errors_total += len(errors)
            self._last_old_snapshot = old_snapshot
            self._last_new_snapshot = new_snapshot
            self._last_run_at = self._now_iso()

            return {
                "build": new_snapshot,
                "evolution": evolution_result,
                "reflection": reflection_record,
                "validation": validation_report,
                "persistence": persistence_result,
                "errors": list(errors),
            }

    # --------------------------------------------------------
    # 辅助
    # --------------------------------------------------------
    def _build_inputs_from_context(
        self, ctx: Dict[str, Any],
    ) -> Dict[str, Any]:
        """从 context 提取 SelfModelFoundation.build() 的 inputs。

        严格按 Foundation 接口识别的字段抽取,不引入未识别字段。
        """
        keys = (
            "trait_states",
            "growth_records",
            "relationship_state",
            "current_state",
            "identity_overrides",
            "extra_entries",
            "core_values",
            "preferences",
        )
        out: Dict[str, Any] = {}
        for k in keys:
            if k in ctx and ctx[k] is not None:
                out[k] = ctx[k]
        return out

    def _safe_get_current_snapshot(self) -> Optional[Any]:
        """从 foundation 取当前 snapshot(只读)。失败返回 None。"""
        if self._foundation is None:
            return None
        try:
            current_attr = getattr(self._foundation, "current", None)
            if current_attr is not None:
                return current_attr
            snap_method = getattr(self._foundation, "snapshot", None)
            if snap_method is not None:
                return snap_method()
        except Exception:  # noqa: BLE001
            return None
        return None

    def _safe_get_evolution_history(
        self, ctx: Dict[str, Any],
    ) -> Optional[List[Any]]:
        """从 context 或 persistence 拿 evolution_history。失败返回 None。"""
        # 优先用 context 显式提供
        if "evolution_history" in ctx and isinstance(ctx["evolution_history"], list):
            return list(ctx["evolution_history"])
        # fallback 从 persistence
        if self._persistence is not None:
            try:
                method = getattr(self._persistence, "get_evolution_history", None)
                if method is not None:
                    return list(method(limit=10) or [])
            except Exception:  # noqa: BLE001
                return None
        return None

    @staticmethod
    def _now_iso() -> str:
        try:
            from datetime import datetime, timezone
            return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        except (AttributeError, TypeError):  # pragma: no cover
            from datetime import datetime
            return datetime.utcnow().isoformat() + "Z"

    # --------------------------------------------------------
    # 健康度自检
    # --------------------------------------------------------
    def health_check(self) -> Dict[str, Any]:
        """返回 5 子系统健康状态。"""
        with self._lock:
            return {
                "name": self.name,
                "schema_version": self.schema_version,
                "identity_id": self._identity_id,
                "run_count": self._run_count,
                "errors_total": self._errors_total,
                "last_run_at": self._last_run_at,
                "last_errors": list(self._last_errors),
                "subsystems": {
                    "foundation": self._subsystem_status(self._foundation),
                    "evolution": self._subsystem_status(self._evolution),
                    "reflection": self._subsystem_status(self._reflection),
                    "validation": self._subsystem_status(self._checker),
                    "persistence": self._subsystem_status(self._persistence),
                },
            }

    @staticmethod
    def _subsystem_status(obj: Optional[Any]) -> Dict[str, Any]:
        if obj is None:
            return {"configured": False, "healthy": True}
        hc = getattr(obj, "health_check", None)
        if hc is None:
            return {"configured": True, "healthy": True, "has_health_check": False}
        try:
            result = hc()
            if isinstance(result, dict):
                result = dict(result)
                result.setdefault("configured", True)
                result.setdefault("healthy", True)
                result.setdefault("has_health_check", True)
                return result
            return {"configured": True, "healthy": True, "has_health_check": True}
        except Exception as exc:  # noqa: BLE001
            return {
                "configured": True,
                "healthy": False,
                "has_health_check": True,
                "error": str(exc),
            }

    def reset_stats(self) -> None:
        """重置统计(测试用)。"""
        with self._lock:
            self._run_count = 0
            self._last_run_at = None
            self._last_errors = []
            self._errors_total = 0
            self._last_old_snapshot = None
            self._last_new_snapshot = None


__all__ = [
    "SelfModelOrchestrator",
    "SELF_MODEL_ORCHESTRATOR_SCHEMA_VERSION",
]
