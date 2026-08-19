# -*- coding: utf-8 -*-
"""
yuyi_desktop/services/life_snapshot_service.py

Phase D.5 —— LifeSnapshot 聚合 Service。

架构原则(避免变成 Orchestrator 大杂烩):
- 三段式拆分对外接口:get_core / get_history / get_activity
  * get_core     : identity + online + personality + emotion → 10s (Live2D 只订阅这段)
  * get_history  : growth + memory + evolution events → 30s (Archive 主要使用)
  * get_activity : initiative + runtime ticks → 5s (Dashboard 活动卡)
- get_snapshot  = 三段聚合(取最慢一段的并发),给 Dashboard 首页用
- 不直接 import src.*,不发起写请求
- 失败优雅降级,永不伪造数据
"""
from __future__ import annotations

import concurrent.futures as _cf
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from yuyi_desktop.core.life_snapshot import (
    CoreSnapshot,
    HistorySnapshot,
    LifeSnapshot,
    LifeSnapshotBuilder,
)
from yuyi_desktop.services.emotion_service import EmotionService, get_emotion_service
from yuyi_desktop.services.growth_service import GrowthService, get_growth_service
from yuyi_desktop.services.initiative_service import (
    InitiativeService,
    get_initiative_service,
)
from yuyi_desktop.services.memory_service import MemoryService, get_memory_service
from yuyi_desktop.services.personality_service import (
    PersonalityService,
    get_personality_service,
)
from yuyi_desktop.services.runtime_service import RuntimeService, get_runtime_service

logger = logging.getLogger(__name__)


class LifeSnapshotService:
    """LifeSnapshot 聚合服务(三段式,不变成 Orchestrator)。"""

    def __init__(
        self,
        personality: Optional[PersonalityService] = None,
        runtime: Optional[RuntimeService] = None,
        memory: Optional[MemoryService] = None,
        growth: Optional[GrowthService] = None,
        initiative: Optional[InitiativeService] = None,
        emotion: Optional[EmotionService] = None,
        connection: Optional[Any] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._personality = personality if personality is not None else get_personality_service()
        self._runtime = runtime if runtime is not None else get_runtime_service()
        self._memory = memory if memory is not None else get_memory_service()
        self._growth = growth if growth is not None else get_growth_service()
        self._initiative = initiative if initiative is not None else get_initiative_service()
        self._emotion = emotion if emotion is not None else get_emotion_service()
        self._connection = connection  # 允许注入,None 时在调用处由 Dashboard 传入
        # 单独的并发锁(避免和 self._lock 混用跨线程死锁,同 D.2.8 修复)
        self._fetch_lock = threading.Lock()

    # ================================================================
    # 对外:三段独立接口(未来 Live2D/Archive/Activity 独立订阅)
    # ================================================================
    def get_core(
        self,
        connection_status: Optional[Dict[str, Any]] = None,
        timeout_s: float = 12.0,
    ) -> CoreSnapshot:
        """Core:身份 / 在线 / 人格 / 情绪(建议 10s 刷新)。"""
        t0 = time.monotonic()
        raw: Dict[str, Any] = {}
        try:
            targets_core = [
                ("personality_overview", lambda: self._personality.get_overview()),
                ("personality_traits", lambda: self._personality.get_traits()),
                ("personality_evolution", lambda: self._personality.get_evolution()),
                ("runtime_overview", lambda: self._runtime.get_overview()),
                ("runtime_health", lambda: self._runtime.get_health()),
                ("emotion_overview", lambda: self._emotion.get_overview()),
            ]
            results = self._run_parallel(targets_core, timeout_s=timeout_s, pool_prefix="core-fetch")
            raw.update(results)
            if connection_status is not None and isinstance(connection_status, dict):
                raw["connection"] = connection_status
        except Exception as exc:  # noqa: BLE001
            logger.warning("[LifeSnapshotService] get_core 异常: %s", exc)
        snap_full = LifeSnapshotBuilder.build(raw, elapsed_ms=(time.monotonic() - t0) * 1000.0)
        return snap_full.core

    def get_history(
        self,
        timeout_s: float = 22.0,
    ) -> HistorySnapshot:
        """History:Growth / Memory / Evolution 事件(建议 30s 刷新)。

        D.6.2: 新增 6 个 SelfModel / Growth 数据源,给 Builder 产出存在记录用。
        """
        t0 = time.monotonic()
        raw: Dict[str, Any] = {}
        try:
            targets_history = [
                ("growth_overview", lambda: self._growth.get_overview()),
                ("growth_recent", lambda: self._growth.get_recent(limit=20)),
                ("memory_overview", lambda: self._memory.get_overview()),
                ("memory_recent", lambda: self._memory.list_recent(limit=20)),
                # Evolution 属于 personality 域,但语义上是"历史变化"的一部分,放在 history 里
                ("personality_evolution", lambda: self._personality.get_evolution()),
                # ---- Phase D.6.2: 存在记录 6 个数据源(从 D.6.0 Gateway 只读接口拿) ----
                (
                    "selfmodel_beliefs_items",
                    lambda: self._personality.get_beliefs(limit=100),
                ),
                (
                    "selfmodel_history_items",
                    lambda: self._personality.get_self_history(limit=200),
                ),
                (
                    "selfmodel_reflections_items",
                    lambda: self._personality.get_reflections(limit=100),
                ),
                (
                    "selfmodel_stable_traits_items",
                    lambda: self._personality.get_stable_traits(limit=100),
                ),
                (
                    "personality_evolution_v2_items",
                    lambda: self._personality.get_evolution_v2(limit=200),
                ),
                (
                    "growth_proposals_v2_items",
                    lambda: self._growth.get_proposals_v2(
                        status=None, limit=200
                    ),
                ),
            ]
            results = self._run_parallel(targets_history, timeout_s=timeout_s, pool_prefix="hist-fetch")
            raw.update(results)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[LifeSnapshotService] get_history 异常: %s", exc)
        snap_full = LifeSnapshotBuilder.build(raw, elapsed_ms=(time.monotonic() - t0) * 1000.0)
        return snap_full.history

    def get_activity(
        self,
        timeout_s: float = 10.0,
    ) -> Dict[str, Any]:
        """Activity:Initiative / Runtime 事件(建议 5s 刷新)。

        返回 LifeSnapshotBuilder.build 后 activity 部分的数据字典,
        同时也会包含 Builder 产生的 activity.activity_q / .last_action / .initiative_count。
        """
        t0 = time.monotonic()
        raw: Dict[str, Any] = {}
        try:
            targets_activity = [
                ("initiative_overview", lambda: self._initiative.get_overview()),
                ("initiative_actions", lambda: self._initiative.get_actions(limit=5)),
                ("runtime_overview", lambda: self._runtime.get_overview()),
            ]
            results = self._run_parallel(targets_activity, timeout_s=timeout_s, pool_prefix="act-fetch")
            raw.update(results)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[LifeSnapshotService] get_activity 异常: %s", exc)
        snap_full = LifeSnapshotBuilder.build(raw, elapsed_ms=(time.monotonic() - t0) * 1000.0)
        return {
            "activity": snap_full.activity,
            "history_events_fallback": snap_full.history.recent_events,
        }

    # ================================================================
    # 对外:Dashboard 首页统一聚合入口
    # ================================================================
    def get_snapshot(
        self,
        connection_status: Optional[Dict[str, Any]] = None,
        timeout_s: float = 25.0,
    ) -> LifeSnapshot:
        """获取聚合 LifeSnapshot(Dashboard 首页用)。

        并发 13 个端点,全局超时 25s,任何一个端点异常不扩散(降级为 {})。
        """
        t0 = time.monotonic()
        raw: Dict[str, Any] = {}
        try:
            # 关键:不在 self._lock 内启动 ThreadPoolExecutor,避免 RLock 跨线程死锁
            # (D.2.8 PersonalityService 已有经验)
            targets_all: List[Tuple[str, Any]] = [
                ("personality_overview", lambda: self._personality.get_overview()),
                ("personality_traits", lambda: self._personality.get_traits()),
                ("personality_evolution", lambda: self._personality.get_evolution()),
                ("runtime_overview", lambda: self._runtime.get_overview()),
                ("runtime_health", lambda: self._runtime.get_health()),
                ("memory_overview", lambda: self._memory.get_overview()),
                ("memory_recent", lambda: self._memory.list_recent(limit=5)),
                ("growth_overview", lambda: self._growth.get_overview()),
                ("growth_recent", lambda: self._growth.get_recent(limit=5)),
                ("initiative_overview", lambda: self._initiative.get_overview()),
                ("initiative_actions", lambda: self._initiative.get_actions(limit=3)),
                ("emotion_overview", lambda: self._emotion.get_overview()),
                # ---- Phase D.6.2: ExistenceTimeline 6 个数据源(并发拉取,失败降级为空列表) ----
                (
                    "selfmodel_beliefs_items",
                    lambda: self._personality.get_beliefs(limit=100),
                ),
                (
                    "selfmodel_history_items",
                    lambda: self._personality.get_self_history(limit=200),
                ),
                (
                    "selfmodel_reflections_items",
                    lambda: self._personality.get_reflections(limit=100),
                ),
                (
                    "selfmodel_stable_traits_items",
                    lambda: self._personality.get_stable_traits(limit=100),
                ),
                (
                    "personality_evolution_v2_items",
                    lambda: self._personality.get_evolution_v2(limit=200),
                ),
                (
                    "growth_proposals_v2_items",
                    lambda: self._growth.get_proposals_v2(status=None, limit=200),
                ),
            ]
            results = self._run_parallel(targets_all, timeout_s=timeout_s, pool_prefix="life-fetch")
            raw.update(results)
            if connection_status is not None and isinstance(connection_status, dict):
                raw["connection"] = connection_status
        except Exception as exc:  # noqa: BLE001
            logger.exception("[LifeSnapshotService] get_snapshot 聚合异常: %s", exc)
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        return LifeSnapshotBuilder.build(raw, elapsed_ms=elapsed_ms)

    # ================================================================
    # 内部:并发执行工具(与 D.2.8 死锁修复模式对齐)
    # ================================================================
    def _run_parallel(
        self,
        targets: List[Tuple[str, Any]],
        timeout_s: float,
        pool_prefix: str,
    ) -> Dict[str, Any]:
        """并发执行多个 fn,返回 {name: data}。所有异常降级为 {}。

        注意:
        - ThreadPoolExecutor 在 self._fetch_lock 保护下创建,但不在 self._lock 下,
          避免 RLock 跨线程死锁。
        - 每个 fn 独立捕获异常,永不扩散。
        """
        if not targets:
            return {}

        def _safe(name: str, fn) -> Tuple[str, Dict[str, Any], Optional[str]]:
            t_s = time.monotonic()
            try:
                data = fn()
                logger.debug("[LifeSnapshotService] %s done %.0fms", name, (time.monotonic() - t_s) * 1000.0)
                return name, data if isinstance(data, (dict, list)) else {}, None
            except Exception as exc:  # noqa: BLE001
                logger.warning("[LifeSnapshotService] %s 降级: %s", name, exc)
                return name, {}, f"{type(exc).__name__}: {exc}"

        results: Dict[str, Any] = {}
        max_workers = max(2, min(8, len(targets)))
        with self._fetch_lock:
            try:
                with _cf.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=pool_prefix) as pool:
                    futures = [pool.submit(_safe, name, fn) for name, fn in targets]
                    for fut in _cf.as_completed(futures, timeout=timeout_s):
                        try:
                            name, data, err = fut.result()
                        except _cf.TimeoutError:
                            continue
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("[LifeSnapshotService] future 失败: %s", exc)
                            continue
                        if isinstance(data, (dict, list)):
                            results[name] = data
            except Exception as exc:  # noqa: BLE001
                logger.warning("[LifeSnapshotService] 线程池异常: %s", exc)
        return results


# 单例(与 6 个 Service 风格对齐)
_inst_life: Optional[LifeSnapshotService] = None
_inst_lock2 = threading.Lock()


def get_life_snapshot_service() -> LifeSnapshotService:
    global _inst_life
    with _inst_lock2:
        if _inst_life is None:
            _inst_life = LifeSnapshotService()
    return _inst_life


__all__ = ["LifeSnapshotService", "get_life_snapshot_service"]
