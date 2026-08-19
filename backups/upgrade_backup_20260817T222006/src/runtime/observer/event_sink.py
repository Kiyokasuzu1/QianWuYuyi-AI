# -*- coding: utf-8 -*-
"""
src/runtime/observer/event_sink.py

Phase 7.1 —— ObservationEventSink 观察事件接收器。

职责:
    接收 RuntimePipeline 的事件 → 双路分发:
      1) EventQueue.push(内存队列, SSE 消费)
      2) JSONL 追加写入(可选, 默认关闭, 由 ObservationConfig.persistence 控制)

设计铁律:
    - 任何 emit() 调用绝不抛异常,失败仅 debug log, 不影响主链路
    - config.enabled=False 时, sink 为严格 No-Op(零副作用)
    - persistence 默认关闭, 避免长期运行磁盘爆炸
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .config import ObservationConfig, DEFAULT_PERSISTENCE_DIR
from .event_queue import EventQueue
from .runtime_event import RuntimeEvent

logger = logging.getLogger(__name__)


# ============================================================
# JSONL 辅助(仅 persistence=true 时执行)
# ============================================================
def _today_file_name() -> str:
    """events_YYYYMMDD.jsonl"""
    try:
        return datetime.now(timezone.utc).strftime("events_%Y%m%d.jsonl")
    except Exception:  # noqa: BLE001
        ts = datetime.utcfromtimestamp(time.time()).strftime("events_%Y%m%d.jsonl")
        return ts


def _atomic_append_line(path: Path, line: str, *, max_bytes: int = 50 * 1024 * 1024) -> bool:
    """
    原子追加一行到 JSONL。

    策略: 先检查文件大小,超过 max_bytes 则 rename 成 .bak+时间戳,
         再写新文件。避免单文件膨胀。
    """
    if not line.endswith("\n"):
        line = line + "\n"
    b = line.encode("utf-8")
    try:
        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True)
        # 轮转检查
        try:
            if path.exists() and path.stat().st_size + len(b) > max_bytes:
                roll_name = (
                    str(path.name) +
                    "." + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") +
                    ".rolled"
                )
                path.rename(parent / roll_name)
        except Exception:  # noqa: BLE001
            # 轮转失败忽略,继续写旧文件
            pass
        with open(path, "ab") as f:
            f.write(b)
            f.flush()
        return True
    except Exception:  # noqa: BLE001
        return False


# ============================================================
# ObservationEventSink
# ============================================================
class ObservationEventSink:
    """
    观察事件接收器。

    典型流程:
        sink.emit(RuntimeEventType.USER_MESSAGE_RECEIVED,
                  trace_id=tid, session_id=sid,
                  data={"preview": "你好"})
    """

    def __init__(
        self,
        queue: EventQueue,
        config: Optional[ObservationConfig] = None,
        project_root: Optional[Any] = None,
    ) -> None:
        self._queue = queue  # 允许 None? 不,强制传;单例工厂在 _get_event_queue None 时自己返回 No-Op sink
        self._cfg: ObservationConfig = (
            config if isinstance(config, ObservationConfig) else ObservationConfig()
        )
        self._lock = threading.Lock()  # 保护 JSONL 写
        self._current_file: Optional[Path] = None
        self._current_file_day: Optional[str] = None  # YYYYMMDD

        # 持久化目录解析
        try:
            if project_root is None:
                # 默认: 相对 cwd(和 data/runtime_trace/ 同级)
                base = Path.cwd()
            else:
                base = Path(project_root)
            self._persistence_dir: Path = base / self._cfg.persistence_dir
        except Exception:  # noqa: BLE001
            self._persistence_dir = Path(DEFAULT_PERSISTENCE_DIR)

        # 统计(用于调试/Dashboard)
        self._emitted_count: int = 0
        self._persist_failure_count: int = 0

    # --------------------------------------------------------
    # 对外 API
    # --------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self._cfg.enabled)

    @property
    def persistence_enabled(self) -> bool:
        return self.enabled and bool(self._cfg.persistence)

    def stats(self) -> Dict[str, Any]:
        """只读统计,用于调试。"""
        with self._lock:
            return {
                "enabled": self.enabled,
                "persistence": self.persistence_enabled,
                "persistence_dir": str(self._persistence_dir),
                "emitted_count": self._emitted_count,
                "persist_failure_count": self._persist_failure_count,
            }

    # --------------------------------------------------------
    # emit —— 主入口
    # --------------------------------------------------------
    def emit(
        self,
        event_type: str,
        *,
        trace_id: str,
        session_id: str,
        stage: str = "",
        level: str = "info",
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        构造 RuntimeEvent 并分发 → queue + (可选) JSONL。

        100% 吞掉异常,绝不影响调用方。
        """
        # 总开关关闭 → 严格 No-Op
        if not self._cfg.enabled:
            return

        try:
            event = RuntimeEvent(
                event_type=event_type,
                trace_id=str(trace_id or ""),
                session_id=str(session_id or ""),
                stage=str(stage or ""),
                level=str(level or "info"),
                data=dict(data) if isinstance(data, dict) else {},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[ObservationEventSink] RuntimeEvent 构造失败(跳过): %s", exc)
            return

        self._dispatch(event)

    def emit_event(self, event: RuntimeEvent) -> None:
        """直接分发一个已构造的 RuntimeEvent(测试/内部用)。"""
        if not self._cfg.enabled:
            return
        if not isinstance(event, RuntimeEvent):
            return
        self._dispatch(event)

    # --------------------------------------------------------
    # 内部分发
    # --------------------------------------------------------
    def _dispatch(self, event: RuntimeEvent) -> None:
        # 1) 内存队列(实时 SSE 消费)
        try:
            if self._queue is not None:
                self._queue.push(event)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[ObservationEventSink] queue.push 失败(已吞掉): %s", exc)

        # 2) JSONL 持久化(默认关闭)
        if self._cfg.persistence:
            try:
                self._safe_persist(event)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[ObservationEventSink] persist 失败(已吞掉): %s", exc)

        # 3) 计数
        try:
            with self._lock:
                self._emitted_count += 1
        except Exception:  # noqa: BLE001
            pass

    def _safe_persist(self, event: RuntimeEvent) -> None:
        line: Optional[str] = None
        try:
            line = json.dumps(event.to_dict(), ensure_ascii=False)
        except Exception:  # noqa: BLE001
            with self._lock:
                self._persist_failure_count += 1
            return
        if not line:
            return

        with self._lock:
            # 换日检查(新日换新文件)
            today = _today_file_name()  # events_YYYYMMDD.jsonl
            if self._current_file_day != today or self._current_file is None:
                self._current_file_day = today
                self._current_file = self._persistence_dir / today
            ok = _atomic_append_line(
                self._current_file,
                line,
                max_bytes=self._cfg.persistence_max_file_bytes,
            )
            if not ok:
                self._persist_failure_count += 1


# ============================================================
# NoOpSink —— config.enabled=False / 组件不可用时的替身
# ============================================================
class NoOpObservationSink(ObservationEventSink):
    """严格 No-Op,所有方法零行为。"""

    def __init__(self) -> None:
        # 跳过父类 __init__,避免任何副作用
        pass

    @property
    def enabled(self) -> bool:  # type: ignore[override]
        return False

    @property
    def persistence_enabled(self) -> bool:  # type: ignore[override]
        return False

    def stats(self) -> Dict[str, Any]:  # type: ignore[override]
        return {"enabled": False, "noop": True}

    def emit(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        return None

    def emit_event(self, event: Any) -> None:  # type: ignore[override]
        return None


__all__ = ["ObservationEventSink", "NoOpObservationSink"]
