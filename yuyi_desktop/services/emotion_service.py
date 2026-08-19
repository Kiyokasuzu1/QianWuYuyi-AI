# -*- coding: utf-8 -*-
"""
yuyi_desktop/services/emotion_service.py

Phase D.5 —— Emotion Service 只读骨架。

严格遵守:
- 当后端 /api/v1/emotion/* 尚未暴露时,永远返回 available=False, mood=UNKNOWN
- 绝不根据 personality/initiatve 推断情绪(人格≠当前情绪)
- 未来 E.0 接入时,只改本文件内部 _fetch_* 实现,对外 API 不变
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class EmotionService:
    """Emotion 数据只读服务(骨架,不伪造)。"""

    def __init__(
        self,
        bridge: Optional[Any] = None,
        cache: Optional[Any] = None,
        schema_validator: Optional[Any] = None,
        event_bus: Optional[Any] = None,
        enable_schema_check: bool = True,
        enable_events: bool = True,
    ) -> None:
        self._lock = threading.RLock()
        # 注:目前 bridge 的 API 里没有 emotion 端点,仅保留注入位
        self._bridge = bridge
        self._cache = cache
        self._schema_validator = schema_validator
        self._event_bus = event_bus
        self._enable_schema_check = bool(enable_schema_check)
        self._enable_events = bool(enable_events)

    # --------------------------------------------------------
    # 公共 API(对外稳定,未来 E.0 接真实端点时内部改)
    # --------------------------------------------------------
    def get_overview(self) -> Dict[str, Any]:
        """情绪概览。

        D.5 当前:
          - 后端尚未暴露 /api/v1/emotion/overview -> 永远返回 available=False,
            mood=UNKNOWN, source=unknown, 不伪造值。
        E.0 接入后:
          - 从 bridge.get_emotion_overview() 返回真实数据。
        """
        with self._lock:
            return {
                "available": False,
                "degraded": False,
                "mood": "UNKNOWN",
                "intensity": 0.0,
                "cause": "",
                "source": "unknown",
                "error": "emotion_endpoint_not_exposed",
            }

    def get_snapshot_envelope(self) -> Dict[str, Any]:
        """统一 envelope 格式(供缓存/事件使用)。"""
        with self._lock:
            return {
                "success": False,
                "data": {
                    "mood": "UNKNOWN",
                    "intensity": 0.0,
                    "cause": "",
                },
                "error": "emotion_endpoint_not_exposed",
                "degraded": False,
                "schema_version": "",
                "latency_ms": 0.0,
            }


# 单例(与其他 6 个 Service 的 get_xxx_service 风格对齐)
_inst_emotion: Optional[EmotionService] = None
_inst_lock = threading.Lock()


def get_emotion_service() -> EmotionService:
    global _inst_emotion
    with _inst_lock:
        if _inst_emotion is None:
            _inst_emotion = EmotionService()
    return _inst_emotion


__all__ = ["EmotionService", "get_emotion_service"]
