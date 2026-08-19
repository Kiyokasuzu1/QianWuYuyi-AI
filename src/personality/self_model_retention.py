"""
Phase 6.4: SelfModel Quota & Retention

职责：
- 防止 SelfModel 数据无限增长
- 不删除任何历史；只标记 active=False 或 archived=True
- 平衡"长期记忆"与"运行时性能"

设计原则（强制）：
1. **不删除**：只允许 active=False / archived=True
2. **可逆**：所有标记都可被查询和还原（通过 audit 追溯）
3. **隔离**：失败不影响 SelfModel 主数据
4. **可配置**：所有阈值都通过参数注入

配额策略：

SelfBelief:
    - max_active_beliefs:        active belief 数量上限（超出按 confidence 降序保留）
    - min_confidence_for_active: confidence 低于此值的 belief 标记 active=False
    - 不删除任何 belief

SelfHistory:
    - max_in_memory_events:      内存中保留的事件数（SelfHistory.MAX_EVENTS 默认 1000）
    - archive_threshold:         超出部分标记 archived=True
    - importance_low_below:      importance 低于此值的优先 archive
    - 不删除任何事件

SelfReflection:
    - recent_window_days:        仅保留窗口内的笔记
    - high_value_min_confidence: 高于此值的笔记永久保留
    - 窗口外 + 低 confidence 的标记 archived=True

回滚与查询：
- restore_active(belief_id)  → 恢复 belief 为 active=True
- is_archived(item_id)        → 查询是否被 archive
- get_archive_summary()        → 返回当前 archive 统计
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 默认配额
# ============================================================

DEFAULT_MAX_ACTIVE_BELIEFS: int = 1000
DEFAULT_MIN_CONFIDENCE_FOR_ACTIVE: float = 0.2

DEFAULT_MAX_IN_MEMORY_EVENTS: int = 1000
DEFAULT_IMPORTANCE_LOW_BELOW: float = 0.3

DEFAULT_RECENT_WINDOW_DAYS: int = 90
DEFAULT_HIGH_VALUE_MIN_CONFIDENCE: float = 0.7


# ============================================================
# Retention Report
# ============================================================

@dataclass
class RetentionReport:
    """配额执行报告"""
    timestamp: str = field(default_factory=_now_iso)

    # SelfBelief
    beliefs_total: int = 0
    beliefs_active_before: int = 0
    beliefs_active_after: int = 0
    beliefs_deactivated: int = 0
    beliefs_deactivated_ids: List[str] = field(default_factory=list)

    # SelfHistory
    history_total: int = 0
    history_in_memory_before: int = 0
    history_archived: int = 0
    history_archived_ids: List[str] = field(default_factory=list)
    history_importance_assigned: int = 0

    # SelfReflection
    reflections_total: int = 0
    reflections_in_window: int = 0
    reflections_high_value: int = 0
    reflections_archived: int = 0
    reflections_archived_ids: List[str] = field(default_factory=list)

    # 元信息
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    dry_run: bool = False
    applied: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def summary(self) -> Dict[str, Any]:
        return {
            "beliefs": {
                "total": self.beliefs_total,
                "active_before": self.beliefs_active_before,
                "active_after": self.beliefs_active_after,
                "deactivated": self.beliefs_deactivated,
            },
            "history": {
                "total": self.history_total,
                "in_memory_before": self.history_in_memory_before,
                "archived": self.history_archived,
            },
            "reflections": {
                "total": self.reflections_total,
                "in_window": self.reflections_in_window,
                "high_value": self.reflections_high_value,
                "archived": self.reflections_archived,
            },
            "applied": self.applied,
            "dry_run": self.dry_run,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
        }


# ============================================================
# Importance Tracker
# ============================================================

class ImportanceTracker:
    """
    SelfHistoryEvent 重要性跟踪器。

    重要性的来源：
    - PCR applied（含 affected_traits）→ 高重要性
    - snapshot_created → 中等重要性
    - belief_added / belief_reinforced → 中等重要性
    - rollback → 高重要性
    - 其他 → 0.5 默认

    重要性仅由 SelfModelRetention 内部使用，不暴露给其他模块。
    """

    # 默认重要性映射
    DEFAULT_IMPORTANCE: Dict[str, float] = {
        "pcr_applied": 0.8,
        "rollback": 0.9,
        "snapshot_created": 0.6,
        "identity_core_changed": 0.95,
        "growth_record_applied": 0.7,
        "reflection_insight_applied": 0.5,
        "self_belief_added": 0.5,
        "self_belief_reinforced": 0.4,
        "self_understanding_updated": 0.4,
    }

    def __init__(self) -> None:
        self._importance: Dict[str, float] = {}

    def get(self, event_id: str) -> float:
        return self._importance.get(event_id, 0.5)

    def compute_for_event(
        self,
        event_type: str,
        affected_traits: Optional[Dict[str, float]] = None,
        affected_beliefs: Optional[List[str]] = None,
    ) -> float:
        """根据事件类型和影响范围计算重要性。"""
        base = self.DEFAULT_IMPORTANCE.get(event_type, 0.5)
        # 放大因子：有 trait 变化或 belief 变化
        if affected_traits:
            base = min(1.0, base + 0.1)
        if affected_beliefs:
            base = min(1.0, base + 0.05 * min(3, len(affected_beliefs)))
        return round(base, 4)

    def assign(self, event_id: str, event_type: str,
               affected_traits: Optional[Dict[str, float]] = None,
               affected_beliefs: Optional[List[str]] = None) -> float:
        """为事件分配并存储重要性。"""
        imp = self.compute_for_event(event_type, affected_traits, affected_beliefs)
        self._importance[event_id] = imp
        return imp

    def all(self) -> Dict[str, float]:
        return dict(self._importance)

    def clear(self) -> None:
        self._importance.clear()


# ============================================================
# SelfModelRetention
# ============================================================

class SelfModelRetention:
    """
    SelfModel 配额与保留策略执行器。

    重要约束：
    - 不修改 SelfBelief / SelfHistory / SelfReflection 的 dataclass 定义
    - 不删除任何数据；只通过 active 字段或 _archived_ids 集合标记
    - 必须通过 SelfModelAdapter 调用，不直接修改 store
    """

    def __init__(
        self,
        max_active_beliefs: int = DEFAULT_MAX_ACTIVE_BELIEFS,
        min_confidence_for_active: float = DEFAULT_MIN_CONFIDENCE_FOR_ACTIVE,
        max_in_memory_events: int = DEFAULT_MAX_IN_MEMORY_EVENTS,
        importance_low_below: float = DEFAULT_IMPORTANCE_LOW_BELOW,
        recent_window_days: int = DEFAULT_RECENT_WINDOW_DAYS,
        high_value_min_confidence: float = DEFAULT_HIGH_VALUE_MIN_CONFIDENCE,
    ) -> None:
        self.max_active_beliefs = int(max_active_beliefs)
        self.min_confidence_for_active = float(min_confidence_for_active)
        self.max_in_memory_events = int(max_in_memory_events)
        self.importance_low_below = float(importance_low_below)
        self.recent_window_days = int(recent_window_days)
        self.high_value_min_confidence = float(high_value_min_confidence)

        # 内部 archive 状态（不修改原对象）
        self._archived_beliefs: Set[str] = set()
        self._archived_history: Set[str] = set()
        self._archived_reflections: Set[str] = set()
        # 重要性追踪
        self._importance_tracker = ImportanceTracker()

    # ============================================================
    # 重要性管理（仅 Retention 内部使用）
    # ============================================================

    def register_event_importance(
        self,
        event_id: str,
        event_type: str,
        affected_traits: Optional[Dict[str, float]] = None,
        affected_beliefs: Optional[List[str]] = None,
    ) -> float:
        """为事件分配重要性。"""
        return self._importance_tracker.assign(
            event_id, event_type, affected_traits, affected_beliefs
        )

    def get_event_importance(self, event_id: str) -> float:
        return self._importance_tracker.get(event_id)

    # ============================================================
    # Archive 状态查询
    # ============================================================

    def is_archived(self, item_id: str, kind: str = "belief") -> bool:
        if kind == "belief":
            return item_id in self._archived_beliefs
        if kind == "history":
            return item_id in self._archived_history
        if kind == "reflection":
            return item_id in self._archived_reflections
        return False

    def restore(self, item_id: str, kind: str = "belief") -> bool:
        """还原 archive 标记（不影响 active 字段）。"""
        if kind == "belief":
            self._archived_beliefs.discard(item_id)
            return True
        if kind == "history":
            self._archived_history.discard(item_id)
            return True
        if kind == "reflection":
            self._archived_reflections.discard(item_id)
            return True
        return False

    # ============================================================
    # SelfBelief 配额
    # ============================================================

    def enforce_belief_quota(
        self,
        beliefs_store: Any,
        dry_run: bool = False,
    ) -> RetentionReport:
        """
        强制执行 SelfBelief 配额。

        规则：
        1. confidence < min_confidence_for_active → active=False
        2. active count > max_active_beliefs → 按 confidence 升序标记 active=False
        3. 不删除任何 belief；归档但保留
        """
        report = RetentionReport(dry_run=dry_run)
        try:
            all_beliefs = list(beliefs_store.all()) if hasattr(beliefs_store, "all") else []
            report.beliefs_total = len(all_beliefs)
            report.beliefs_active_before = sum(1 for b in all_beliefs if getattr(b, "active", True))

            to_deactivate: List[Any] = []

            # 1. 低 confidence 标记
            for b in all_beliefs:
                try:
                    if b.active and float(b.confidence) < self.min_confidence_for_active:
                        to_deactivate.append(b)
                except Exception:
                    continue

            # 2. 超过 max_active_beliefs → 按 confidence 升序
            active_beliefs = [b for b in all_beliefs if b.active and b not in to_deactivate]
            if len(active_beliefs) > self.max_active_beliefs:
                active_beliefs_sorted = sorted(active_beliefs, key=lambda b: float(b.confidence))
                excess = len(active_beliefs) - self.max_active_beliefs
                to_deactivate.extend(active_beliefs_sorted[:excess])

            # 3. 执行
            for b in to_deactivate:
                bid = getattr(b, "belief_id", "")
                if not dry_run:
                    try:
                        # SelfBelief 是 dataclass；可直接设值
                        object.__setattr__(b, "active", False)
                    except Exception:
                        # 退化为使用 beliefs_store 的 mark inactive（若存在）
                        if hasattr(beliefs_store, "deactivate"):
                            try:
                                beliefs_store.deactivate(bid)
                            except Exception:
                                pass
                    self._archived_beliefs.add(bid)
                report.beliefs_deactivated += 1
                report.beliefs_deactivated_ids.append(bid)

            report.beliefs_active_after = sum(1 for b in all_beliefs if getattr(b, "active", True))
            if not dry_run:
                report.applied = True
        except Exception as e:
            report.errors.append(f"enforce_belief_quota: {e}")
            logger.warning(f"SelfModelRetention.enforce_belief_quota 失败: {e}")
        return report

    # ============================================================
    # SelfHistory 配额
    # ============================================================

    def enforce_history_quota(
        self,
        history: Any,
        dry_run: bool = False,
    ) -> RetentionReport:
        """
        强制执行 SelfHistory 配额。

        规则：
        1. in-memory 超出 max_in_memory_events → 按 importance 升序归档
        2. importance_low_below 以下的优先归档
        3. 不删除任何事件
        """
        report = RetentionReport(dry_run=dry_run)
        try:
            all_events = list(history.all()) if hasattr(history, "all") else []
            report.history_total = len(all_events)
            report.history_in_memory_before = len(all_events)

            # 先为未分配的事件计算重要性
            for ev in all_events:
                eid = getattr(ev, "event_id", "")
                if eid and eid not in self._importance_tracker._importance:
                    self._importance_tracker.assign(
                        eid,
                        getattr(ev, "event_type", ""),
                        getattr(ev, "affected_traits", {}) or {},
                        getattr(ev, "affected_beliefs", []) or [],
                    )
                    report.history_importance_assigned += 1

            if len(all_events) <= self.max_in_memory_events:
                if not dry_run:
                    report.applied = True
                return report

            # 超出 → 按 importance 升序
            excess_count = len(all_events) - self.max_in_memory_events
            events_with_imp = [
                (ev, self._importance_tracker.get(getattr(ev, "event_id", "")))
                for ev in all_events
            ]
            events_sorted = sorted(events_with_imp, key=lambda x: x[1])
            to_archive = [ev for ev, _ in events_sorted[:excess_count]]

            for ev in to_archive:
                eid = getattr(ev, "event_id", "")
                if not dry_run:
                    self._archived_history.add(eid)
                report.history_archived += 1
                report.history_archived_ids.append(eid)

            if not dry_run:
                report.applied = True
        except Exception as e:
            report.errors.append(f"enforce_history_quota: {e}")
            logger.warning(f"SelfModelRetention.enforce_history_quota 失败: {e}")
        return report

    # ============================================================
    # SelfReflection 配额
    # ============================================================

    def enforce_reflection_quota(
        self,
        reflections_store: Any,
        dry_run: bool = False,
    ) -> RetentionReport:
        """
        强制执行 SelfReflection 配额。

        规则：
        1. 高 confidence 笔记（>= high_value_min_confidence）→ 永久保留
        2. 窗口内笔记（recent_window_days 内）→ 保留
        3. 窗口外 + 低 confidence → archive（不删除）
        """
        report = RetentionReport(dry_run=dry_run)
        try:
            all_notes = list(reflections_store.all()) if hasattr(reflections_store, "all") else []
            report.reflections_total = len(all_notes)

            # 计算窗口截止时间
            now = datetime.utcnow()
            window_cutoff = _now_iso()  # 简化：用 ISO 字符串排序
            try:
                from datetime import timedelta
                cutoff_dt = now - timedelta(days=self.recent_window_days)
                window_cutoff = cutoff_dt.isoformat() + "Z"
            except Exception:
                pass

            high_value_ids: Set[str] = set()
            in_window_ids: Set[str] = set()
            to_archive: List[Any] = []

            for note in all_notes:
                nid = getattr(note, "note_id", "")
                conf = float(getattr(note, "confidence", 0.0) or 0.0)
                ts = getattr(note, "timestamp", "")
                if conf >= self.high_value_min_confidence:
                    high_value_ids.add(nid)
                if ts and ts >= window_cutoff:
                    in_window_ids.add(nid)
                # 既非高价值又不在窗口内 → 可 archive
                if nid not in high_value_ids and nid not in in_window_ids:
                    to_archive.append(note)

            report.reflections_high_value = len(high_value_ids)
            report.reflections_in_window = len(in_window_ids)

            for note in to_archive:
                nid = getattr(note, "note_id", "")
                if not dry_run:
                    self._archived_reflections.add(nid)
                report.reflections_archived += 1
                report.reflections_archived_ids.append(nid)

            if not dry_run:
                report.applied = True
        except Exception as e:
            report.errors.append(f"enforce_reflection_quota: {e}")
            logger.warning(f"SelfModelRetention.enforce_reflection_quota 失败: {e}")
        return report

    # ============================================================
    # 一键执行
    # ============================================================

    def enforce_all(
        self,
        beliefs_store: Any,
        history: Any,
        reflections_store: Any,
        dry_run: bool = False,
    ) -> RetentionReport:
        """执行全部三类对象的配额。"""
        belief_report = self.enforce_belief_quota(beliefs_store, dry_run=dry_run)
        history_report = self.enforce_history_quota(history, dry_run=dry_run)
        reflection_report = self.enforce_reflection_quota(reflections_store, dry_run=dry_run)

        merged = RetentionReport(dry_run=dry_run)
        merged.applied = belief_report.applied or history_report.applied or reflection_report.applied
        merged.beliefs_total = belief_report.beliefs_total
        merged.beliefs_active_before = belief_report.beliefs_active_before
        merged.beliefs_active_after = belief_report.beliefs_active_after
        merged.beliefs_deactivated = belief_report.beliefs_deactivated
        merged.beliefs_deactivated_ids = belief_report.beliefs_deactivated_ids
        merged.history_total = history_report.history_total
        merged.history_in_memory_before = history_report.history_in_memory_before
        merged.history_archived = history_report.history_archived
        merged.history_archived_ids = history_report.history_archived_ids
        merged.history_importance_assigned = history_report.history_importance_assigned
        merged.reflections_total = reflection_report.reflections_total
        merged.reflections_in_window = reflection_report.reflections_in_window
        merged.reflections_high_value = reflection_report.reflections_high_value
        merged.reflections_archived = reflection_report.reflections_archived
        merged.reflections_archived_ids = reflection_report.reflections_archived_ids
        merged.errors = belief_report.errors + history_report.errors + reflection_report.errors
        merged.warnings = belief_report.warnings + history_report.warnings + reflection_report.warnings
        return merged

    # ============================================================
    # 摘要
    # ============================================================

    def get_archive_summary(self) -> Dict[str, Any]:
        return {
            "beliefs_archived_count": len(self._archived_beliefs),
            "history_archived_count": len(self._archived_history),
            "reflections_archived_count": len(self._archived_reflections),
            "beliefs_archived_ids": list(self._archived_beliefs)[:20],
            "history_archived_ids": list(self._archived_history)[:20],
            "reflections_archived_ids": list(self._archived_reflections)[:20],
            "thresholds": {
                "max_active_beliefs": self.max_active_beliefs,
                "min_confidence_for_active": self.min_confidence_for_active,
                "max_in_memory_events": self.max_in_memory_events,
                "importance_low_below": self.importance_low_below,
                "recent_window_days": self.recent_window_days,
                "high_value_min_confidence": self.high_value_min_confidence,
            },
        }

    def reset(self) -> None:
        """重置所有 archive 状态（仅用于测试）"""
        self._archived_beliefs.clear()
        self._archived_history.clear()
        self._archived_reflections.clear()
        self._importance_tracker.clear()


# ============================================================
# 工厂函数
# ============================================================

def create_default_retention() -> SelfModelRetention:
    """创建默认配额的 Retention 实例"""
    return SelfModelRetention()


__all__ = [
    "SelfModelRetention",
    "RetentionReport",
    "ImportanceTracker",
    "create_default_retention",
    "DEFAULT_MAX_ACTIVE_BELIEFS",
    "DEFAULT_MIN_CONFIDENCE_FOR_ACTIVE",
    "DEFAULT_MAX_IN_MEMORY_EVENTS",
    "DEFAULT_IMPORTANCE_LOW_BELOW",
    "DEFAULT_RECENT_WINDOW_DAYS",
    "DEFAULT_HIGH_VALUE_MIN_CONFIDENCE",
]
