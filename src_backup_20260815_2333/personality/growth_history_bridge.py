"""
Phase B.1.4 — GrowthHistoryBridge

职责：
- 把 PersonalityGrowthHistory.records 转换为 SelfModel 需要的 growth_history 视图
- 提供查询接口：
    - recent_growth_events(n)
    - growth_count()
    - important_changes(min_confidence)

设计原则：
- 只读 PersonalityGrowthHistory,不修改
- 返回的 dict 是新对象（深拷贝），不污染 store 内部状态
- 不直接写 SelfModel，由 SelfModelStore 调用本模块来更新 growth_history 字段
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime


def now_iso() -> str:
    return datetime.now().isoformat()


class GrowthHistoryBridge:
    """PersonalityGrowthHistory → SelfModel.growth_history 桥接器。"""

    def __init__(self, growth_history: Any = None):
        """
        Args:
            growth_history: PersonalityGrowthHistory 实例（提供 .records / .all() / .count()）
        """
        self._growth_history = growth_history

    def set_growth_history(self, growth_history: Any) -> None:
        """注入或替换 growth_history 实例。"""
        self._growth_history = growth_history

    # ============================================================
    # build_growth_history_view
    # ============================================================
    def build_growth_history_view(self) -> Dict[str, Any]:
        """从 PersonalityGrowthHistory 构建 SelfModel.growth_history 视图。

        Returns:
            {
                "records": [ {id, event, evidence, impact, confidence, created_at, ...} ],
                "total_count": int,
                "last_updated": str,
                "high_impact_count": int,
            }
        """
        records = self._safe_records()
        normalized = [self._normalize(r) for r in records]

        # 高影响记录：confidence >= 0.8 且 growth_level == "trait"
        high_impact = sum(
            1 for r in normalized
            if r.get("confidence", 0) >= 0.8 and r.get("growth_level") == "trait"
        )

        last_updated = ""
        if normalized:
            timestamps = [r.get("created_at", "") for r in normalized if r.get("created_at")]
            last_updated = max(timestamps) if timestamps else now_iso()
        else:
            last_updated = now_iso()

        return {
            "records": normalized,
            "total_count": len(normalized),
            "last_updated": last_updated,
            "high_impact_count": high_impact,
        }

    # ============================================================
    # 查询接口
    # ============================================================
    def recent_growth_events(self, n: int = 5) -> List[Dict[str, Any]]:
        """返回最近 n 条成长事件。"""
        records = self._safe_records()
        normalized = [self._normalize(r) for r in records]
        # 按 created_at 降序
        normalized.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return normalized[:n]

    def growth_count(self) -> int:
        """返回总成长记录数。"""
        records = self._safe_records()
        return len(records)

    def important_changes(self, min_confidence: float = 0.8) -> List[Dict[str, Any]]:
        """返回 confidence >= min_confidence 的重要变化。"""
        records = self._safe_records()
        normalized = [self._normalize(r) for r in records]
        return [r for r in normalized if r.get("confidence", 0) >= min_confidence]

    def changes_by_dimension(self, dimension: str) -> List[Dict[str, Any]]:
        """返回影响指定维度的所有成长记录。"""
        records = self._safe_records()
        result = []
        for r in records:
            affected = r.get("affected_dimensions") or []
            if dimension in affected:
                result.append(self._normalize(r))
        return result

    # ============================================================
    # 内部辅助
    # ============================================================
    def _safe_records(self) -> List[Dict[str, Any]]:
        if self._growth_history is None:
            return []
        try:
            if hasattr(self._growth_history, "all"):
                return list(self._growth_history.all() or [])
            if hasattr(self._growth_history, "records"):
                return list(self._growth_history.records or [])
        except Exception:
            return []
        return []

    @staticmethod
    def _normalize(record: Any) -> Dict[str, Any]:
        """把 PersonalityGrowthRecord 标准化为 SelfModel 视图。"""
        if not isinstance(record, dict):
            return {
            "id": "",
            "event": str(record),
            "evidence": [],
            "impact": "",
            "confidence": 0.0,
            "created_at": "",
            "growth_level": "",
            "affected_dimensions": [],
            "source_growth_record_id": "",
            "evidence_ids": [],
        }

        affected = record.get("affected_dimensions") or []
        changes = record.get("changes") or {}

        # event 描述：拼接 first 触发事件 + 主要 affected dim
        trigger = record.get("trigger_events") or []
        event_text = ""
        if trigger:
            event_text = f"trigger: {trigger[0]}"
        if affected:
            suffix = ", ".join(str(a) for a in affected[:3])
            event_text = f"{event_text} -> [{suffix}]" if event_text else f"affected: [{suffix}]"

        # impact：从 changes 聚合 delta
        total_delta = 0.0
        for trait, ch in changes.items():
            if isinstance(ch, dict):
                try:
                    total_delta += abs(float(ch.get("delta", 0.0) or 0.0))
                except Exception:
                    pass
        impact = round(total_delta, 4) if total_delta else 0.0

        return {
            "id": record.get("record_id", ""),
            "event": event_text or record.get("meaning", ""),
            "evidence": list(trigger),
            "impact": impact,
            "confidence": float(record.get("confidence", 0.0) or 0.0),
            "created_at": record.get("timestamp", ""),
            "growth_level": record.get("growth_level", ""),
            "affected_dimensions": list(affected),
            "meaning": record.get("meaning", ""),
            "source_proposal_id": record.get("source_proposal_id", ""),
            "source_growth_record_id": record.get("source_growth_record_id") or "",
            "evidence_ids": record.get("evidence_ids") or [],
        }
