"""
SelfModelSnapshot (Phase 6.1)

SelfModel 在某个时间点的完整可回滚快照。

职责：
- 保存 SelfIdentity + SelfBeliefs + Understanding 完整状态
- 提供 rollback 接口
- 提供持久化
- 与 SelfHistory 配合：每次快照前向 SelfHistory 写入 SNAPSHOT_CREATED 事件
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import uuid


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# SelfModelSnapshot
# ============================================================

@dataclass
class SelfModelSnapshot:
    """
    SelfModel 在某时间点的完整快照。

    - snapshot_id: 唯一
    - timestamp: ISO8601
    - self_identity: SelfIdentity.to_dict()（来自 contracts.self_model_schema）
    - self_beliefs: List[SelfBelief.to_dict()]
    - history_event_count: SelfHistory 在快照时的 size
    - understanding: Dict[str, float]（experience_awareness / trait_awareness / ...）
    - version: SelfIdentity.version
    - trigger_event_id: 触发此快照的事件 ID（可选）
    - note: 快照说明
    """

    snapshot_id: str = field(default_factory=lambda: f"snap_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=_now_iso)
    self_identity: Dict[str, Any] = field(default_factory=dict)
    self_beliefs: List[Dict[str, Any]] = field(default_factory=list)
    history_event_count: int = 0
    understanding: Dict[str, float] = field(default_factory=dict)
    version: int = 1
    trigger_event_id: Optional[str] = None
    note: str = ""

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not isinstance(self.self_identity, dict):
            errors.append("self_identity must be dict")
        if not isinstance(self.self_beliefs, list):
            errors.append("self_beliefs must be list")
        if self.history_event_count < 0:
            errors.append(f"history_event_count must be >= 0: {self.history_event_count}")
        if not isinstance(self.understanding, dict):
            errors.append("understanding must be dict")
        if self.version < 1:
            errors.append(f"version must be >= 1: {self.version}")
        return errors

    def is_valid(self) -> bool:
        return len(self.validate()) == 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SelfModelSnapshot":
        return cls(
            snapshot_id=data.get("snapshot_id") or f"snap_{uuid.uuid4().hex[:10]}",
            timestamp=data.get("timestamp", _now_iso()),
            self_identity=dict(data.get("self_identity") or {}),
            self_beliefs=list(data.get("self_beliefs") or []),
            history_event_count=int(data.get("history_event_count", 0)),
            understanding=dict(data.get("understanding") or {}),
            version=int(data.get("version", 1)),
            trigger_event_id=data.get("trigger_event_id"),
            note=data.get("note", ""),
        )


# ============================================================
# SelfModelSnapshotStore
# ============================================================

class SelfModelSnapshotStore:
    """
    快照容器 + 持久化 + 回滚支持。

    不直接修改 SelfModelStore；仅保存 / 提供快照。
    实际应用由 SelfModelAdapter 完成。
    """

    MAX_SNAPSHOTS_DEFAULT = 50

    def __init__(
        self,
        storage_path: Optional[str] = None,
        max_snapshots: int = MAX_SNAPSHOTS_DEFAULT,
    ) -> None:
        self._snapshots: List[SelfModelSnapshot] = []
        self._max = max_snapshots
        self._path: Optional[Path] = None
        if storage_path:
            self._path = Path(storage_path)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._load()

    # ============================================================
    # CRUD
    # ============================================================

    def add(self, snapshot: SelfModelSnapshot) -> bool:
        if not snapshot.is_valid():
            return False
        self._snapshots.append(snapshot)
        self._prune()
        self._save()
        return True

    def get(self, snapshot_id: str) -> Optional[SelfModelSnapshot]:
        for s in self._snapshots:
            if s.snapshot_id == snapshot_id:
                return s
        return None

    def all(self) -> List[SelfModelSnapshot]:
        return list(self._snapshots)

    def count(self) -> int:
        return len(self._snapshots)

    def latest(self, n: int = 10) -> List[SelfModelSnapshot]:
        return self._snapshots[-n:]

    def latest_one(self) -> Optional[SelfModelSnapshot]:
        return self._snapshots[-1] if self._snapshots else None

    def clear(self) -> None:
        self._snapshots.clear()
        if self._path and self._path.exists():
            try:
                self._path.unlink()
            except Exception:
                pass

    # ============================================================
    # 回滚
    # ============================================================

    def rollback_to(self, snapshot_id: str) -> Optional[SelfModelSnapshot]:
        """
        返回指定 snapshot（由 SelfModelAdapter 实际应用）。
        不直接修改 SelfModelStore。
        """
        return self.get(snapshot_id)

    def rollback_to_latest(self) -> Optional[SelfModelSnapshot]:
        return self.latest_one()

    # ============================================================
    # 内部
    # ============================================================

    def _prune(self) -> None:
        if len(self._snapshots) > self._max:
            self._snapshots = self._snapshots[-self._max:]

    def _save(self) -> None:
        if not self._path:
            return
        try:
            data = {
                "version": "1.0",
                "max_snapshots": self._max,
                "count": len(self._snapshots),
                "snapshots": [s.to_dict() for s in self._snapshots],
            }
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        except Exception:
            # 不抛出
            pass

    def _load(self) -> None:
        if not self._path or not self._path.exists():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._snapshots = []
            for s in (data.get("snapshots") or []):
                snap = SelfModelSnapshot.from_dict(s)
                if snap.is_valid():
                    self._snapshots.append(snap)
        except Exception:
            self._snapshots = []
