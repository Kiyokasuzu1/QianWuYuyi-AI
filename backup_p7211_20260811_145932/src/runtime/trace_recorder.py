# -*- coding: utf-8 -*-
"""
src/runtime/trace_recorder.py

Phase 7.0 Dashboard Runtime Center —— RuntimeTraceRecorder。

职责:
    记录一次请求的生命周期链路到 JSONL 文件,用于 Dashboard 展示与调试。
    解决 "羽依突然不回复,不知道卡在哪里" 的问题。

设计原则:
    1. 轻量侵入: 只在 RuntimePipeline 关键节点打点,不修改业务逻辑。
    2. 异常隔离: 任何记录失败都吞掉,绝不影响主链路。
    3. 文件存储: 使用 JSONL,每行一条 trace 记录,跨进程可读。
    4. 容量保护: 文件超过阈值自动轮转,防止磁盘爆炸。
    5. 无依赖: 仅使用 Python 标准库。

存储:
    data/runtime_trace/trace_YYYYMMDD.jsonl
    每天一个文件,便于归档和清理。

记录格式:
    {
        "trace_id": "pipeline_abc123def456",
        "session_id": "pipe_abc123def456",
        "user_message": "你好羽依",   # 截断前 100 字符
        "stages": [
            {"name": "receive_message", "start": "15:02:01.123",
             "end": "15:02:01.124", "duration_ms": 1, "error": null},
            {"name": "memory_retrieval", "start": "...", "end": "...",
             "duration_ms": 2103, "error": null},
            ...
        ],
        "reply_source": "runtime",      # runtime / legacy / None
        "reply_preview": "你好呀...",   # 截断前 50 字符
        "total_duration_ms": 11000,
        "success": true,
        "error": null,
        "started_at": "2026-08-10T15:02:01.123456",
        "ended_at": "2026-08-10T15:02:12.124680"
    }
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================

DEFAULT_TRACE_DIR = Path("data/runtime_trace")
DEFAULT_MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB,超过则轮转
DEFAULT_MAX_RECENT_TRACES = 100  # 内存缓存最近 N 条,供 API 快速返回
DEFAULT_USER_MESSAGE_PREVIEW_LEN = 100
DEFAULT_REPLY_PREVIEW_LEN = 50
DEFAULT_ROTATE_KEEP_FILES = 5  # 轮转保留最近 5 个文件


# ============================================================
# RuntimeTraceRecorder
# ============================================================
class RuntimeTraceRecorder:
    """记录请求链路到 JSONL 文件 + 内存缓存的轻量 Recorder。

    线程安全: 使用 RLock 保护内部状态。
    异常隔离: 所有公开方法均吞掉异常,只记录日志,绝不抛出。
    """

    def __init__(
        self,
        trace_dir: Path = DEFAULT_TRACE_DIR,
        *,
        max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES,
        max_recent_traces: int = DEFAULT_MAX_RECENT_TRACES,
        rotate_keep_files: int = DEFAULT_ROTATE_KEEP_FILES,
    ) -> None:
        self._trace_dir = Path(trace_dir)
        self._max_file_size = max(1024, int(max_file_size_bytes))
        self._max_recent = max(1, int(max_recent_traces))
        self._rotate_keep = max(1, int(rotate_keep_files))
        self._lock = threading.RLock()
        self._recent: List[Dict[str, Any]] = []
        self._current_file: Optional[Path] = None
        # 启动时确保目录存在
        try:
            self._trace_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RuntimeTraceRecorder] trace_dir 创建失败(将降级到内存缓存): %s", exc,
            )

    # --------------------------------------------------------
    # 公开 API
    # --------------------------------------------------------
    def start_trace(
        self,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        user_message: str = "",
    ) -> "RuntimeTraceContext":
        """开始一次 trace,返回 RuntimeTraceContext 句柄。

        Args:
            trace_id: 可选 trace_id,不传则自动生成。
            session_id: 可选 session_id,与 RuntimePipeline 对齐。
            user_message: 用户原始消息(用于预览)。

        Returns:
            RuntimeTraceContext 实例,调用方在结束时调用 finalize()。
        """
        if not trace_id:
            trace_id = f"trace_{uuid.uuid4().hex[:12]}"
        return RuntimeTraceContext(
            recorder=self,
            trace_id=trace_id,
            session_id=session_id or trace_id,
            user_message=user_message,
        )

    def record_stage(
        self,
        trace_id: str,
        stage: str,
        *,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        duration_ms: Optional[int] = None,
        error: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """记录单个阶段(轻量增量写入接口,异常隔离)。

        注意: 此方法是低级 API,优先使用 RuntimeTraceContext.record_stage()。
        """
        try:
            self._safe_append_stage_to_recent(trace_id, {
                "name": str(stage),
                "start": self._iso(start_time) if start_time else None,
                "end": self._iso(end_time) if end_time else None,
                "duration_ms": int(duration_ms) if duration_ms is not None else None,
                "error": error,
                "extra": extra or {},
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RuntimeTraceRecorder] record_stage 失败(已吞掉): %s", exc)

    def finalize_trace(self, trace_ctx: "RuntimeTraceContext") -> None:
        """结束一次 trace,写入 JSONL 文件 + 缓存最近记录。

        Args:
            trace_ctx: 由 start_trace() 返回的上下文。
        """
        try:
            record = trace_ctx.build_record()
            self._safe_write_record(record)
            self._safe_cache_recent(record)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RuntimeTraceRecorder] finalize_trace 失败(已吞掉): %s", exc)

    def get_recent_traces(self, limit: int = 20) -> List[Dict[str, Any]]:
        """返回最近 N 条 trace 记录(从内存缓存读)。

        按时间倒序(最新在前)。
        """
        try:
            n = max(1, min(200, int(limit)))
        except (TypeError, ValueError):
            n = 20
        with self._lock:
            # 返回副本,避免外部修改
            return [dict(r) for r in self._recent[-n:][::-1]]

    def get_trace_by_id(self, trace_id: str) -> Optional[Dict[str, Any]]:
        """根据 trace_id 查找记录(仅从内存缓存)。"""
        if not trace_id:
            return None
        with self._lock:
            for r in self._recent:
                if r.get("trace_id") == trace_id:
                    return dict(r)
        return None

    # --------------------------------------------------------
    # 内部: 文件写入 + 轮转
    # --------------------------------------------------------
    def _safe_write_record(self, record: Dict[str, Any]) -> None:
        """安全写一条 JSONL(异常隔离)。"""
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RuntimeTraceRecorder] JSON 序列化失败: %s", exc)
            return

        try:
            target = self._get_target_file()
            if target is None:
                # 目录不可用,降级到仅内存缓存
                return
            with open(target, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RuntimeTraceRecorder] 写文件失败(已降级): %s", exc)

    def _get_target_file(self) -> Optional[Path]:
        """获取当天 trace 文件,自动轮转。"""
        try:
            if not self._trace_dir.exists():
                self._trace_dir.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            return None

        today = datetime.now().strftime("%Y%m%d")
        target = self._trace_dir / f"trace_{today}.jsonl"
        try:
            if target.exists() and target.stat().st_size > self._max_file_size:
                self._rotate_file(target)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RuntimeTraceRecorder] 轮转检查失败: %s", exc)
        return target

    def _rotate_file(self, target: Path) -> None:
        """轮转当前文件: trace_YYYYMMDD.jsonl → trace_YYYYMMDD.1.jsonl → ..."""
        try:
            base = target.stem  # trace_YYYYMMDD
            suffix = target.suffix  # .jsonl
            for i in range(self._rotate_keep, 0, -1):
                src = target.parent / f"{base}.{i}{suffix}"
                dst = target.parent / f"{base}.{i + 1}{suffix}"
                if src.exists():
                    if i + 1 > self._rotate_keep:
                        src.unlink()
                    else:
                        src.rename(dst)
            rotated = target.parent / f"{base}.1{suffix}"
            target.rename(rotated)
            logger.info("[RuntimeTraceRecorder] trace 文件已轮转: %s", rotated.name)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RuntimeTraceRecorder] 轮转失败: %s", exc)

    # --------------------------------------------------------
    # 内部: 内存缓存
    # --------------------------------------------------------
    def _safe_cache_recent(self, record: Dict[str, Any]) -> None:
        with self._lock:
            self._recent.append(record)
            if len(self._recent) > self._max_recent:
                # 保留最新的 N 条
                self._recent = self._recent[-self._max_recent:]

    def _safe_append_stage_to_recent(
        self, trace_id: str, stage: Dict[str, Any],
    ) -> None:
        """增量追加 stage 到最近 trace(用于流式记录)。

        如果 trace_id 不在最近列表中,则忽略(由 finalize_trace 完整写入)。
        """
        with self._lock:
            for r in self._recent:
                if r.get("trace_id") == trace_id:
                    stages = r.setdefault("stages", [])
                    stages.append(dict(stage))
                    return

    # --------------------------------------------------------
    # 内部: 工具
    # --------------------------------------------------------
    @staticmethod
    def _iso(ts: Optional[float]) -> str:
        if ts is None:
            return ""
        try:
            return datetime.fromtimestamp(float(ts)).isoformat(timespec="milliseconds")
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _preview(text: str, max_len: int) -> str:
        if not text or not isinstance(text, str):
            return ""
        if len(text) <= max_len:
            return text
        return text[:max_len] + "..."


# ============================================================
# RuntimeTraceContext
# ============================================================
class RuntimeTraceContext:
    """一次 trace 的上下文句柄。

    典型用法:
        ctx = recorder.start_trace(user_message="你好")
        ctx.record_stage("receive_message")
        ctx.record_stage("memory_retrieval")
        ctx.record_stage("llm_request", error=None)
        ctx.finalize(reply="你好呀", reply_source="runtime", success=True)
    """

    def __init__(
        self,
        recorder: RuntimeTraceRecorder,
        trace_id: str,
        session_id: str,
        user_message: str,
    ) -> None:
        self._recorder = recorder
        self._trace_id = str(trace_id)
        self._session_id = str(session_id)
        self._user_message = str(user_message or "")
        self._started_at = time.time()
        self._ended_at: Optional[float] = None
        self._stages: List[Dict[str, Any]] = []
        self._stage_starts: Dict[str, float] = {}
        self._reply: str = ""
        self._reply_source: Optional[str] = None
        self._success: bool = False
        self._error: Optional[str] = None
        self._finalized: bool = False

    @property
    def trace_id(self) -> str:
        return self._trace_id

    @property
    def session_id(self) -> str:
        return self._session_id

    def record_stage(
        self,
        name: str,
        *,
        error: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """记录一个阶段(开始时间自动取上次 stage 结束,简化调用)。

        异常隔离: 任何失败都吞掉。
        """
        if self._finalized:
            return
        try:
            now = time.time()
            start = self._stage_starts.pop(name, None) or self._started_at
            duration_ms = max(0, int((now - start) * 1000))
            self._stages.append({
                "name": str(name),
                "start": self._iso(start),
                "end": self._iso(now),
                "duration_ms": duration_ms,
                "error": error,
                "extra": extra or {},
            })
            # 更新下一个 stage 的隐式起点
            self._stage_starts["__last__"] = now
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RuntimeTraceContext] record_stage 失败(已吞掉): %s", exc)

    def start_stage(self, name: str) -> None:
        """显式标记某阶段开始(可选,配合 end_stage 使用)。"""
        if self._finalized:
            return
        try:
            self._stage_starts[name] = time.time()
        except Exception:  # noqa: BLE001
            pass

    def end_stage(
        self,
        name: str,
        *,
        error: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """显式标记某阶段结束。"""
        if self._finalized:
            return
        try:
            end = time.time()
            start = self._stage_starts.pop(name, None)
            if start is None:
                start = end
            duration_ms = max(0, int((end - start) * 1000))
            self._stages.append({
                "name": str(name),
                "start": self._iso(start),
                "end": self._iso(end),
                "duration_ms": duration_ms,
                "error": error,
                "extra": extra or {},
            })
            self._stage_starts["__last__"] = end
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RuntimeTraceContext] end_stage 失败(已吞掉): %s", exc)

    def finalize(
        self,
        *,
        reply: str = "",
        reply_source: Optional[str] = None,
        success: bool = True,
        error: Optional[str] = None,
    ) -> None:
        """结束 trace 并写入文件 + 缓存。"""
        if self._finalized:
            return
        self._finalized = True
        self._reply = str(reply or "")
        self._reply_source = reply_source
        self._success = bool(success)
        self._error = error
        self._ended_at = time.time()
        try:
            self._recorder.finalize_trace(self)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RuntimeTraceContext] finalize 失败(已吞掉): %s", exc)

    def build_record(self) -> Dict[str, Any]:
        """构造要写入 JSONL 的完整记录。"""
        ended = self._ended_at or time.time()
        total_ms = max(0, int((ended - self._started_at) * 1000))
        return {
            "trace_id": self._trace_id,
            "session_id": self._session_id,
            "user_message_preview": RuntimeTraceRecorder._preview(
                self._user_message, DEFAULT_USER_MESSAGE_PREVIEW_LEN,
            ),
            "stages": [dict(s) for s in self._stages],
            "reply_source": self._reply_source,
            "reply_preview": RuntimeTraceRecorder._preview(
                self._reply, DEFAULT_REPLY_PREVIEW_LEN,
            ),
            "total_duration_ms": total_ms,
            "success": self._success,
            "error": self._error,
            "started_at": self._iso(self._started_at),
            "ended_at": self._iso(ended),
        }

    @staticmethod
    def _iso(ts: float) -> str:
        try:
            return datetime.fromtimestamp(float(ts)).isoformat(timespec="milliseconds")
        except Exception:  # noqa: BLE001
            return ""


# ============================================================
# 模块级单例(供 api_server.py 注入 + Dashboard 读取)
# ============================================================
_recorder_singleton: Optional[RuntimeTraceRecorder] = None
_recorder_lock = threading.Lock()


def get_runtime_trace_recorder() -> RuntimeTraceRecorder:
    """获取全局 RuntimeTraceRecorder 单例(懒加载)。"""
    global _recorder_singleton
    if _recorder_singleton is None:
        with _recorder_lock:
            if _recorder_singleton is None:
                _recorder_singleton = RuntimeTraceRecorder()
    return _recorder_singleton


def set_runtime_trace_recorder(recorder: Optional[RuntimeTraceRecorder]) -> None:
    """注入全局 RuntimeTraceRecorder(供 api_server.py 启动时使用)。"""
    global _recorder_singleton
    with _recorder_lock:
        _recorder_singleton = recorder


def reset_runtime_trace_recorder_for_testing() -> None:
    """测试用: 重置单例。"""
    global _recorder_singleton
    with _recorder_lock:
        _recorder_singleton = None


__all__ = [
    "RuntimeTraceRecorder",
    "RuntimeTraceContext",
    "get_runtime_trace_recorder",
    "set_runtime_trace_recorder",
    "reset_runtime_trace_recorder_for_testing",
    "DEFAULT_TRACE_DIR",
]
