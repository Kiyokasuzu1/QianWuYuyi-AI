# -*- coding: utf-8 -*-
"""
src/runtime/action_outcome.py

Phase B.6 Runtime Integration —— Action Outcome Tracking

本文件是 Phase B.6 的"结果反馈层胶水",**不修改**任何核心模块:

  - 不修改 RuntimeCore
  - 不修改 RuntimeSupervisor
  - 不修改 ActionDispatcher
  - 不修改 ProactiveEngine
  - 不修改 ActionConfidenceGate
  - 不修改 ActionLifecycleManager(通过可选 outcome 钩子接入)
  - 不修改 ActionPersistenceManager(复用其 persist_event 通道)

集成原理(继续 B.4/B.5 的"零侵入包装"模式):
  ┌──────────────────────────────────────────────────────┐
  │ ActionLifecycleManager                               │
  │  mark_terminal(COMPLETED) / record_trail             │
  │     ↓                                                │
  │  RuntimeB4Bridge._audit_flush_result()               │
  │     ↓ (B.6 新增)                                     │
  │  OutcomeTracker.record_outcome()                     │
  │     ↓                                                │
  │  ActionPersistenceManager.persist_event(             │
  │      stage="outcome_recorded"                        │
  │  )   → JSONL append-only                             │
  │     ↓                                                │
  │  data/action_outcome.jsonl                           │
  │     ↓                                                │
  │  ProactiveEngine 决策时调用                          │
  │  get_action_success_rate() / get_user_action_feedback()
  └──────────────────────────────────────────────────────┘

B.6 接入路径(零侵入,默认安全):
  - outcome 默认 enabled=True,但不阻塞
  - 写盘失败 → 计数 +1,自动降级为 memory-only(in-memory only)
  - 任何异常都被 try/except 隔离,绝不抛
  - 复用 ActionPersistenceManager 作为底层 JSONL 通道
  - outcome_records 在内存中维护(action_id -> OutcomeRecord),供查询
  - 不修改 supervisor / runtime_core / proactive_engine 任何代码

B.6 硬约束(项目红线):
  - 不修改任何核心模块
  - 不绕过 ActionConfidenceGate
  - 不自动发消息(handler 不注册)
  - 不启动 InitiativeBridge
  - 不新增线程(同步 in-tick)
  - 不新增事件总线
  - 默认开启但不阻塞(失败自动降级)
  - 全部 fail-soft

本模块职责:
  1. OutcomeRecord:结果反馈数据类(action 执行后的效果)
  2. OutcomeTracker:负责接收 outcome / 持久化 / 查询 / 统计
  3. fail-soft:写盘失败降级为 memory-only
  4. 复用 B.5 ActionPersistenceManager(同一 JSONL 文件,不同 stage)
  5. 提供统计接口(get_action_success_rate / get_user_action_feedback)
  6. 提供 outcome 评分接口(供 ProactiveEngine 决策优化)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# 默认配置
# ============================================================

PHASE_B6_DEFAULT_CONFIG: Dict[str, Any] = {
    "action_outcome": {
        "enabled": True,                  # 默认开启(但不阻塞)
        "path": "data/action_outcome.jsonl",
        "max_records": 50000,             # 内存中最多保留多少条 outcome
        "auto_recover": True,             # 启动时自动 load_state
    },
}

PHASE_B6_NAME = "phase_b6"
PHASE_B6_VERSION = "1.0.0"

DEFAULT_OUTCOME_PATH = "data/action_outcome.jsonl"
SCHEMA_VERSION = "1.0"

# B.6 使用的特殊 stage 名(用于复用 B.5 persistence 通道)
OUTCOME_STAGE_RECORDED = "outcome_recorded"


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

def apply_phase_b6_config(user_cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    将 B.6 默认配置合并到用户配置中。

    规则:
      - 用户显式给定的 key 优先级最高
      - 其余用 PHASE_B6_DEFAULT_CONFIG 兜底
      - 不修改原 dict(返回新 dict)
      - 嵌套 dict 递归合并
    """
    merged: Dict[str, Any] = {}
    if isinstance(user_cfg, dict):
        merged = {k: v for k, v in user_cfg.items()}

    for k, v in PHASE_B6_DEFAULT_CONFIG.items():
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


def is_phase_b6_enabled(cfg: Optional[Dict[str, Any]]) -> bool:
    """判断 B.6 outcome tracking 是否在 cfg 中启用"""
    if not isinstance(cfg, dict):
        return False
    p = cfg.get("action_outcome", {})
    if not isinstance(p, dict):
        return False
    return bool(p.get("enabled", True))


def is_b6_enabled_simple(user_cfg: Optional[Dict[str, Any]]) -> bool:
    """简化版判定:支持 cfg['outcome_enabled']=True/False 直接开关"""
    if not isinstance(user_cfg, dict):
        return False
    if "outcome_enabled" in user_cfg:
        return bool(user_cfg.get("outcome_enabled"))
    return is_phase_b6_enabled(user_cfg)


# ============================================================
# OutcomeRecord(dataclass 表示,实际写入用 dict)
# ============================================================

@dataclass
class OutcomeRecord:
    """
    Action 执行结果反馈记录。

    字段:
      action_id:    ProactiveEngine 生成的 action_id
      action_type:  action 类型(greeting / share / reminder ...)
      user_id:      目标用户 ID
      created_at:   action 创建时间(unix timestamp)
      completed_at: action 完成时间(unix timestamp)
      success:      是否成功执行
      score:        效果评分(0.0 ~ 1.0);用于 ProactiveEngine 决策
      feedback:     用户反馈文本(可选,未来扩展用)
      metadata:     额外元数据(可选)
    """
    action_id: str
    action_type: str
    user_id: str
    created_at: float
    completed_at: float
    success: bool
    score: float = 0.0
    feedback: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "action_id": self.action_id,
            "action_type": self.action_type,
            "user_id": self.user_id,
            "created_at": float(self.created_at),
            "completed_at": float(self.completed_at),
            "success": bool(self.success),
            "score": float(self.score),
        }
        if self.feedback is not None:
            out["feedback"] = self.feedback
        if self.metadata:
            out["metadata"] = dict(self.metadata)
        return out

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OutcomeRecord":
        return cls(
            action_id=str(d.get("action_id", "")),
            action_type=str(d.get("action_type", "")),
            user_id=str(d.get("user_id", "")),
            created_at=float(d.get("created_at", 0.0) or 0.0),
            completed_at=float(d.get("completed_at", 0.0) or 0.0),
            success=bool(d.get("success", False)),
            score=float(d.get("score", 0.0) or 0.0),
            feedback=d.get("feedback"),
            metadata=dict(d.get("metadata", {}) or {}),
        )

    @property
    def duration_seconds(self) -> float:
        """action 从创建到完成的耗时(秒)"""
        try:
            return max(0.0, float(self.completed_at) - float(self.created_at))
        except (TypeError, ValueError):
            return 0.0


# ============================================================
# OutcomeTracker
# ============================================================

class OutcomeTracker:
    """
    Action Outcome Tracker —— 结果反馈管理器

    职责:
      1. record_outcome(record):记录一条 outcome(写盘 + 内存)
      2. get_action_success_rate(action_type):查询某 action_type 的成功率
      3. get_user_action_feedback(user_id):查询某用户的所有反馈
      4. get_action_type_score(action_type):查询某 action_type 的平均评分
      5. get_recent_outcomes(limit):查询最近 N 条 outcome
      6. load_state():从 JSONL 恢复
      7. 写盘失败自动降级为 memory-only
      8. 线程安全(RLock)
    """

    def __init__(
        self,
        persistence: Any = None,
        enabled: bool = True,
        max_records: int = 50000,
    ) -> None:
        """
        Args:
            persistence: ActionPersistenceManager 实例(B.5 复用通道)
            enabled: 是否启用 outcome tracking
            max_records: 内存中最多保留多少条 outcome(超出滚动)
        """
        self._persistence = persistence
        self._enabled = bool(enabled)
        self._max_records = int(max_records or 0)

        self._lock = threading.RLock()

        # action_id -> OutcomeRecord(覆盖式,只保留最新一条)
        self._outcomes: Dict[str, OutcomeRecord] = {}

        # action_id -> 历史(append-only,带界,用于审计追溯)
        self._history: Dict[str, List[Dict[str, Any]]] = {}

        # action_id 出现顺序(用于 max_records 滚动)
        self._order: List[str] = []

        # 全部 outcome 按时间顺序的最近副本
        self._recent: List[Dict[str, Any]] = []

        # 统计
        self._record_count = 0
        self._record_error_count = 0
        self._recover_count = 0
        self._last_record_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._created_at: str = _now_iso()
        self._closed = False

        # 启动时:若 persistence 可用,自动 load_state
        if self._persistence is not None:
            try:
                self.load_state()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[phase_b6] 启动 load_state 异常(已隔离): {exc}")

    # --------------------------------------------------------
    # 属性
    # --------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def persistence(self) -> Any:
        return self._persistence

    @property
    def is_degraded(self) -> bool:
        """是否已降级(写盘失败 / persistence 不可用)"""
        if self._persistence is None:
            return True
        try:
            return bool(getattr(self._persistence, "is_degraded", False))
        except Exception:  # noqa: BLE001
            return False

    @property
    def record_count(self) -> int:
        with self._lock:
            return self._record_count

    @property
    def record_error_count(self) -> int:
        with self._lock:
            return self._record_error_count

    @property
    def recover_count(self) -> int:
        with self._lock:
            return self._recover_count

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    @property
    def in_memory_count(self) -> int:
        with self._lock:
            return len(self._order)

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
    # 核心: 记录一条 outcome
    # --------------------------------------------------------

    def record_outcome(self, outcome: OutcomeRecord) -> bool:
        """
        记录一条 outcome(写盘 + 内存更新)

        Returns:
            True 表示成功(写盘成功或 disabled),False 表示失败或降级。

        行为:
          - persistence=None → 直接更新内存,返回 True(等同 disabled)
          - enabled=False → 直接更新内存,返回 True
          - persistence.persist_event 抛错 → 异常隔离,返回 False
          - 内存始终更新(memory-only 降级)
        """
        if outcome is None:
            return False
        if not outcome.action_id:
            return False

        with self._lock:
            if self._closed:
                return False

            # 1) 总是更新内存(覆盖式 + 历史 append)
            try:
                self._outcomes[outcome.action_id] = outcome
                payload = outcome.to_dict()
                if outcome.action_id not in self._history:
                    self._history[outcome.action_id] = []
                    self._order.append(outcome.action_id)
                self._history[outcome.action_id].append(payload)
                # 限制 history 长度
                if len(self._history[outcome.action_id]) > 200:
                    self._history[outcome.action_id] = self._history[outcome.action_id][-200:]
                # 限制 max_records
                if self._max_records > 0:
                    while len(self._order) > self._max_records:
                        old = self._order.pop(0)
                        self._outcomes.pop(old, None)
                        self._history.pop(old, None)
                # 最近 outcomes(只读副本,带界)
                self._recent.append(payload)
                if self._max_records > 0 and len(self._recent) > self._max_records:
                    self._recent = self._recent[-self._max_records:]
            except Exception as exc:  # noqa: BLE001
                self._record_error_count += 1
                self._last_error = repr(exc)
                logger.debug(f"[phase_b6] outcome 内存更新异常(已隔离): {exc}")
                return False

            # 2) 写盘(若启用 + persistence 可用)
            if not self._enabled:
                # disabled 时只更新内存,不算 error
                return True
            if self._persistence is None:
                # 无 persistence,内存已更新
                return True

            try:
                if not hasattr(self._persistence, "persist_event"):
                    self._record_error_count += 1
                    self._last_error = "persistence missing persist_event"
                    return False

                ok = self._persistence.persist_event(
                    action_id=outcome.action_id,
                    lifecycle_id=f"outcome_{outcome.action_id}",
                    stage=OUTCOME_STAGE_RECORDED,
                    decision="outcome_recorded",
                    ts=float(outcome.completed_at or time.time()),
                    result=payload,
                    source="phase_b6_outcome",
                )
                if ok:
                    self._record_count += 1
                    self._last_record_at = _now_iso()
                    self._last_error = None
                else:
                    # persist_event 返回 False(降级 / disabled)
                    self._record_error_count += 1
                return bool(ok)
            except Exception as exc:  # noqa: BLE001
                self._record_error_count += 1
                self._last_error = repr(exc)
                logger.warning(f"[phase_b6] outcome 写盘异常(已隔离): {exc}")
                return False

    # --------------------------------------------------------
    # 统计接口
    # --------------------------------------------------------

    def get_action_success_rate(self, action_type: str) -> Dict[str, Any]:
        """
        查询某 action_type 的成功率

        Returns:
            {
                "action_type": str,
                "total": int,         # 总数
                "success": int,       # 成功数
                "rate": float,        # 成功率(0.0~1.0,无数据时 0.0)
                "avg_score": float,   # 平均评分
            }
        """
        with self._lock:
            total = 0
            success = 0
            score_sum = 0.0
            for outcome in self._outcomes.values():
                if outcome.action_type == action_type:
                    total += 1
                    if outcome.success:
                        success += 1
                    score_sum += float(outcome.score or 0.0)
            rate = (success / total) if total > 0 else 0.0
            avg_score = (score_sum / total) if total > 0 else 0.0
            return {
                "action_type": action_type,
                "total": total,
                "success": success,
                "rate": float(rate),
                "avg_score": float(avg_score),
            }

    def get_action_type_score(self, action_type: str) -> Dict[str, Any]:
        """
        查询某 action_type 的效果评分(综合成功率 + score)
        """
        with self._lock:
            total = 0
            success = 0
            score_sum = 0.0
            for outcome in self._outcomes.values():
                if outcome.action_type == action_type:
                    total += 1
                    if outcome.success:
                        success += 1
                    score_sum += float(outcome.score or 0.0)
            avg_score = (score_sum / total) if total > 0 else 0.0
            success_rate = (success / total) if total > 0 else 0.0
            # 综合评分 = 成功率 * 0.6 + 平均 score * 0.4
            combined = success_rate * 0.6 + avg_score * 0.4
            return {
                "action_type": action_type,
                "total": total,
                "success": success,
                "success_rate": float(success_rate),
                "avg_score": float(avg_score),
                "combined_score": float(combined),
            }

    def get_user_action_feedback(self, user_id: str) -> List[Dict[str, Any]]:
        """
        查询某用户的所有 outcome 反馈(按时间倒序)

        Returns:
            List[{action_id, action_type, success, score, feedback, completed_at}, ...]
        """
        with self._lock:
            items: List[Dict[str, Any]] = []
            for outcome in self._outcomes.values():
                if outcome.user_id == user_id:
                    items.append({
                        "action_id": outcome.action_id,
                        "action_type": outcome.action_type,
                        "success": outcome.success,
                        "score": float(outcome.score or 0.0),
                        "feedback": outcome.feedback,
                        "completed_at": float(outcome.completed_at),
                    })
            # 按 completed_at 倒序
            items.sort(key=lambda x: float(x.get("completed_at", 0.0) or 0.0), reverse=True)
            return items

    def get_action_outcome(self, action_id: str) -> Optional[OutcomeRecord]:
        """获取某 action 的最新 outcome"""
        with self._lock:
            return self._outcomes.get(action_id)

    def get_outcome_history(self, action_id: str) -> List[Dict[str, Any]]:
        """获取某 action 的 outcome 历史"""
        with self._lock:
            items = self._history.get(action_id, [])
            return [dict(x) for x in items]

    def get_recent_outcomes(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取最近 N 条 outcome(按写入顺序)"""
        with self._lock:
            if limit <= 0:
                return []
            return [dict(x) for x in self._recent[-limit:]]

    def get_outcome_summary(self) -> Dict[str, Any]:
        """整体 outcome 摘要"""
        with self._lock:
            total = len(self._order)
            success = sum(1 for o in self._outcomes.values() if o.success)
            # 按 action_type 分组
            type_stats: Dict[str, Dict[str, int]] = {}
            for o in self._outcomes.values():
                if o.action_type not in type_stats:
                    type_stats[o.action_type] = {"total": 0, "success": 0}
                type_stats[o.action_type]["total"] += 1
                if o.success:
                    type_stats[o.action_type]["success"] += 1
            return {
                "total": total,
                "success": success,
                "success_rate": (success / total) if total > 0 else 0.0,
                "by_type": type_stats,
            }

    # --------------------------------------------------------
    # 恢复: load_state()
    # --------------------------------------------------------

    def load_state(self) -> int:
        """
        从 persistence 恢复 outcome trail。
        Returns: 恢复的 action_id 数。

        行为:
          - persistence=None → 返回 0
          - 遍历 persistence.get_recent_actions() 找出所有 stage=outcome_recorded
          - 反推 OutcomeRecord 并灌回内存
          - 异常隔离,不抛
        """
        if self._persistence is None:
            return 0

        latest_map: Dict[str, Dict[str, Any]] = {}
        try:
            if hasattr(self._persistence, "get_recent_actions"):
                recents = self._persistence.get_recent_actions(
                    limit=self._max_records or 50000
                ) or []
                for r in recents:
                    if not isinstance(r, dict):
                        continue
                    stage = str(r.get("stage", ""))
                    if stage != OUTCOME_STAGE_RECORDED:
                        continue
                    result = r.get("result") or {}
                    if not isinstance(result, dict):
                        continue
                    action_id = str(result.get("action_id", "") or "")
                    if not action_id:
                        # 退化:用 record 自己的 action_id
                        action_id = str(r.get("action_id", "") or "")
                    if not action_id:
                        continue
                    latest_map[action_id] = result
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b6] load_state 失败(已隔离): {exc}")
            return 0

        if not latest_map:
            return 0

        count = 0
        with self._lock:
            for action_id, payload in latest_map.items():
                try:
                    rec = OutcomeRecord.from_dict({
                        "action_id": payload.get("action_id") or action_id,
                        "action_type": payload.get("action_type", ""),
                        "user_id": payload.get("user_id", ""),
                        "created_at": payload.get("created_at", 0.0),
                        "completed_at": payload.get(
                            "completed_at", payload.get("ts", 0.0)
                        ),
                        "success": payload.get("success", False),
                        "score": payload.get("score", 0.0),
                        "feedback": payload.get("feedback"),
                        "metadata": payload.get("metadata", {}),
                    })
                    if not rec.action_id:
                        continue
                    if rec.action_id in self._outcomes:
                        continue
                    self._outcomes[rec.action_id] = rec
                    if rec.action_id not in self._history:
                        self._history[rec.action_id] = []
                        self._order.append(rec.action_id)
                    self._history[rec.action_id].append(rec.to_dict())
                    self._recent.append(rec.to_dict())
                    count += 1
                except Exception:  # noqa: BLE001
                    continue
            self._recover_count = count
        return count

    # --------------------------------------------------------
    # 健康度
    # --------------------------------------------------------

    def health_check(self) -> Dict[str, Any]:
        with self._lock:
            persistence_status: Dict[str, Any] = {"enabled": False}
            if self._persistence is not None:
                try:
                    if hasattr(self._persistence, "health_check"):
                        persistence_status = self._persistence.health_check() or persistence_status
                except Exception:  # noqa: BLE001
                    pass
            return {
                "name": PHASE_B6_NAME,
                "schema_version": SCHEMA_VERSION,
                "version": PHASE_B6_VERSION,
                "enabled": self._enabled,
                "degraded": self.is_degraded,
                "closed": self._closed,
                "in_memory_outcomes": len(self._order),
                "record_count": self._record_count,
                "record_error_count": self._record_error_count,
                "recover_count": self._recover_count,
                "last_record_at": self._last_record_at,
                "last_error": self._last_error,
                "created_at": self._created_at,
                "persistence": persistence_status,
            }

    def reset_stats(self) -> None:
        with self._lock:
            self._record_count = 0
            self._record_error_count = 0
            self._recover_count = 0
            self._last_record_at = None
            self._last_error = None

    def clear(self) -> None:
        """清空内存(测试用);不删 JSONL 文件"""
        with self._lock:
            self._outcomes.clear()
            self._history.clear()
            self._order.clear()
            self._recent.clear()
            self._record_count = 0
            self._record_error_count = 0
            self._recover_count = 0
            self._last_record_at = None
            self._last_error = None


# ============================================================
# 工厂 / 便捷函数
# ============================================================

def create_outcome_tracker(
    persistence: Any = None,
    cfg: Optional[Dict[str, Any]] = None,
    enabled: Optional[bool] = None,
) -> OutcomeTracker:
    """
    工厂:根据 cfg 构造 OutcomeTracker

    Args:
        persistence: ActionPersistenceManager 实例
        cfg: 完整 cfg,会取 cfg["action_outcome"]
        enabled: 显式覆盖 enabled

    Returns:
        OutcomeTracker 实例
    """
    o_cfg: Dict[str, Any] = {}
    if isinstance(cfg, dict):
        raw = cfg.get("action_outcome", {}) or {}
        if isinstance(raw, dict):
            o_cfg = raw

    is_enabled = bool(enabled) if enabled is not None else bool(o_cfg.get("enabled", True))
    max_records = int(o_cfg.get("max_records", 50000))

    return OutcomeTracker(
        persistence=persistence,
        enabled=is_enabled,
        max_records=max_records,
    )


def safe_record_outcome(
    tracker: Optional[OutcomeTracker],
    outcome: OutcomeRecord,
) -> bool:
    """全局安全 record_outcome(tracker=None / 异常都被隔离)"""
    if tracker is None:
        return False
    if outcome is None or not outcome.action_id:
        return False
    try:
        return bool(tracker.record_outcome(outcome))
    except Exception:  # noqa: BLE001
        return False


__all__ = [
    "PHASE_B6_DEFAULT_CONFIG",
    "PHASE_B6_NAME",
    "PHASE_B6_VERSION",
    "DEFAULT_OUTCOME_PATH",
    "SCHEMA_VERSION",
    "OUTCOME_STAGE_RECORDED",
    "apply_phase_b6_config",
    "is_phase_b6_enabled",
    "is_b6_enabled_simple",
    "OutcomeRecord",
    "OutcomeTracker",
    "create_outcome_tracker",
    "safe_record_outcome",
]
