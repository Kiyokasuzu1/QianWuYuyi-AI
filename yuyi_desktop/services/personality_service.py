# -*- coding: utf-8 -*-
"""
yuyi_desktop/services/personality_service.py

Phase C.10.2 / C.10.4 —— Personality Service(远程 + 缓存 + 事件)

通过 RemoteProviderBridge 访问 Yuyi Server API。
不直接 import 任何 src.* 模块。

约束(强):
- 不允许 import 任何 src.* 业务模块
- 禁止调用任何写接口
- 禁止触发人格演化 / 演化历史写入
- 禁止调用 resolve / apply / 任何修改流程
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from yuyi_desktop.core.cache.remote_snapshot_cache import (
    RemoteSnapshotCache,
    get_remote_snapshot_cache,
)
from yuyi_desktop.core.events.event_bus import (
    EventTypes,
    get_event_bus,
)
from yuyi_desktop.core.remote_provider_bridge import (
    RemoteProviderBridge,
    get_remote_provider_bridge,
)
from yuyi_desktop.core.schema_validator import (
    SchemaValidator,
    get_schema_validator,
)

logger = logging.getLogger(__name__)


CACHE_KEY = "personality.snapshot"


class PersonalityService:
    """Personality / SelfModel 数据只读服务(远程 + 缓存 + 事件)。"""

    def __init__(
        self,
        bridge: Optional[RemoteProviderBridge] = None,
        cache: Optional[RemoteSnapshotCache] = None,
        schema_validator: Optional[SchemaValidator] = None,
        event_bus: Optional[Any] = None,
        enable_schema_check: bool = True,
        enable_events: bool = True,
    ) -> None:
        self._lock = threading.RLock()
        self._bridge = bridge if bridge is not None else get_remote_provider_bridge()
        self._cache = cache if cache is not None else get_remote_snapshot_cache()
        self._schema_validator = (
            schema_validator if schema_validator is not None
            else get_schema_validator()
        )
        self._enable_schema_check = bool(enable_schema_check)
        self._event_bus = event_bus if event_bus is not None else get_event_bus()
        self._enable_events = bool(enable_events)

    # --------------------------------------------------------
    # 旧 API
    # --------------------------------------------------------
    def get_snapshot_envelope(self) -> Dict[str, Any]:
        with self._lock:
            return self._bridge.get_personality_snapshot()

    def get_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return self._bridge.get_personality_snapshot_data()

    def get_selfmodel_envelope(self) -> Dict[str, Any]:
        with self._lock:
            return self._bridge.get_selfmodel_snapshot()

    def get_selfmodel(self) -> Dict[str, Any]:
        with self._lock:
            return self._bridge.get_selfmodel_snapshot_data()

    def get_personality_status_v2(self) -> Dict[str, Any]:
        with self._lock:
            return self._bridge.get_personality_status_v2_data()

    def get_selfmodel_status_v2(self) -> Dict[str, Any]:
        with self._lock:
            return self._bridge.get_selfmodel_status_v2_data()

    def get_traits(self) -> List[Any]:
        with self._lock:
            return self._bridge.get_personality_traits_data()

    def get_evolution(self) -> List[Any]:
        with self._lock:
            return self._bridge.get_personality_evolution_data()

    # --------------------------------------------------------
    # Phase D.6.0: SelfModel 正式列表 + Personality Evolution 正式版
    # 所有方法只读,不抛错,失败返回空 list/dict
    # --------------------------------------------------------

    @staticmethod
    def _extract_items_from_envelope(envelope: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从 Bridge 返回 envelope 中安全抽取 items list。

        注意! 不调用 bridge 的 *_data() 方法,那些方法会 re-fetch 用 DEFAULT 参数,
        覆盖用户传入的自定义参数(domain/limit/min_stability 等)。
        """
        if not isinstance(envelope, dict):
            return []
        if not envelope.get("success", False):
            return []
        data = envelope.get("data", {}) or {}
        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for key in ("items", "events", "history", "timeline", "proposals", "beliefs"):
                candidate = data.get(key)
                if isinstance(candidate, list):
                    items = candidate
                    break
        return [d for d in items if isinstance(d, dict)]

    # ---- SelfModel Beliefs ----
    def get_beliefs(
        self,
        domain: Optional[str] = None,
        min_confidence: float = 0.0,
        include_inactive: bool = True,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """D.6.0: 核心信念列表(稳定信念,≠ 即时人格)。"""
        with self._lock:
            try:
                envelope = self._bridge.get_selfmodel_beliefs(
                    domain=domain,
                    min_confidence=min_confidence,
                    include_inactive=include_inactive,
                    limit=limit,
                )
                return self._extract_items_from_envelope(envelope)
            except Exception:  # noqa: BLE001
                return []

    # ---- SelfModel History ----
    def get_self_history(
        self,
        event_type: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """D.6.0: SelfModel 历史事件(pcr_applied / belief_formed / identity_updated 等)。Growth Timeline 主数据源之一。"""
        with self._lock:
            try:
                envelope = self._bridge.get_selfmodel_history(
                    event_type=event_type,
                    since=since,
                    until=until,
                    limit=limit,
                )
                return self._extract_items_from_envelope(envelope)
            except Exception:  # noqa: BLE001
                return []

    # ---- SelfModel Reflections ----
    def get_reflections(
        self,
        trigger_source: Optional[str] = None,
        reflection_type: Optional[str] = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """D.6.0: Self 反思(SelfPhase 内省产出)。"""
        with self._lock:
            try:
                envelope = self._bridge.get_selfmodel_reflections(
                    trigger_source=trigger_source,
                    reflection_type=reflection_type,
                    min_confidence=min_confidence,
                    limit=limit,
                )
                return self._extract_items_from_envelope(envelope)
            except Exception:  # noqa: BLE001
                return []

    # ---- SelfModel Stable Traits (≠ Resolver 即时 Traits!) ----
    def get_stable_traits(
        self,
        min_stability: float = 0.0,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """D.6.0: SelfModelV3 稳定特质。

        ❗️ 强约束: 本方法 ONLY 调用 bridge.get_selfmodel_stable_traits()。
        绝对不允许从 get_traits() (Resolver.current) 复制值过来冒充稳定特质。
        StablePortraitCard / Archive 稳定人格部分 必须用本方法。
        """
        with self._lock:
            try:
                envelope = self._bridge.get_selfmodel_stable_traits(
                    min_stability=min_stability,
                    limit=limit,
                    offset=offset,
                )
                return self._extract_items_from_envelope(envelope)
            except Exception:  # noqa: BLE001
                return []

    # ---- Personality Evolution v2 (正式版) ----
    def get_evolution_v2(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 100,
        sources: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """D.6.0: 人格演化时间线(正式版,直接调 provider.get_evolution_timeline)。Growth Timeline 主数据源之二。"""
        with self._lock:
            try:
                envelope = self._bridge.get_personality_evolution_v2(
                    start=start,
                    end=end,
                    limit=limit,
                    sources=sources,
                )
                return self._extract_items_from_envelope(envelope)
            except Exception:  # noqa: BLE001
                return []

    # --------------------------------------------------------
    # Phase D.2.3: Growth 跨服务只读访问 (Personality Tab 展示需要)
    # 直接走 RemoteProviderBridge -> /api/v1/growth/status
    # 不 import src/** / 不调用任何写接口
    # --------------------------------------------------------
    def get_growth_status_v2_data(self) -> Dict[str, Any]:
        """Growth 状态摘要 (来自 /api/v1/growth/status)。"""
        with self._lock:
            try:
                return self._bridge.get_growth_status_v2_data() or {}
            except Exception:  # noqa: BLE001
                return {}

    def get_growth_recent_data_safe(self) -> List[Dict[str, Any]]:
        """Growth 最近记录 (来自 /api/v1/growth/recent)。

        安全包装: 失败时返回空列表, 不抛错。
        """
        with self._lock:
            try:
                data = self._bridge.get_growth_recent_data()
                if isinstance(data, list):
                    return [d for d in data if isinstance(d, dict)]
                if isinstance(data, dict):
                    items = data.get("items") or data.get("history") or []
                    if isinstance(items, list):
                        return [d for d in items if isinstance(d, dict)]
                return []
            except Exception:  # noqa: BLE001
                return []

    def get_overview(self) -> Dict[str, Any]:
        """获取 personality + selfmodel 概览。

        Phase D.2.4: 改为并行执行 5 个独立 HTTP 请求,避免 30s+ 串行等待。
        并行池只用于发起请求,不在 desktop 主线程,不影响 UI。

        Phase D.2.8 关键修复: 死锁修复
        - 不能在持锁状态下启动子线程,子线程调用 service 子方法时会
          因 RLock 线程局部重入失败而阻塞,导致外层 as_completed 永远等不到结果。
        - 修复方式: 子任务封装为 lambda 形式,lambda 内部各自获取 RLock;
          ThreadPoolExecutor 放在锁外执行,确保子线程独立获取锁,避免死锁。
        """
        import time as _t
        import concurrent.futures as _cf
        logger.info("[PersonalityService] get_overview start (parallel, deadlock-safe)")
        t0 = _t.monotonic()
        try:
            # ---------- 并行 5 个独立请求 ----------
            # 关键: 子任务通过 lambda 调用,不在外层持锁状态下启动。
            # 这里的 lambda 直接调用原始 service 方法,每个方法内部自己
            # 用 `with self._lock` 加锁。子线程独立获取 RLock,不会与
            # 外层线程的死锁。
            fetch_targets = [
                ("ps_v2", self.get_personality_status_v2),
                ("sm_v2", self.get_selfmodel_status_v2),
                ("envelope", self.get_snapshot_envelope),
                ("snapshot", self.get_snapshot),
                ("sm", self.get_selfmodel),
            ]
            fetched: Dict[str, Any] = {}
            # 注意: 必须在锁外启动 ThreadPoolExecutor,避免 RLock 跨线程死锁
            with _cf.ThreadPoolExecutor(
                max_workers=5, thread_name_prefix="psv-fetch"
            ) as pool:
                futures = {
                    pool.submit(fn): name for name, fn in fetch_targets
                }
                # 增加超时到 25s,避免某个端点长时间挂起导致永远等不到
                for fut in _cf.as_completed(futures, timeout=25.0):
                    name = futures[fut]
                    try:
                        fetched[name] = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "[PersonalityService] %s fetch failed: %s",
                            name, exc,
                        )
                        fetched[name] = {}

            ps_v2 = fetched.get("ps_v2") or {}
            sm_v2 = fetched.get("sm_v2") or {}
            envelope = fetched.get("envelope") or {}
            snapshot = fetched.get("snapshot") or {}
            sm = fetched.get("sm") or {}

            logger.info(
                "[PersonalityService] personality status response"
                " available=%s degraded=%s error=%s",
                bool(ps_v2.get("available", False)) if isinstance(ps_v2, dict) else False,
                bool(ps_v2.get("degraded", False)) if isinstance(ps_v2, dict) else False,
                str(ps_v2.get("error", "")) if isinstance(ps_v2, dict) else "",
            )
            logger.info(
                "[PersonalityService] selfmodel status response"
                " identity_name=%s identity_id=%s stable=%s source=%s",
                str(sm_v2.get("identity_name", ""))
                if isinstance(sm_v2, dict) else "",
                str(sm_v2.get("identity_id", ""))
                if isinstance(sm_v2, dict) else "",
                bool(sm_v2.get("stable", False))
                if isinstance(sm_v2, dict) else False,
                str(sm_v2.get("source", ""))
                if isinstance(sm_v2, dict) else "",
            )

            v2_snapshot = ps_v2.get("snapshot", {}) if isinstance(ps_v2, dict) else {}
            identity_name = str(
                v2_snapshot.get("identity_name")
                or sm_v2.get("identity_name")
                or snapshot.get("identity_name")
                or sm.get("identity_name")
                or ""  # P1-2: 不写死人格名,后端无数据时为空
            )
            version = (
                ps_v2.get("evolution_version")
                or v2_snapshot.get("version")
                or sm_v2.get("version")
                or snapshot.get("version")
                or sm.get("version")
            )
            stable = bool(
                v2_snapshot.get("stable", False)
                or snapshot.get("stable", False)
                or sm.get("stable", False)
                or sm_v2.get("health") == "healthy"
            )
            result = {
                "identity_name": identity_name,
                "version": version,
                "stable": stable,
                "personality_v2": ps_v2,
                "selfmodel_v2": sm_v2,
                "available": bool(envelope.get("success", False)) or bool(ps_v2.get("available", False)),
                "degraded": bool(envelope.get("degraded", False)),
            }
            elapsed = (_t.monotonic() - t0) * 1000.0
            logger.info(
                "[PersonalityService] overview done in %.0fms"
                " identity=%s available=%s degraded=%s",
                elapsed,
                str(result.get("identity_name", "")),
                bool(result.get("available", False)),
                bool(result.get("degraded", False)),
            )
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "[PersonalityService] get_overview failed: %s", exc
            )
            raise

    # --------------------------------------------------------
    # C.10.4
    # --------------------------------------------------------
    def refresh(self) -> Dict[str, Any]:
        with self._lock:
            envelope = self._bridge.get_personality_snapshot()
            return self._handle_envelope(envelope)

    def get_snapshot_with_cache(self) -> Dict[str, Any]:
        with self._lock:
            envelope = self._bridge.get_personality_snapshot()
            self._handle_envelope(envelope)
            return self._build_view(envelope)

    def get_cached_snapshot(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._cache.get_data(CACHE_KEY, allow_stale=True)

    def get_snapshot_status(self) -> Dict[str, Any]:
        with self._lock:
            return self._cache.get_status(CACHE_KEY)

    def _handle_envelope(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(envelope, dict):
            envelope = {
                "success": False, "data": {}, "error": "envelope_not_dict",
                "degraded": True, "schema_version": "", "latency_ms": 0.0,
            }
        if envelope.get("success", False):
            if self._enable_schema_check:
                result = self._schema_validator.validate(envelope)
                if not result.ok:
                    self._publish(
                        EventTypes.SCHEMA_CHANGED,
                        {
                            "domain": "personality",
                            "expected": result.expected,
                            "actual": result.actual,
                            "reason": result.reason,
                        },
                    )
                    return {
                        "success": False, "data": {},
                        "error": f"schema_mismatch: {result.reason}",
                        "degraded": True,
                        "schema_version": str(result.actual or ""),
                        "latency_ms": float(envelope.get("latency_ms", 0.0)),
                    }
            latency = float(envelope.get("latency_ms", 0.0) or 0.0)
            self._cache.put_envelope(CACHE_KEY, envelope)
            self._publish(
                EventTypes.PERSONALITY_UPDATED,
                {
                    "domain": "personality",
                    "latency_ms": latency,
                    "cache_key": CACHE_KEY,
                },
            )
        else:
            self._publish(
                EventTypes.CONNECTION_CHANGED,
                {
                    "domain": "personality",
                    "success": False,
                    "error": str(envelope.get("error", "")),
                },
            )
        return envelope

    def _build_view(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        success = bool(envelope.get("success", False)) if isinstance(envelope, dict) else False
        cache_status = self._cache.get_status(CACHE_KEY)
        if success:
            data = envelope.get("data", {}) if isinstance(envelope, dict) else {}
            return {
                "data": data if isinstance(data, dict) else {},
                "source": "live",
                "online": True,
                "last_update_time": str(envelope.get("timestamp", "")),
                "is_stale": False,
                "degraded": bool(envelope.get("degraded", False)),
                "offline": False,
                "schema_version": str(envelope.get("schema_version", "")),
            }
        cached = self._cache.get_data(CACHE_KEY, allow_stale=True)
        return {
            "data": cached if isinstance(cached, dict) else {},
            "source": "cache" if cached is not None else "empty",
            "online": False,
            "last_update_time": str(cache_status.get("timestamp", "")),
            "is_stale": bool(cache_status.get("is_stale", True)),
            "degraded": True,
            "offline": cached is None,
            "schema_version": str(cache_status.get("schema_version", "")),
        }

    def _publish(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        if not self._enable_events:
            return
        try:
            self._event_bus.publish_typed(
                event_type=event_type,
                source="service.personality",
                data=data if isinstance(data, dict) else {},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("PersonalityService: publish 失败: %s", exc)


_svc_instance: Optional[PersonalityService] = None
_svc_lock = threading.Lock()


def get_personality_service() -> PersonalityService:
    global _svc_instance
    if _svc_instance is None:
        with _svc_lock:
            if _svc_instance is None:
                _svc_instance = PersonalityService()
    return _svc_instance


def reset_personality_service_for_testing() -> None:
    global _svc_instance
    with _svc_lock:
        _svc_instance = None


__all__ = [
    "PersonalityService",
    "get_personality_service",
    "reset_personality_service_for_testing",
]
