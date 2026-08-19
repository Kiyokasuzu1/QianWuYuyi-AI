# -*- coding: utf-8 -*-
"""
src/runtime/audit_log/runtime_audit_logger.py

Phase 5.0-B: RuntimeAuditLogger —— Runtime 生命周期审计日志。

职责:
- 记录 Runtime 生命周期中的关键事件:
  event_start / event_end / memory_change / growth_change
  / self_model_evolution / reflection / persistence / exception
- append-only JSONL 格式
- 写入失败完全隔离(不影响主流程)
- 文件/目录不存在自动创建
- 支持 size-based rotation
- 线程安全

约束:
- 不引入 LLM SDK
- 不持有 Orchestrator 引用(单向)
- 所有公开方法 fire-and-forget,不抛异常
- 不写超大对象(只记录元信息 + 摘要)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional


logger = logging.getLogger(__name__)


RUNTIME_AUDIT_LOGGER_SCHEMA_VERSION = "1.0"

# 默认日志路径
DEFAULT_AUDIT_LOG_PATH = "data/runtime_audit.jsonl"

# rotation 默认阈值:10MB
DEFAULT_MAX_BYTES = 10 * 1024 * 1024

# 摘要截断长度
_SUMMARY_MAX_LEN = 240


# ============================================================
# 事件类型常量
# ============================================================
class AuditEvent:
    """审计事件类型常量。"""

    EVENT_START = "event_start"
    EVENT_END = "event_end"
    MEMORY_CHANGE = "memory_change"
    GROWTH_CHANGE = "growth_change"
    SELF_MODEL_EVOLUTION = "self_model_evolution"
    REFLECTION = "reflection"
    VALIDATION = "validation"
    PERSISTENCE = "persistence"
    EXCEPTION = "exception"
    CHECKPOINT = "checkpoint"
    LOOP_START = "loop_start"
    LOOP_STOP = "loop_stop"
    CUSTOM = "custom"


# ============================================================
# 辅助
# ============================================================
def _now_iso() -> str:
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):  # pragma: no cover
        return datetime.utcnow().isoformat() + "Z"


def _safe_summary(value: Any, max_len: int = _SUMMARY_MAX_LEN) -> str:
    """将任意对象安全地截断为可读字符串摘要。"""
    if value is None:
        return ""
    try:
        if isinstance(value, str):
            s = value
        else:
            s = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        try:
            s = str(value)
        except Exception:  # noqa: BLE001
            return "<unprintable>"
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


def _safe_get(obj: Any, *attrs: str, default: Any = None) -> Any:
    """getattr 链式安全访问。"""
    cur = obj
    for a in attrs:
        if cur is None:
            return default
        try:
            cur = getattr(cur, a, None)
        except Exception:  # noqa: BLE001
            return default
    return default if cur is None else cur


# ============================================================
# RuntimeAuditLogger
# ============================================================
class RuntimeAuditLogger:
    """Runtime 生命周期审计日志(Phase 5.0-B / v1.0)。

    使用方式:
        audit = RuntimeAuditLogger(log_path="data/runtime_audit.jsonl")
        audit.log_event_start(event_id="e_1", user_input="hi")
        # ... 主流程 ...
        audit.log_event_end(event_id="e_1", reply="hi back", duration_ms=12.3)

    关键不变量:
    - 任何方法调用永不抛异常(写入失败只内部计数)
    - 同一实例线程安全(RLock)
    - 文件不存在/目录不存在自动创建
    - 默认 size-based rotation(10MB → .1 后缀)
    - 提供查询接口(query / tail / read_all)用于 stability_test
    - 关闭时 flush
    """

    name: str = "runtime_audit_logger"
    schema_version: str = RUNTIME_AUDIT_LOGGER_SCHEMA_VERSION

    def __init__(
        self,
        log_path: str = DEFAULT_AUDIT_LOG_PATH,
        max_bytes: int = DEFAULT_MAX_BYTES,
        enabled: bool = True,
        auto_flush: bool = True,
        rotation_suffix: str = ".1",
    ) -> None:
        """构造 RuntimeAuditLogger。

        参数:
        - log_path: 日志文件路径(.jsonl)。
        - max_bytes: 超过此大小自动 rotation(<=0 表示不 rotation)。
        - enabled: 总开关;False 时所有写入都 no-op。
        - auto_flush: 每次写入后立即 flush(append-only 安全)。
        - rotation_suffix: 旧日志后缀,如 .1 → data/runtime_audit.jsonl.1。
        """
        self._lock = threading.RLock()
        self._log_path = str(log_path or DEFAULT_AUDIT_LOG_PATH)
        self._max_bytes = int(max_bytes or 0)
        self._enabled = bool(enabled)
        self._auto_flush = bool(auto_flush)
        self._rotation_suffix = str(rotation_suffix or ".1")

        # 统计
        self._write_count: int = 0
        self._write_error_count: int = 0
        self._rotation_count: int = 0
        self._last_write_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._created_at: str = _now_iso()
        self._closed: bool = False

    # --------------------------------------------------------
    # 属性
    # --------------------------------------------------------
    @property
    def log_path(self) -> str:
        return self._log_path

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def write_count(self) -> int:
        with self._lock:
            return self._write_count

    @property
    def write_error_count(self) -> int:
        with self._lock:
            return self._write_error_count

    @property
    def rotation_count(self) -> int:
        with self._lock:
            return self._rotation_count

    @property
    def last_write_at(self) -> Optional[str]:
        with self._lock:
            return self._last_write_at

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    # --------------------------------------------------------
    # 总开关
    # --------------------------------------------------------
    def enable(self) -> None:
        with self._lock:
            self._enabled = True

    def disable(self) -> None:
        with self._lock:
            self._enabled = False

    def close(self) -> None:
        with self._lock:
            self._closed = True

    # --------------------------------------------------------
    # 核心: 写一条记录
    # --------------------------------------------------------
    def _write_record(self, record: Dict[str, Any]) -> bool:
        """实际写盘操作。所有异常被隔离。"""
        with self._lock:
            if not self._enabled or self._closed:
                return False
            try:
                # 注入 schema 元数据
                record.setdefault("schema_version", self.schema_version)
                record.setdefault("ts", _now_iso())

                # size-based rotation
                if self._max_bytes > 0 and os.path.exists(self._log_path):
                    try:
                        size = os.path.getsize(self._log_path)
                        if size >= self._max_bytes:
                            self._do_rotate()
                    except Exception:  # noqa: BLE001
                        # size 检查失败不阻塞
                        pass

                # 确保目录存在
                folder = os.path.dirname(self._log_path)
                if folder and not os.path.exists(folder):
                    try:
                        os.makedirs(folder, exist_ok=True)
                    except Exception:  # noqa: BLE001
                        pass

                # append-only
                line = json.dumps(record, ensure_ascii=False, default=str)
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.write("\n")
                    if self._auto_flush:
                        try:
                            f.flush()
                            os.fsync(f.fileno())
                        except Exception:  # noqa: BLE001
                            pass

                self._write_count += 1
                self._last_write_at = _now_iso()
                self._last_error = None
                return True
            except Exception as exc:  # noqa: BLE001
                self._write_error_count += 1
                self._last_error = repr(exc)
                logger.warning("RuntimeAuditLogger 写入失败(已隔离): %s", exc)
                return False

    def _do_rotate(self) -> None:
        """执行 rotation:旧文件 → .1 后缀。"""
        try:
            backup = self._log_path + self._rotation_suffix
            if os.path.exists(backup):
                try:
                    os.remove(backup)
                except Exception:  # noqa: BLE001
                    pass
            try:
                os.replace(self._log_path, backup)
            except Exception:  # noqa: BLE001
                # rename 失败 → 删除并重建
                try:
                    if os.path.exists(self._log_path):
                        os.remove(self._log_path)
                except Exception:  # noqa: BLE001
                    pass
            self._rotation_count += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("RuntimeAuditLogger rotation 失败(已隔离): %s", exc)

    # --------------------------------------------------------
    # 公开 API
    # --------------------------------------------------------
    def log_event_start(
        self,
        event_id: str,
        user_input: Optional[str] = None,
        **extra: Any,
    ) -> bool:
        return self._write_record({
            "event": AuditEvent.EVENT_START,
            "event_id": str(event_id) if event_id is not None else "",
            "user_input_summary": _safe_summary(user_input),
            **self._clean_extra(extra),
        })

    def log_event_end(
        self,
        event_id: str,
        reply: Optional[str] = None,
        duration_ms: Optional[float] = None,
        success: bool = True,
        **extra: Any,
    ) -> bool:
        return self._write_record({
            "event": AuditEvent.EVENT_END,
            "event_id": str(event_id) if event_id is not None else "",
            "reply_summary": _safe_summary(reply),
            "duration_ms": float(duration_ms) if duration_ms is not None else None,
            "success": bool(success),
            **self._clean_extra(extra),
        })

    def log_memory_change(
        self,
        before: Any = None,
        after: Any = None,
        event_id: Optional[str] = None,
        **extra: Any,
    ) -> bool:
        return self._write_record({
            "event": AuditEvent.MEMORY_CHANGE,
            "event_id": str(event_id) if event_id is not None else "",
            "before_summary": _safe_summary(before),
            "after_summary": _safe_summary(after),
            "delta_summary": _safe_summary(self._safe_delta(before, after)),
            **self._clean_extra(extra),
        })

    def log_growth_change(
        self,
        before: Any = None,
        after: Any = None,
        event_id: Optional[str] = None,
        **extra: Any,
    ) -> bool:
        return self._write_record({
            "event": AuditEvent.GROWTH_CHANGE,
            "event_id": str(event_id) if event_id is not None else "",
            "before_summary": _safe_summary(before),
            "after_summary": _safe_summary(after),
            "delta_summary": _safe_summary(self._safe_delta(before, after)),
            **self._clean_extra(extra),
        })

    def log_self_model_evolution(
        self,
        evolution_result: Any = None,
        event_id: Optional[str] = None,
        **extra: Any,
    ) -> bool:
        new_snap = _safe_get(evolution_result, "new_snapshot")
        old_snap = _safe_get(evolution_result, "old_snapshot")
        return self._write_record({
            "event": AuditEvent.SELF_MODEL_EVOLUTION,
            "event_id": str(event_id) if event_id is not None else "",
            "old_snapshot_summary": _safe_summary(old_snap),
            "new_snapshot_summary": _safe_summary(new_snap),
            "evolution_id": _safe_get(evolution_result, "evolution_id"),
            **self._clean_extra(extra),
        })

    def log_reflection(
        self,
        reflection: Any = None,
        event_id: Optional[str] = None,
        **extra: Any,
    ) -> bool:
        return self._write_record({
            "event": AuditEvent.REFLECTION,
            "event_id": str(event_id) if event_id is not None else "",
            "reflection_summary": _safe_summary(reflection),
            "reflection_id": _safe_get(reflection, "reflection_id"),
            **self._clean_extra(extra),
        })

    def log_validation(
        self,
        report: Any = None,
        event_id: Optional[str] = None,
        **extra: Any,
    ) -> bool:
        return self._write_record({
            "event": AuditEvent.VALIDATION,
            "event_id": str(event_id) if event_id is not None else "",
            "report_summary": _safe_summary(report),
            "valid": _safe_get(report, "valid"),
            **self._clean_extra(extra),
        })

    def log_persistence(
        self,
        success: bool,
        event_id: Optional[str] = None,
        persisted_id: Optional[str] = None,
        **extra: Any,
    ) -> bool:
        return self._write_record({
            "event": AuditEvent.PERSISTENCE,
            "event_id": str(event_id) if event_id is not None else "",
            "success": bool(success),
            "persisted_id": str(persisted_id) if persisted_id is not None else None,
            **self._clean_extra(extra),
        })

    def log_exception(
        self,
        phase: str,
        exc: BaseException,
        event_id: Optional[str] = None,
        **extra: Any,
    ) -> bool:
        return self._write_record({
            "event": AuditEvent.EXCEPTION,
            "event_id": str(event_id) if event_id is not None else "",
            "phase": str(phase or "unknown"),
            "exception_type": type(exc).__name__,
            "exception_message": _safe_summary(str(exc), max_len=500),
            **self._clean_extra(extra),
        })

    def log_checkpoint(
        self,
        reason: str,
        state: Optional[Dict[str, Any]] = None,
        **extra: Any,
    ) -> bool:
        return self._write_record({
            "event": AuditEvent.CHECKPOINT,
            "reason": str(reason or "unknown"),
            "state": self._safe_state(state),
            **self._clean_extra(extra),
        })

    def log_loop_start(self, **extra: Any) -> bool:
        return self._write_record({
            "event": AuditEvent.LOOP_START,
            **self._clean_extra(extra),
        })

    def log_loop_stop(self, reason: Optional[str] = None, **extra: Any) -> bool:
        return self._write_record({
            "event": AuditEvent.LOOP_STOP,
            "reason": str(reason) if reason else None,
            **self._clean_extra(extra),
        })

    def log_custom(
        self,
        event_name: str,
        **extra: Any,
    ) -> bool:
        return self._write_record({
            "event": AuditEvent.CUSTOM,
            "event_name": str(event_name or "custom"),
            **self._clean_extra(extra),
        })

    # --------------------------------------------------------
    # 查询接口
    # --------------------------------------------------------
    def read_all(self, limit: int = 0) -> List[Dict[str, Any]]:
        """读取所有记录(limit<=0 表示全部)。返回列表,异常返回 []。"""
        try:
            if not os.path.exists(self._log_path):
                return []
            out: List[Dict[str, Any]] = []
            with open(self._log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:  # noqa: BLE001
                        # 跳过损坏行
                        continue
            if limit > 0 and len(out) > limit:
                return out[-limit:]
            return out
        except Exception as exc:  # noqa: BLE001
            logger.warning("RuntimeAuditLogger.read_all 失败(已隔离): %s", exc)
            return []

    def tail(self, n: int = 10) -> List[Dict[str, Any]]:
        return self.read_all(limit=max(0, int(n)))

    def count_by_event(self, event_name: str) -> int:
        try:
            return sum(
                1 for r in self.read_all() if r.get("event") == event_name
            )
        except Exception:  # noqa: BLE001
            return 0

    def count_exceptions(self) -> int:
        return self.count_by_event(AuditEvent.EXCEPTION)

    def query(
        self,
        predicate: Optional[Callable[[Dict[str, Any]], bool]] = None,
        limit: int = 0,
    ) -> List[Dict[str, Any]]:
        """按 predicate 过滤。"""
        try:
            all_records = self.read_all()
            if predicate is None:
                result = all_records
            else:
                result = [r for r in all_records if predicate(r)]
            if limit > 0 and len(result) > limit:
                return result[-limit:]
            return result
        except Exception:  # noqa: BLE001
            return []

    # --------------------------------------------------------
    # 健康度
    # --------------------------------------------------------
    def health_check(self) -> Dict[str, Any]:
        with self._lock:
            file_size = 0
            file_exists = False
            try:
                if os.path.exists(self._log_path):
                    file_exists = True
                    file_size = os.path.getsize(self._log_path)
            except Exception:  # noqa: BLE001
                pass
            return {
                "name": self.name,
                "schema_version": self.schema_version,
                "enabled": self._enabled,
                "closed": self._closed,
                "log_path": self._log_path,
                "file_exists": file_exists,
                "file_size": file_size,
                "max_bytes": self._max_bytes,
                "write_count": self._write_count,
                "write_error_count": self._write_error_count,
                "rotation_count": self._rotation_count,
                "last_write_at": self._last_write_at,
                "last_error": self._last_error,
                "created_at": self._created_at,
            }

    def reset_stats(self) -> None:
        with self._lock:
            self._write_count = 0
            self._write_error_count = 0
            self._rotation_count = 0
            self._last_write_at = None
            self._last_error = None

    # --------------------------------------------------------
    # 辅助
    # --------------------------------------------------------
    @staticmethod
    def _clean_extra(extra: Dict[str, Any]) -> Dict[str, Any]:
        """过滤掉不可序列化的 extra 字段。"""
        out: Dict[str, Any] = {}
        for k, v in extra.items():
            try:
                # 测试基础可序列化
                json.dumps(v, default=str)
                out[str(k)] = v
            except Exception:  # noqa: BLE001
                out[str(k)] = _safe_summary(v)
        return out

    @staticmethod
    def _safe_delta(before: Any, after: Any) -> Any:
        if before is None and after is None:
            return None
        if before is None:
            return {"added": _safe_summary(after, max_len=120)}
        if after is None:
            return {"removed": _safe_summary(before, max_len=120)}
        return {
            "before": _safe_summary(before, max_len=120),
            "after": _safe_summary(after, max_len=120),
        }

    @staticmethod
    def _safe_state(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(state, dict):
            return {}
        # 只保留标量/字符串,过滤大对象
        out: Dict[str, Any] = {}
        for k, v in state.items():
            if isinstance(v, (str, int, float, bool, type(None))):
                out[str(k)] = v
            else:
                out[str(k)] = _safe_summary(v, max_len=80)
        return out


__all__ = [
    "RuntimeAuditLogger",
    "AuditEvent",
    "RUNTIME_AUDIT_LOGGER_SCHEMA_VERSION",
    "DEFAULT_AUDIT_LOG_PATH",
    "DEFAULT_MAX_BYTES",
]
