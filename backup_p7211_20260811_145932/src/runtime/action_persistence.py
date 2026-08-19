# -*- coding: utf-8 -*-
"""
src/runtime/action_persistence.py

Phase B.5 Runtime Integration —— Action Lifecycle 持久化与恢复

本文件是 Phase B.5 的"持久化层胶水",**不修改**任何核心模块:

  - 不修改 RuntimeCore
  - 不修改 RuntimeSupervisor
  - 不修改 ActionDispatcher
  - 不修改 ProactiveEngine
  - 不修改 RuntimeB3Bridge
  - 不修改 ActionLifecycleManager(通过可选 persistence 钩子接入)

集成原理(继续 B.4 的"零侵入包装"模式):
  ┌──────────────────────────────────────────────────────┐
  │ ActionLifecycleManager                               │
  │  mark_terminal() / record_trail()                    │
  │     ↓ (B.5 钩子)                                    │
  │  ActionPersistenceManager.persist_event()           │
  │     ↓                                                │
  │  JSONL append-only                                   │
  │     ↓                                                │
  │  data/action_lifecycle.jsonl                         │
  └──────────────────────────────────────────────────────┘

启动恢复流程:
  start_runtime.py
    ↓ 构造 ActionPersistenceManager(enabled=True)
    ↓ load_state() 读 JSONL
    ↓ 把 (action_id, stage, ts) 灌回 ActionLifecycleManager
    ↓ RuntimeB4Bridge.flush_pending() 走治理时自动避开已 terminal action

B.5 接入路径(零侵入,默认安全):
  - persistence 默认 enabled=True,但不阻塞
  - 写盘失败 → 计数 +1,自动降级为内存模式(in-memory only)
  - 读盘失败 → 返回空(等同首次启动),不抛错
  - 任何文件/IO 异常都被 try/except 隔离

B.5 硬约束(项目红线):
  - 不修改任何核心模块
  - 不绕过 ActionConfidenceGate
  - 不自动发消息(handler 不注册)
  - 不启动 InitiativeBridge
  - 不新增线程(同步 in-tick)
  - 不新增事件总线
  - 默认开启但不阻塞(失败自动降级)
  - 全部 fail-soft

本模块职责:
  1. ActionPersistenceRecord: 持久化记录 dataclass
  2. ActionPersistenceManager: 负责 write / query / load_state
  3. fail-soft: 写盘失败降级为 memory-only
  4. append-only JSONL,支持 size-based rotation
  5. 提供 query 接口(get_action / get_history / get_recent_actions)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# 默认配置
# ============================================================

PHASE_B5_DEFAULT_CONFIG: Dict[str, Any] = {
    "action_persistence": {
        "enabled": True,                  # 默认开启(但不阻塞)
        "path": "data/action_lifecycle.jsonl",
        "max_bytes": 10 * 1024 * 1024,    # 10MB rotation
        "rotation_suffix": ".1",
        "max_records": 50000,             # 内存中最多保留多少条
        "auto_recover": True,             # 启动时自动 load_state
    },
}

PHASE_B5_NAME = "phase_b5"
PHASE_B5_VERSION = "1.0.0"

DEFAULT_PERSISTENCE_PATH = "data/action_lifecycle.jsonl"
SCHEMA_VERSION = "1.0"


# ============================================================
# B.13 引入的执行层 stage 字符串(本文件仅做常量集中引用,
# 实际写入由 B.13 decision_executor._persist_execution() 调用 persist_event 实现)
# ============================================================

# Phase B.13(Decision Governance Executor)写入的 4 个新 stage
B13_STAGE_EXEC_STARTED = "decision_execution_started"
B13_STAGE_EXEC_COMPLETED = "decision_execution_completed"
B13_STAGE_EXEC_FAILED = "decision_execution_failed"
B13_STAGE_EXEC_REVERTED = "decision_execution_reverted"

ALL_B13_STAGES = (
    B13_STAGE_EXEC_STARTED,
    B13_STAGE_EXEC_COMPLETED,
    B13_STAGE_EXEC_FAILED,
    B13_STAGE_EXEC_REVERTED,
)


# ============================================================
# 时间工具
# ============================================================

def _now_iso() -> str:
    """ISO 8601 UTC timestamp"""
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):  # pragma: no cover
        return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 配置注入
# ============================================================

def apply_phase_b5_config(user_cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    将 B.5 默认配置合并到用户配置中。

    规则:
      - 用户显式给定的 key 优先级最高
      - 其余用 PHASE_B5_DEFAULT_CONFIG 兜底
      - 不修改原 dict(返回新 dict)
      - 嵌套 dict 递归合并
    """
    merged: Dict[str, Any] = {}
    if isinstance(user_cfg, dict):
        merged = {k: v for k, v in user_cfg.items()}

    for k, v in PHASE_B5_DEFAULT_CONFIG.items():
        if isinstance(v, dict):
            existing = merged.get(k)
            if isinstance(existing, dict):
                sub: Dict[str, Any] = {}
                sub.update(v)
                sub.update(existing)
                merged[k] = sub
            else:
                merged[k] = {k2: v2 for k2, v2 in v.items()}
        else:
            merged.setdefault(k, v)
    return merged


def is_phase_b5_enabled(cfg: Optional[Dict[str, Any]]) -> bool:
    """判断 B.5 persistence 是否在 cfg 中启用"""
    if not isinstance(cfg, dict):
        return False
    p = cfg.get("action_persistence", {})
    if not isinstance(p, dict):
        return False
    return bool(p.get("enabled", True))


def is_b5_enabled_simple(user_cfg: Optional[Dict[str, Any]]) -> bool:
    """简化版判定:支持 cfg['persistence_enabled']=True/False 直接开关"""
    if not isinstance(user_cfg, dict):
        return False
    if "persistence_enabled" in user_cfg:
        return bool(user_cfg.get("persistence_enabled"))
    return is_phase_b5_enabled(user_cfg)


# ============================================================
# ActionPersistenceRecord
# ============================================================

@dataclass
class ActionPersistenceRecord:
    """
    Action 持久化记录(JSONL 行)。

    字段:
      action_id:    ProactiveEngine 生成的 action_id
      lifecycle_id: governance 分配的内部 ID
      stage:        LifecycleStage 之一
      ts:           unix timestamp(float)
      timestamp:    ISO 8601 字符串
      decision:     任意决策标签
      source:       来源(默认 'phase_b5_persistence')
      result:       任意 dict
      error:        异常 repr
    """
    action_id: str
    lifecycle_id: str
    stage: str
    ts: float
    timestamp: str = ""
    decision: str = ""
    source: str = "phase_b5_persistence"
    result: Optional[Any] = None
    error: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "ts": self.ts,
            "timestamp": self.timestamp or _now_iso(),
            "action_id": self.action_id,
            "lifecycle_id": self.lifecycle_id,
            "stage": self.stage,
            "decision": self.decision,
            "source": self.source,
        }
        if self.result is not None:
            out["result"] = self.result
        if self.error is not None:
            out["error"] = self.error
        if self.extra:
            out["extra"] = dict(self.extra)
        return out

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ActionPersistenceRecord":
        return cls(
            action_id=str(d.get("action_id", "")),
            lifecycle_id=str(d.get("lifecycle_id", "")),
            stage=str(d.get("stage", "")),
            ts=float(d.get("ts", 0.0) or 0.0),
            timestamp=str(d.get("timestamp", "")),
            decision=str(d.get("decision", "")),
            source=str(d.get("source", "phase_b5_persistence")),
            result=d.get("result"),
            error=d.get("error"),
            extra=dict(d.get("extra", {}) or {}),
        )


# ============================================================
# ActionPersistenceManager
# ============================================================

class ActionPersistenceManager:
    """
    Action Lifecycle 持久化 & 恢复管理器。

    职责:
      1. persist_event(record): 写入一条 JSONL 记录(append-only)
      2. get_action(action_id): 获取该 action_id 的最新状态
      3. get_history(action_id): 获取该 action_id 的全部历史
      4. get_recent_actions(limit): 获取最近 N 条 actions
      5. load_state(): 从 JSONL 恢复(返回 {action_id: (stage, ts)})
      6. 写盘失败自动降级为 memory-only
      7. size-based rotation
      8. 线程安全
    """

    def __init__(
        self,
        path: str = DEFAULT_PERSISTENCE_PATH,
        max_bytes: int = 10 * 1024 * 1024,
        max_records: int = 50000,
        enabled: bool = True,
        rotation_suffix: str = ".1",
    ) -> None:
        self._path = str(path or DEFAULT_PERSISTENCE_PATH)
        self._max_bytes = int(max_bytes or 0)
        self._max_records = int(max_records or 0)
        self._enabled = bool(enabled)
        self._rotation_suffix = str(rotation_suffix or ".1")

        # 降级标志: 第一次写盘失败后置 True,后续不再尝试写盘
        self._degraded = False

        # 内存中保存的 action_id → 最新 (stage, ts, lifecycle_id, ...)
        # 只存最新一条(覆盖式)
        self._latest: Dict[str, Dict[str, Any]] = {}

        # 内存中保存的 action_id → 全部历史(append-only,带界)
        self._history: Dict[str, List[Dict[str, Any]]] = {}

        # 全部事件的顺序列表(action_id 出现顺序)
        self._order: List[str] = []

        # 全部事件按时间顺序的最新副本(只读)
        self._all_records: List[Dict[str, Any]] = []

        # 统计
        self._write_count = 0
        self._write_error_count = 0
        self._rotation_count = 0
        self._recover_count = 0
        self._last_write_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._created_at: str = _now_iso()
        self._closed = False

        self._lock = threading.RLock()

        # 启动时自动恢复
        try:
            self.load_state()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[phase_b5] 启动 load_state 异常(已隔离): {e}")

    # --------------------------------------------------------
    # 属性
    # --------------------------------------------------------

    @property
    def path(self) -> str:
        return self._path

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def is_degraded(self) -> bool:
        """是否已降级为 memory-only(写盘失败)"""
        return self._degraded

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
    def recover_count(self) -> int:
        """load_state 恢复的 action 数"""
        with self._lock:
            return self._recover_count

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    def enable(self) -> None:
        with self._lock:
            self._enabled = True
            self._degraded = False  # 重新启用时重置降级标志

    def disable(self) -> None:
        with self._lock:
            self._enabled = False

    def close(self) -> None:
        with self._lock:
            self._closed = True

    # --------------------------------------------------------
    # 核心: 写一条记录
    # --------------------------------------------------------

    def persist_event(
        self,
        action_id: str,
        lifecycle_id: str,
        stage: str,
        decision: str = "",
        ts: Optional[float] = None,
        result: Any = None,
        error: Optional[str] = None,
        source: str = "phase_b5_persistence",
        extra: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        持久化一条 lifecycle 事件。

        Returns:
            True 表示写盘成功(或不写因为 disabled);False 表示失败或降级。
        """
        if not action_id:
            return False

        rec_ts = float(ts if ts is not None else time.time())
        rec = ActionPersistenceRecord(
            action_id=action_id,
            lifecycle_id=lifecycle_id,
            stage=stage,
            ts=rec_ts,
            timestamp=_now_iso(),
            decision=decision,
            source=source,
            result=result,
            error=error,
            extra=extra or {},
        )
        return self._write_record(rec)

    def _write_record(self, rec: ActionPersistenceRecord) -> bool:
        """实际写盘 + 更新内存索引。所有异常被隔离。"""
        with self._lock:
            if self._closed:
                return False

            payload = rec.to_dict()
            action_id = rec.action_id

            # 1) 总是更新内存索引
            try:
                self._latest[action_id] = payload
                if action_id not in self._history:
                    self._history[action_id] = []
                    self._order.append(action_id)
                self._history[action_id].append(payload)
                # 限制 history 长度
                if len(self._history[action_id]) > 200:
                    self._history[action_id] = self._history[action_id][-200:]
                # 限制 max_records
                if self._max_records > 0:
                    while len(self._order) > self._max_records:
                        old = self._order.pop(0)
                        self._latest.pop(old, None)
                        self._history.pop(old, None)
                # 全部事件顺序列表(只读副本,带界)
                self._all_records.append(payload)
                if self._max_records > 0 and len(self._all_records) > self._max_records:
                    self._all_records = self._all_records[-self._max_records:]
            except Exception as exc:  # noqa: BLE001
                # 内存索引失败,降级
                self._degraded = True
                self._last_error = repr(exc)
                return False

            # 2) 写盘(若未禁用 / 未降级)
            if not self._enabled or self._degraded:
                return False

            try:
                # size-based rotation
                if self._max_bytes > 0 and os.path.exists(self._path):
                    try:
                        size = os.path.getsize(self._path)
                        if size >= self._max_bytes:
                            self._do_rotate()
                    except Exception:  # noqa: BLE001
                        pass

                # 确保目录存在
                folder = os.path.dirname(self._path)
                if folder and not os.path.exists(folder):
                    try:
                        os.makedirs(folder, exist_ok=True)
                    except Exception:  # noqa: BLE001
                        pass

                line = json.dumps(payload, ensure_ascii=False, default=str)
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.write("\n")
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
                # 写盘失败 → 降级
                self._degraded = True
                self._write_error_count += 1
                self._last_error = repr(exc)
                logger.warning(
                    "[phase_b5] 持久化写盘失败(已降级为 memory-only): %s", exc
                )
                return False

    def _do_rotate(self) -> None:
        """执行 rotation:旧文件 → .1 后缀。"""
        try:
            backup = self._path + self._rotation_suffix
            if os.path.exists(backup):
                try:
                    os.remove(backup)
                except Exception:  # noqa: BLE001
                    pass
            try:
                os.replace(self._path, backup)
            except Exception:  # noqa: BLE001
                try:
                    if os.path.exists(self._path):
                        os.remove(self._path)
                except Exception:  # noqa: BLE001
                    pass
            self._rotation_count += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("[phase_b5] rotation 失败(已隔离): %s", exc)

    # --------------------------------------------------------
    # 恢复: load_state()
    # --------------------------------------------------------

    def load_state(self) -> Dict[str, Tuple[str, float]]:
        """
        从 JSONL 恢复 action 状态。

        Returns:
            {action_id: (stage, ts), ...}  —— 仅终态 / 已知 stage 的 actions

        行为:
          - 文件不存在 → 返回 {}
          - 任意一行解析失败 → 跳过(不抛)
          - 文件 IO 错误 → 计数 +1,返回 {}
        """
        with self._lock:
            self._recover_count = 0
            if not os.path.exists(self._path):
                return {}

            result: Dict[str, Tuple[str, float]] = {}
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            d = json.loads(line)
                        except Exception:  # noqa: BLE001
                            continue
                        action_id = str(d.get("action_id", ""))
                        stage = str(d.get("stage", ""))
                        ts = float(d.get("ts", 0.0) or 0.0)
                        if not action_id or not stage:
                            continue
                        # 覆盖式写入(最新的 stage 覆盖旧的)
                        result[action_id] = (stage, ts)
                        # 同时更新内存索引
                        if action_id not in self._history:
                            self._history[action_id] = []
                            self._order.append(action_id)
                        self._history[action_id].append(d)
                        # 限制
                        if len(self._history[action_id]) > 200:
                            self._history[action_id] = self._history[action_id][-200:]
                        # latest
                        cur = self._latest.get(action_id)
                        if cur is None or float(cur.get("ts", 0.0) or 0.0) <= ts:
                            self._latest[action_id] = d
                        # all_records
                        self._all_records.append(d)
                self._recover_count = len(result)
                return result
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[phase_b5] load_state 失败(已隔离): {exc}")
                return {}

    # --------------------------------------------------------
    # 查询接口
    # --------------------------------------------------------

    def get_action(self, action_id: str) -> Optional[Dict[str, Any]]:
        """获取 action_id 的最新状态;不存在返回 None"""
        with self._lock:
            d = self._latest.get(action_id)
            return dict(d) if d else None

    def get_history(self, action_id: str) -> List[Dict[str, Any]]:
        """获取 action_id 的全部历史(按写入顺序)"""
        with self._lock:
            items = self._history.get(action_id, [])
            return [dict(x) for x in items]

    def get_recent_actions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取最近 N 条 actions(按 action_id 出现顺序的末段)"""
        with self._lock:
            if limit <= 0:
                return []
            aids = self._order[-limit:]
            return [dict(self._latest[a]) for a in aids if a in self._latest]

    def get_terminal_actions(self) -> Dict[str, Tuple[str, float]]:
        """返回所有已终态的 actions(action_id → (stage, ts))"""
        from src.runtime.phase_b4_integration import LifecycleStage
        out: Dict[str, Tuple[str, float]] = {}
        with self._lock:
            for aid, d in self._latest.items():
                stage = d.get("stage", "")
                if stage in LifecycleStage.TERMINAL:
                    out[aid] = (stage, float(d.get("ts", 0.0) or 0.0))
        return out

    def get_dispatched_ids(self) -> Set[str]:
        """返回所有已 dispatched(completed) 的 action_id 集合"""
        from src.runtime.phase_b4_integration import LifecycleStage
        out: Set[str] = set()
        with self._lock:
            for aid, d in self._latest.items():
                if d.get("stage") == LifecycleStage.COMPLETED:
                    out.add(aid)
        return out

    # --------------------------------------------------------
    # 健康度
    # --------------------------------------------------------

    def health_check(self) -> Dict[str, Any]:
        with self._lock:
            file_size = 0
            file_exists = False
            try:
                if os.path.exists(self._path):
                    file_exists = True
                    file_size = os.path.getsize(self._path)
            except Exception:  # noqa: BLE001
                pass
            return {
                "name": PHASE_B5_NAME,
                "schema_version": SCHEMA_VERSION,
                "version": PHASE_B5_VERSION,
                "enabled": self._enabled,
                "degraded": self._degraded,
                "closed": self._closed,
                "path": self._path,
                "file_exists": file_exists,
                "file_size": file_size,
                "max_bytes": self._max_bytes,
                "max_records": self._max_records,
                "in_memory_actions": len(self._order),
                "write_count": self._write_count,
                "write_error_count": self._write_error_count,
                "rotation_count": self._rotation_count,
                "recover_count": self._recover_count,
                "last_write_at": self._last_write_at,
                "last_error": self._last_error,
                "created_at": self._created_at,
            }

    def reset_stats(self) -> None:
        with self._lock:
            self._write_count = 0
            self._write_error_count = 0
            self._rotation_count = 0
            self._recover_count = 0
            self._last_write_at = None
            self._last_error = None

    def clear(self) -> None:
        """清空内存(测试用);不删 JSONL 文件"""
        with self._lock:
            self._latest.clear()
            self._history.clear()
            self._order.clear()
            self._all_records.clear()
            self._degraded = False


# ============================================================
# 工厂 / 便捷函数
# ============================================================

def create_persistence_manager(
    cfg: Optional[Dict[str, Any]] = None,
    auto_recover: bool = True,
) -> ActionPersistenceManager:
    """
    根据 cfg 构造 ActionPersistenceManager。

    Args:
        cfg: 完整 cfg,会取 cfg['action_persistence']
        auto_recover: 是否在构造时自动 load_state

    Returns:
        ActionPersistenceManager 实例
    """
    p_cfg: Dict[str, Any] = {}
    if isinstance(cfg, dict):
        raw = cfg.get("action_persistence", {}) or {}
        if isinstance(raw, dict):
            p_cfg = raw

    path = p_cfg.get("path", DEFAULT_PERSISTENCE_PATH)
    max_bytes = int(p_cfg.get("max_bytes", 10 * 1024 * 1024))
    max_records = int(p_cfg.get("max_records", 50000))
    enabled = bool(p_cfg.get("enabled", True))
    rotation_suffix = str(p_cfg.get("rotation_suffix", ".1"))

    mgr = ActionPersistenceManager(
        path=path,
        max_bytes=max_bytes,
        max_records=max_records,
        enabled=enabled,
        rotation_suffix=rotation_suffix,
    )
    # auto_recover 可独立控制
    if not auto_recover:
        mgr.clear()
    return mgr


def safe_persist(
    manager: Optional[ActionPersistenceManager],
    action_id: str,
    lifecycle_id: str,
    stage: str,
    **kwargs: Any,
) -> bool:
    """全局安全 persist(manager=None / 异常都被隔离)"""
    if manager is None:
        return False
    if not action_id:
        return False
    try:
        return manager.persist_event(
            action_id=action_id,
            lifecycle_id=lifecycle_id,
            stage=stage,
            **kwargs,
        )
    except Exception:  # noqa: BLE001
        return False


__all__ = [
    "PHASE_B5_DEFAULT_CONFIG",
    "PHASE_B5_NAME",
    "PHASE_B5_VERSION",
    "DEFAULT_PERSISTENCE_PATH",
    "SCHEMA_VERSION",
    "apply_phase_b5_config",
    "is_phase_b5_enabled",
    "is_b5_enabled_simple",
    "ActionPersistenceRecord",
    "ActionPersistenceManager",
    "create_persistence_manager",
    "safe_persist",
]
