# -*- coding: utf-8 -*-
"""
src/admin/dashboard/provider.py

Phase 5.0 Dashboard Upgrade —— DashboardProvider 聚合 Provider。

职责:
- 聚合 RuntimeProvider / SelfModelProvider 等子 Provider 的数据
- 输出 YuyiDashboardSnapshot
- 只读,不修改任何 Authority 数据
- 不直接 import 任何业务模块

约束:
- 禁止 import: memory / growth / emotion / personality / relationship / runtime core / self_model
- 通过 src.admin.runtime_provider / src.admin.self_model_provider 访问
- 严格容错:任何子 Provider 不可用时返回 fallback 视图
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from src.admin.dashboard.snapshot import (
    BodyStateView,
    EmotionView,
    GoalStateView,
    GrowthSummaryView,
    HealthView,
    IdentityView,
    InitiativeStateView,
    MemorySummaryView,
    ReflectionSummaryView,
    RelationshipSummaryView,
    RuntimeStateView,
    YuyiDashboardSnapshot,
)

logger = logging.getLogger(__name__)


def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
    try:
        return getattr(obj, key, default)
    except Exception:
        return default


def _safe_call(fn, *args: Any, **kwargs: Any) -> Any:
    """容错调用:异常时返回 None,不抛错。"""
    try:
        if fn is None:
            return None
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.debug("DashboardProvider safe_call 失败: %s", exc)
        return None


class DashboardProvider:
    """
    聚合 Provider,对外提供 Dashboard 完整快照。

    用法:
        provider = DashboardProvider()
        snapshot = provider.get_snapshot()
        # or
        overview = provider.get_overview()
    """

    def __init__(
        self,
        runtime_provider: Optional[Any] = None,
        self_model_provider: Optional[Any] = None,
    ) -> None:
        """
        Args:
            runtime_provider: 已注入的 RuntimeProvider(测试用);None 时懒加载
            self_model_provider: 已注入的 SelfModelProvider(测试用);None 时懒加载
        """
        self._lock = threading.RLock()
        self._runtime_provider = runtime_provider
        self._self_model_provider = self_model_provider

    # --------------------------------------------------------
    # 子 Provider 懒加载
    # --------------------------------------------------------
    def _get_runtime_provider(self) -> Optional[Any]:
        with self._lock:
            if self._runtime_provider is not None:
                return self._runtime_provider
            try:
                from src.admin.runtime_provider import get_runtime_provider
                self._runtime_provider = get_runtime_provider()
            except Exception as exc:  # noqa: BLE001
                logger.debug("DashboardProvider: RuntimeProvider 不可用: %s", exc)
                self._runtime_provider = None
            return self._runtime_provider

    def _get_self_model_provider(self) -> Optional[Any]:
        with self._lock:
            if self._self_model_provider is not None:
                return self._self_model_provider
            try:
                from src.admin.self_model_provider import get_self_model_provider
                self._self_model_provider = get_self_model_provider()
            except Exception as exc:  # noqa: BLE001
                logger.debug("DashboardProvider: SelfModelProvider 不可用: %s", exc)
                self._self_model_provider = None
            return self._self_model_provider

    # --------------------------------------------------------
    # 子视图构建(每个子方法容错)
    # --------------------------------------------------------
    def _build_runtime_state(self) -> RuntimeStateView:
        rp = self._get_runtime_provider()
        if rp is None:
            return RuntimeStateView(online=False, error="runtime_provider_unavailable")
        try:
            status = _safe_call(rp.get_status) or {}
            authority = _safe_call(rp.get_authority_status) or {}
            runtime = status.get("runtime", {}) if isinstance(status, dict) else {}
            return RuntimeStateView(
                online=bool(status.get("online", False)),
                initialized=bool(runtime.get("initialized", False)),
                is_running=bool(runtime.get("is_running", False)),
                authority=authority if isinstance(authority, dict) else {},
                error=status.get("bridge_error") if isinstance(status, dict) else None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("DashboardProvider._build_runtime_state 失败: %s", exc)
            return RuntimeStateView(online=False, error=str(exc))

    def _build_emotion(self) -> EmotionView:
        rp = self._get_runtime_provider()
        if rp is None:
            return EmotionView()
        try:
            summary = _safe_call(rp.get_emotion_summary) or {}
            if not isinstance(summary, dict):
                return EmotionView()
            return EmotionView(
                primary=str(summary.get("current", "unknown") or "unknown"),
                valence=float(summary.get("valence", 0.0) or 0.0),
                arousal=float(summary.get("arousal", 0.0) or 0.0),
                intensity=float(summary.get("intensity", 0.0) or 0.0),
                recent=list(summary.get("recent", []) or []),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("DashboardProvider._build_emotion 失败: %s", exc)
            return EmotionView()

    def _build_memory_summary(self) -> MemorySummaryView:
        rp = self._get_runtime_provider()
        if rp is None:
            return MemorySummaryView()
        try:
            summary = _safe_call(rp.get_memory_summary) or {}
            if not isinstance(summary, dict):
                return MemorySummaryView()
            return MemorySummaryView(
                total=int(summary.get("total_count", 0) or 0),
                important=int(summary.get("important_count", 0) or 0),
                recent=list(summary.get("recent", []) or []),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("DashboardProvider._build_memory_summary 失败: %s", exc)
            return MemorySummaryView()

    def _build_growth_summary(self) -> GrowthSummaryView:
        rp = self._get_runtime_provider()
        if rp is None:
            return GrowthSummaryView()
        try:
            summary = _safe_call(rp.get_growth_summary) or {}
            if not isinstance(summary, dict):
                return GrowthSummaryView()
            metrics = summary.get("metrics", {}) if isinstance(summary, dict) else {}
            if not isinstance(metrics, dict):
                metrics = {}
            return GrowthSummaryView(
                stage=str(metrics.get("maturity", "unknown") or "unknown"),
                score=float(metrics.get("growth_score", 0.0) or 0.0),
                pending_proposals=0,
                approved=0,
                rejected=0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("DashboardProvider._build_growth_summary 失败: %s", exc)
            return GrowthSummaryView()

    def _build_relationship_summary(self) -> RelationshipSummaryView:
        """Phase 5.0-D:RelationshipProvider 尚未单独建立,本阶段返回 placeholder。"""
        return RelationshipSummaryView()

    def _build_goal_state(self) -> GoalStateView:
        """Phase 5.0-D:GoalProvider 尚未单独建立,本阶段返回 placeholder。"""
        return GoalStateView()

    def _build_initiative_state(self) -> InitiativeStateView:
        """Phase 5.0-D:InitiativeProvider 尚未单独建立,本阶段返回 placeholder。"""
        return InitiativeStateView()

    def _build_reflection_summary(self) -> ReflectionSummaryView:
        """Phase 5.0-D:ReflectionProvider 尚未单独建立,本阶段返回 placeholder。"""
        return ReflectionSummaryView()

    def _build_body_state(self) -> BodyStateView:
        """Phase 5.0-E:BodyProvider 尚未建立,本阶段返回 placeholder。"""
        return BodyStateView()

    def _build_identity(self) -> IdentityView:
        smp = self._get_self_model_provider()
        if smp is None:
            return IdentityView()
        try:
            identity = _safe_call(smp.get_identity) or {}
            if not isinstance(identity, dict):
                return IdentityView()
            return IdentityView(
                name=str(identity.get("identity_name", "") or ""),
                anchor=str(identity.get("anchor", "") or ""),
                stable=bool(identity.get("stable", False)),
                raw=identity,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("DashboardProvider._build_identity 失败: %s", exc)
            return IdentityView()

    def _build_health(self, runtime: RuntimeStateView) -> HealthView:
        issues: List[str] = []
        score = 100
        if not runtime.online:
            issues.append("runtime_offline")
            score -= 30
        if runtime.error:
            issues.append("runtime_bridge_error")
            score -= 20
        if not runtime.authority.get("memory_store", False):
            issues.append("memory_store_unavailable")
            score -= 10
        if not runtime.authority.get("emotion_manager", False):
            issues.append("emotion_manager_unavailable")
            score -= 10
        if not runtime.authority.get("personality_resolver", False):
            issues.append("personality_resolver_unavailable")
            score -= 10
        score = max(0, min(100, score))
        if score >= 80:
            status = "healthy"
        elif score >= 50:
            status = "degraded"
        elif score > 0:
            status = "critical"
        else:
            status = "offline"
        return HealthView(status=status, score=score, issues=issues)

    # --------------------------------------------------------
    # 对外接口
    # --------------------------------------------------------
    def get_snapshot(self) -> YuyiDashboardSnapshot:
        """获取完整 Dashboard 快照。"""
        runtime = self._build_runtime_state()
        snapshot = YuyiDashboardSnapshot(
            runtime_state=runtime,
            identity=self._build_identity(),
            emotion=self._build_emotion(),
            memory_summary=self._build_memory_summary(),
            growth_summary=self._build_growth_summary(),
            relationship_summary=self._build_relationship_summary(),
            goal_state=self._build_goal_state(),
            initiative_state=self._build_initiative_state(),
            reflection_summary=self._build_reflection_summary(),
            body_state=self._build_body_state(),
            health=self._build_health(runtime),
        )
        return snapshot

    def get_overview(self) -> Dict[str, Any]:
        """获取 Overview 页数据(精简版,用于首页)。"""
        snapshot = self.get_snapshot()
        rs = snapshot.runtime_state
        health = snapshot.health
        return {
            "online": rs.online,
            "runtime_status": {
                "initialized": rs.initialized,
                "is_running": rs.is_running,
                "authority": rs.authority,
            },
            "emotion": {
                "primary": snapshot.emotion.primary,
                "valence": snapshot.emotion.valence,
                "arousal": snapshot.emotion.arousal,
                "intensity": snapshot.emotion.intensity,
            },
            "current_goal": snapshot.goal_state.current,
            "current_interest": (
                snapshot.initiative_state.recent[0]
                if snapshot.initiative_state.recent
                else None
            ),
            "recent_events": self._get_recent_events_summary(limit=10),
            "health": {
                "status": health.status,
                "score": health.score,
                "issues": health.issues,
            },
        }

    def _get_recent_events_summary(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        从 IntegrationEventLog 获取最近事件摘要。
        Phase 5.0-D2 EventHub 集成通过 EventHub 进行;本阶段直接尝试 RuntimeIntegrationHost。
        """
        events: List[Dict[str, Any]] = []
        try:
            from src.runtime.integration.runtime_integration_host import (
                RuntimeIntegrationHost,
            )
        except Exception:
            return events
        try:
            host = _safe_call(RuntimeIntegrationHost.get_default) \
                if hasattr(RuntimeIntegrationHost, "get_default") else None
        except Exception:
            host = None
        if host is None:
            return events
        try:
            event_log = _safe_get(host, "event_log")
            if event_log is None:
                return events
            raw_events = _safe_call(event_log.events, limit=limit) or []
            for ev in raw_events:
                events.append({
                    "event_id": _safe_get(ev, "event_id", ""),
                    "event_type": _safe_get(ev, "event_type", ""),
                    "source": _safe_get(ev, "source", ""),
                    "timestamp": _safe_get(ev, "timestamp", ""),
                })
        except Exception as exc:  # noqa: BLE001
            logger.debug("DashboardProvider._get_recent_events_summary 失败: %s", exc)
        return events


# ============================================================
# 模块级单例
# ============================================================
_provider_instance: Optional[DashboardProvider] = None
_provider_lock = threading.Lock()


def get_dashboard_provider() -> DashboardProvider:
    """获取 DashboardProvider 单例(懒加载)。"""
    global _provider_instance
    if _provider_instance is None:
        with _provider_lock:
            if _provider_instance is None:
                _provider_instance = DashboardProvider()
    return _provider_instance


def reset_dashboard_provider_for_testing() -> None:
    """测试用:重置单例。"""
    global _provider_instance
    with _provider_lock:
        _provider_instance = None


__all__ = [
    "DashboardProvider",
    "get_dashboard_provider",
    "reset_dashboard_provider_for_testing",
]
