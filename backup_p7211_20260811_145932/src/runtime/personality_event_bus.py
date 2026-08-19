"""
Phase 3.5.14: Personality Event Bus

人格生命系统的事件总线。

职责：
- 类型化事件发布（5 类：Memory / Growth / Reflection / Identity / Relationship）
- 订阅管理（按分类 / 事件类型 / 全局）
- 事件历史（bounded，支持重放与查询）
- 事件过滤
- 错误隔离（单个处理器失败不影响其他）
- 统计与快照（可审计）

设计原则：
- 不替换已有 EventBus（src/events/bus.py），作为独立模块
- 不引入新框架
- 所有变化可追踪、可审计、可恢复
- 默认关闭，通过 config 显式启用
- 持久化可选
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

from src.contracts.personality_event_schema import (
    ALL_CATEGORIES,
    CATEGORY_TYPE_PREFIX,
    EventBusSnapshot,
    EventFilter,
    PersonalityEvent,
    SubscriberInfo,
    infer_category,
)

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_CAPACITY = 500
DEFAULT_PERSIST_PATH = "data/event_bus/history.json"


class PersonalityEventBus:
    """
    人格生命系统事件总线。

    支持类型化事件、订阅、历史、过滤、统计。
    线程安全。
    """

    def __init__(
        self,
        history_capacity: int = DEFAULT_HISTORY_CAPACITY,
        persist_path: Optional[str] = None,
        auto_persist: bool = False,
    ):
        self._history_capacity = max(history_capacity, 0)
        self._persist_path = Path(persist_path) if persist_path else None
        self._auto_persist = auto_persist

        # 事件历史（bounded deque）
        self._history: Deque[PersonalityEvent] = deque(maxlen=self._history_capacity)

        # 订阅者：category -> list of SubscriberInfo
        self._subscribers: Dict[str, List[SubscriberInfo]] = {}
        # 全局订阅者（订阅所有事件）
        self._global_subscribers: List[SubscriberInfo] = []

        # 统计
        self._total_published = 0
        self._events_by_category: Dict[str, int] = {}
        self._events_by_severity: Dict[str, int] = {}
        self._events_by_type: Dict[str, int] = {}

        self._last_event_at: str = ""
        self._last_error: str = ""
        self._enabled: bool = True

        # 线程安全
        self._lock = threading.RLock()

        # 加载持久化历史
        if self._persist_path and self._persist_path.exists():
            self._load_history()

    # ============================================================
    # 启用/禁用
    # ============================================================

    def enable(self) -> None:
        with self._lock:
            self._enabled = True

    def disable(self) -> None:
        with self._lock:
            self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ============================================================
    # 订阅管理
    # ============================================================

    def subscribe(
        self,
        handler: Callable[[PersonalityEvent], Any],
        category: Optional[str] = None,
        event_types: Optional[List[str]] = None,
        handler_name: str = "",
    ) -> str:
        """
        订阅事件。

        Args:
            handler: 处理函数
            category: 订阅的分类（None 表示订阅所有分类）
            event_types: 订阅的事件类型列表（空表示订阅该分类下所有类型）
            handler_name: 处理函数名（用于调试）

        Returns:
            subscriber_id
        """
        with self._lock:
            info = SubscriberInfo(
                category=category,
                event_types=list(event_types) if event_types else [],
                handler_name=handler_name or getattr(handler, "__name__", "anonymous"),
            )
            # 关联 handler（info 自身不可序列化函数，单独维护映射）
            info_id = info.subscriber_id
            # 使用 info 的属性作为 key，handler 通过闭包引用
            # 这里将 handler 附加到 info 上（不参与 to_dict）
            info.__dict__["_handler"] = handler  # type: ignore

            if category is None:
                self._global_subscribers.append(info)
            else:
                self._subscribers.setdefault(category, []).append(info)

            logger.debug(f"订阅注册: {info_id} (category={category}, types={event_types})")
            return info_id

    def unsubscribe(self, subscriber_id: str) -> bool:
        """取消订阅"""
        with self._lock:
            # 全局
            for i, info in enumerate(self._global_subscribers):
                if info.subscriber_id == subscriber_id:
                    self._global_subscribers.pop(i)
                    return True
            # 分类
            for cat, infos in self._subscribers.items():
                for i, info in enumerate(infos):
                    if info.subscriber_id == subscriber_id:
                        infos.pop(i)
                        return True
            return False

    def unsubscribe_all(self) -> None:
        """清空所有订阅"""
        with self._lock:
            self._subscribers.clear()
            self._global_subscribers.clear()

    # ============================================================
    # 发布事件
    # ============================================================

    def publish(self, event: PersonalityEvent) -> bool:
        """
        发布事件。

        Args:
            event: 人格事件

        Returns:
            是否发布成功（即使部分处理器失败，只要事件入历史即视为成功）
        """
        if not self._enabled:
            return False

        if not event.validate():
            self._last_error = f"事件校验失败: {event.event_id}"
            logger.warning(self._last_error)
            return False

        with self._lock:
            # 加入历史
            self._history.append(event)
            self._total_published += 1

            # 更新统计
            self._events_by_category[event.category] = (
                self._events_by_category.get(event.category, 0) + 1
            )
            self._events_by_severity[event.severity] = (
                self._events_by_severity.get(event.severity, 0) + 1
            )
            self._events_by_type[event.event_type] = (
                self._events_by_type.get(event.event_type, 0) + 1
            )
            self._last_event_at = event.timestamp

            # 收集匹配的订阅者
            matched = self._collect_matching_subscribers(event)

        # 在锁外调用处理器（避免回调中再次发布导致死锁）
        for info in matched:
            self._invoke_handler(info, event)

        # 自动持久化
        if self._auto_persist and self._persist_path:
            try:
                self._save_history()
            except Exception as e:
                logger.warning(f"自动持久化事件历史失败: {e}")

        return True

    def publish_memory_event(
        self,
        event_type: str,
        source: str,
        payload: Optional[Dict[str, Any]] = None,
        related_ids: Optional[List[str]] = None,
        severity: str = "info",
        actor: str = "",
    ) -> PersonalityEvent:
        """便捷发布 Memory 事件"""
        from src.contracts.personality_event_schema import (
            CATEGORY_MEMORY,
            SEVERITY_INFO,
            make_memory_event,
        )
        if severity not in ("info", "notice", "warning", "critical"):
            severity = SEVERITY_INFO
        event = make_memory_event(
            event_type=event_type,
            source=source,
            payload=payload,
            related_ids=related_ids,
            severity=severity,
            actor=actor,
        )
        self.publish(event)
        return event

    def publish_growth_event(
        self,
        event_type: str,
        source: str,
        payload: Optional[Dict[str, Any]] = None,
        related_ids: Optional[List[str]] = None,
        severity: str = "info",
        actor: str = "",
        related_actor: str = "",
    ) -> PersonalityEvent:
        """便捷发布 Growth 事件"""
        from src.contracts.personality_event_schema import (
            SEVERITY_INFO,
            make_growth_event,
        )
        if severity not in ("info", "notice", "warning", "critical"):
            severity = SEVERITY_INFO
        event = make_growth_event(
            event_type=event_type,
            source=source,
            payload=payload,
            related_ids=related_ids,
            severity=severity,
            actor=actor,
            related_actor=related_actor,
        )
        self.publish(event)
        return event

    def publish_reflection_event(
        self,
        event_type: str,
        source: str,
        payload: Optional[Dict[str, Any]] = None,
        related_ids: Optional[List[str]] = None,
        severity: str = "info",
        actor: str = "",
    ) -> PersonalityEvent:
        """便捷发布 Reflection 事件"""
        from src.contracts.personality_event_schema import (
            SEVERITY_INFO,
            make_reflection_event,
        )
        if severity not in ("info", "notice", "warning", "critical"):
            severity = SEVERITY_INFO
        event = make_reflection_event(
            event_type=event_type,
            source=source,
            payload=payload,
            related_ids=related_ids,
            severity=severity,
            actor=actor,
        )
        self.publish(event)
        return event

    def publish_identity_event(
        self,
        event_type: str,
        source: str,
        payload: Optional[Dict[str, Any]] = None,
        related_ids: Optional[List[str]] = None,
        severity: str = "info",
        actor: str = "",
    ) -> PersonalityEvent:
        """便捷发布 Identity 事件"""
        from src.contracts.personality_event_schema import (
            SEVERITY_INFO,
            make_identity_event,
        )
        if severity not in ("info", "notice", "warning", "critical"):
            severity = SEVERITY_INFO
        event = make_identity_event(
            event_type=event_type,
            source=source,
            payload=payload,
            related_ids=related_ids,
            severity=severity,
            actor=actor,
        )
        self.publish(event)
        return event

    def publish_relationship_event(
        self,
        event_type: str,
        source: str,
        payload: Optional[Dict[str, Any]] = None,
        related_ids: Optional[List[str]] = None,
        severity: str = "info",
        actor: str = "",
    ) -> PersonalityEvent:
        """便捷发布 Relationship 事件"""
        from src.contracts.personality_event_schema import (
            SEVERITY_INFO,
            make_relationship_event,
        )
        if severity not in ("info", "notice", "warning", "critical"):
            severity = SEVERITY_INFO
        event = make_relationship_event(
            event_type=event_type,
            source=source,
            payload=payload,
            related_ids=related_ids,
            severity=severity,
            actor=actor,
        )
        self.publish(event)
        return event

    # ============================================================
    # 历史查询
    # ============================================================

    def get_history(
        self,
        limit: int = 100,
        offset: int = 0,
        event_filter: Optional[EventFilter] = None,
    ) -> List[PersonalityEvent]:
        """
        查询事件历史。

        Args:
            limit: 最大数量
            offset: 偏移量（从最新开始计算）
            event_filter: 过滤器

        Returns:
            事件列表（按时间倒序）
        """
        with self._lock:
            all_events = list(self._history)

        # 倒序（最新在前）
        all_events.reverse()

        # 应用过滤
        if event_filter:
            all_events = [e for e in all_events if event_filter.matches(e)]

        # 偏移
        if offset > 0:
            all_events = all_events[offset:]

        return all_events[:limit]

    def get_history_by_category(
        self,
        category: str,
        limit: int = 50,
    ) -> List[PersonalityEvent]:
        """按分类查询历史"""
        return self.get_history(
            limit=limit,
            event_filter=EventFilter(categories=[category]),
        )

    def get_history_by_type(
        self,
        event_type: str,
        limit: int = 50,
    ) -> List[PersonalityEvent]:
        """按事件类型查询历史"""
        return self.get_history(
            limit=limit,
            event_filter=EventFilter(event_types=[event_type]),
        )

    def get_history_by_related_id(
        self,
        related_id: str,
        limit: int = 50,
    ) -> List[PersonalityEvent]:
        """按相关 ID 查询历史（如 proposal_id / memory_id）"""
        return self.get_history(
            limit=limit,
            event_filter=EventFilter(related_id=related_id),
        )

    def get_event(self, event_id: str) -> Optional[PersonalityEvent]:
        """按 ID 查询事件"""
        with self._lock:
            for event in self._history:
                if event.event_id == event_id:
                    return event
        return None

    def clear_history(self) -> int:
        """清空历史，返回被清除的数量"""
        with self._lock:
            count = len(self._history)
            self._history.clear()
            # 不清除统计，保留累计值
            return count

    # ============================================================
    # 重放
    # ============================================================

    def replay_history(
        self,
        handler: Callable[[PersonalityEvent], Any],
        event_filter: Optional[EventFilter] = None,
        limit: int = 0,
    ) -> int:
        """
        重放历史事件给指定处理器（不影响订阅者统计）。

        Args:
            handler: 处理函数
            event_filter: 过滤器
            limit: 最大重放数量（0 表示全部）

        Returns:
            重放的事件数量
        """
        with self._lock:
            events = list(self._history)

        if event_filter:
            events = [e for e in events if event_filter.matches(e)]

        # 历史顺序（旧→新）
        if limit > 0:
            events = events[-limit:]

        count = 0
        for event in events:
            try:
                handler(event)
                count += 1
            except Exception as e:
                logger.warning(f"重放事件 {event.event_id} 失败: {e}")

        return count

    # ============================================================
    # 快照与统计
    # ============================================================

    def snapshot(self) -> EventBusSnapshot:
        """生成事件总线快照"""
        with self._lock:
            subscriber_infos = []
            for info in self._global_subscribers:
                d = info.to_dict()
                d.pop("_handler", None)
                subscriber_infos.append(d)
            for infos in self._subscribers.values():
                for info in infos:
                    d = info.to_dict()
                    d.pop("_handler", None)
                    subscriber_infos.append(d)

            return EventBusSnapshot(
                total_events_published=self._total_published,
                total_subscribers=len(self._global_subscribers)
                + sum(len(v) for v in self._subscribers.values()),
                history_size=len(self._history),
                history_capacity=self._history_capacity,
                events_by_category=dict(self._events_by_category),
                events_by_severity=dict(self._events_by_severity),
                events_by_type=dict(self._events_by_type),
                subscriber_infos=subscriber_infos,
                last_event_at=self._last_event_at,
                last_error=self._last_error,
                enabled=self._enabled,
            )

    def get_subscribers(self) -> List[Dict[str, Any]]:
        """获取所有订阅者信息"""
        with self._lock:
            result = []
            for info in self._global_subscribers:
                d = info.to_dict()
                d.pop("_handler", None)
                result.append(d)
            for infos in self._subscribers.values():
                for info in infos:
                    d = info.to_dict()
                    d.pop("_handler", None)
                    result.append(d)
            return result

    # ============================================================
    # 持久化
    # ============================================================

    def save_history(self) -> bool:
        """显式保存历史到磁盘"""
        if not self._persist_path:
            return False
        return self._save_history()

    def _save_history(self) -> bool:
        """内部保存实现"""
        if not self._persist_path:
            return False
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                data = {
                    "total_published": self._total_published,
                    "events_by_category": dict(self._events_by_category),
                    "events_by_severity": dict(self._events_by_severity),
                    "events_by_type": dict(self._events_by_type),
                    "last_event_at": self._last_event_at,
                    "history": [e.to_dict() for e in self._history],
                }
            with open(self._persist_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            self._last_error = f"保存历史失败: {e}"
            logger.error(self._last_error)
            return False

    def _load_history(self) -> None:
        """加载持久化历史"""
        if not self._persist_path or not self._persist_path.exists():
            return
        try:
            with open(self._persist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                self._total_published = data.get("total_published", 0)
                self._events_by_category = data.get("events_by_category", {})
                self._events_by_severity = data.get("events_by_severity", {})
                self._events_by_type = data.get("events_by_type", {})
                self._last_event_at = data.get("last_event_at", "")
                history_data = data.get("history", [])
                self._history.clear()
                for e_data in history_data:
                    self._history.append(PersonalityEvent.from_dict(e_data))
            logger.info(f"加载事件历史: {len(history_data)} 条")
        except Exception as e:
            self._last_error = f"加载历史失败: {e}"
            logger.warning(self._last_error)

    # ============================================================
    # 内部方法
    # ============================================================

    def _collect_matching_subscribers(
        self,
        event: PersonalityEvent,
    ) -> List[SubscriberInfo]:
        """收集匹配事件的所有订阅者"""
        matched: List[SubscriberInfo] = []

        # 全局订阅者
        for info in self._global_subscribers:
            if self._subscriber_matches(info, event):
                matched.append(info)

        # 分类订阅者
        cat_subs = self._subscribers.get(event.category, [])
        for info in cat_subs:
            if self._subscriber_matches(info, event):
                matched.append(info)

        return matched

    @staticmethod
    def _subscriber_matches(
        info: SubscriberInfo,
        event: PersonalityEvent,
    ) -> bool:
        """检查订阅者是否匹配事件"""
        # 全局订阅者（category=None）匹配所有
        if info.category is None:
            if not info.event_types:
                return True
            return event.event_type in info.event_types

        # 分类订阅者
        if info.category != event.category:
            return False
        if not info.event_types:
            return True
        return event.event_type in info.event_types

    def _invoke_handler(
        self,
        info: SubscriberInfo,
        event: PersonalityEvent,
    ) -> None:
        """调用订阅者处理器（错误隔离）"""
        handler = info.__dict__.get("_handler")
        if handler is None:
            return

        try:
            handler(event)
            info.call_count += 1
            info.last_called_at = event.timestamp
        except Exception as e:
            info.error_count += 1
            info.last_error = str(e)
            self._last_error = f"处理器 {info.subscriber_id} 失败: {e}"
            logger.warning(self._last_error)
