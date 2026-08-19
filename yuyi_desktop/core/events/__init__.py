# -*- coding: utf-8 -*-
"""
yuyi_desktop/core/events/__init__.py

Phase C.10.4.5 —— Yuyi Desktop Event System

事件总线允许 Service 在数据更新时通知 UI 层,替代轮询。

事件流:
    Server Data
       ↓
    Service
       ↓
    Signal/Event
       ↓
    UI 更新

约束(强):
- 仅使用标准 threading,不依赖 PySide6(保持 core 层纯净)
- 任何订阅失败不应阻塞发布
- 历史事件可被订阅者获取(只读快照)
- UI 不主动轮询,只订阅事件
"""
from .event_bus import (
    DesktopEvent,
    EventBus,
    EventTypes,
    get_event_bus,
    reset_event_bus_for_testing,
    subscribe,
    unsubscribe,
)

__all__ = [
    "DesktopEvent",
    "EventBus",
    "EventTypes",
    "get_event_bus",
    "reset_event_bus_for_testing",
    "subscribe",
    "unsubscribe",
]
