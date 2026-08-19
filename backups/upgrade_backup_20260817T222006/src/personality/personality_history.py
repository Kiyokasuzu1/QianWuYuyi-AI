"""
人格历史记录 (PersonalityHistory) v1.0

职责：
保存羽依人格变化的完整轨迹，使“为什么变成这样”可追溯。

设计原则：
- 每次核心维度有显著变化（>0.005）时生成快照
- 保存 before/after 完整维度值
- 关联触发事件 ID，可追溯原因
"""

from typing import Dict, List, TypedDict, Optional
from datetime import datetime


class PersonalitySnapshot(TypedDict, total=False):
    """人格变化快照"""

    snapshot_id: str
    timestamp: str
    before_traits: Dict[str, float]
    after_traits: Dict[str, float]
    trigger_event_id: Optional[str]
    reason: str
    changed_dimensions: List[str]


class PersonalityHistory:
    """人格历史记录管理器"""

    def __init__(self):
        self.snapshots: List[PersonalitySnapshot] = []

    def record_change(
        self,
        before: Dict[str, float],
        after: Dict[str, float],
        trigger_event_id: Optional[str] = None,
        reason: str = "",
    ) -> Optional[PersonalitySnapshot]:
        changed_dimensions = []
        all_dims = set(before.keys()) | set(after.keys())
        for dim in all_dims:
            if abs(after.get(dim, 0.0) - before.get(dim, 0.0)) > 0.005:
                changed_dimensions.append(dim)

        if not changed_dimensions:
            return None

        snapshot: PersonalitySnapshot = {
            "snapshot_id": f"snap_{len(self.snapshots):04d}",
            "timestamp": datetime.now().isoformat(),
            "before_traits": before.copy(),
            "after_traits": after.copy(),
            "trigger_event_id": trigger_event_id,
            "reason": reason,
            "changed_dimensions": changed_dimensions,
        }
        self.snapshots.append(snapshot)
        return snapshot

    def get_recent_changes(self, n: int = 5) -> List[PersonalitySnapshot]:
        return self.snapshots[-n:] if self.snapshots else []

    def get_changes_for_dimension(self, trait: str) -> List[PersonalitySnapshot]:
        return [
            snap for snap in self.snapshots
            if trait in snap.get("changed_dimensions", [])
        ]

    def latest(self) -> Optional[PersonalitySnapshot]:
        return self.snapshots[-1] if self.snapshots else None

    def count(self) -> int:
        return len(self.snapshots)