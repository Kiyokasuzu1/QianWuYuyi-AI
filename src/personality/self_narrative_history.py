"""
自我叙事历史 (SelfNarrativeHistory) — Phase 12.2 最终版
追踪自我叙事版本变化，支持差异计算和 JSON 持久化。
增加 schema_version 以保证未来数据迁移兼容性。
"""
from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime

from src.memory.atomic_write import (
    atomic_write_json,
    backup_corrupt_file,
    get_path_lock,
)


@dataclass
class NarrativeSnapshot:
    """一次自我叙事快照"""
    version: int = 1
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    core_identity: str = "浅雾羽依"
    major_changes: List[str] = field(default_factory=list)
    changed_traits: Dict[str, float] = field(default_factory=dict)
    narrative_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "timestamp": self.timestamp,
            "core_identity": self.core_identity,
            "major_changes": self.major_changes,
            "changed_traits": self.changed_traits,
            "narrative_text": self.narrative_text,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NarrativeSnapshot":
        return cls(
            version=data.get("version", 1),
            timestamp=data.get("timestamp", ""),
            core_identity=data.get("core_identity", "浅雾羽依"),
            major_changes=data.get("major_changes", []),
            changed_traits=data.get("changed_traits", {}),
            narrative_text=data.get("narrative_text", ""),
        )


@dataclass
class NarrativeDiff:
    """两个快照之间的差异"""
    added_traits: List[str] = field(default_factory=list)
    removed_traits: List[str] = field(default_factory=list)
    changed_beliefs: List[str] = field(default_factory=list)
    new_origin: List[str] = field(default_factory=list)
    is_significant: bool = False

    @staticmethod
    def compute(prev: NarrativeSnapshot, current: NarrativeSnapshot) -> "NarrativeDiff":
        """计算两个快照之间的差异"""
        prev_traits = set(prev.changed_traits.keys())
        curr_traits = set(current.changed_traits.keys())

        added_traits = list(curr_traits - prev_traits)
        removed_traits = list(prev_traits - curr_traits)
        changed_beliefs = [
            change for change in current.major_changes
            if change not in prev.major_changes
        ]

        is_significant = bool(
            added_traits or removed_traits or changed_beliefs
        )

        return NarrativeDiff(
            added_traits=added_traits,
            removed_traits=removed_traits,
            changed_beliefs=changed_beliefs,
            new_origin=[],
            is_significant=is_significant,
        )


class SelfNarrativeHistory:
    """自我叙事历史管理器"""
    SCHEMA_VERSION = 1

    def __init__(self, max_snapshots: int = 10):
        self.max_snapshots = max_snapshots
        self.snapshots: List[NarrativeSnapshot] = []

    def add_snapshot(self, snapshot: NarrativeSnapshot) -> NarrativeDiff:
        """添加快照，返回与前一个版本的差异"""
        prev = self.snapshots[-1] if self.snapshots else None
        self.snapshots.append(snapshot)
        if len(self.snapshots) > self.max_snapshots:
            self.snapshots = self.snapshots[-self.max_snapshots:]

        if prev:
            return NarrativeDiff.compute(prev, snapshot)
        return NarrativeDiff(is_significant=True)

    def append_snapshot(
        self, snapshot: NarrativeSnapshot, filepath: Optional[str] = None
    ) -> NarrativeDiff:
        """v1.2: append-only 追加快照。

        - 与最新版本无显著差异时不新增（幂等：重复组装不产生重复版本）;
        - 保留旧版本（只裁剪超出 max_snapshots 的最旧快照）;
        - filepath 提供时立即原子落盘（复用 save 的锁 + 原子写）;
        - 任何异常 fail-soft：不抛出，仅放弃本次落盘。
        """
        try:
            latest = self.snapshots[-1] if self.snapshots else None
            if latest is not None:
                diff = NarrativeDiff.compute(latest, snapshot)
                if not diff.is_significant:
                    return diff
            diff = self.add_snapshot(snapshot)
            if filepath:
                try:
                    self.save(filepath)
                except Exception:
                    pass
            return diff
        except Exception:
            return NarrativeDiff()

    def get_recent(self, limit: int = 5) -> List[NarrativeSnapshot]:
        """v1.2: 返回最近 N 个快照（最新在前）。"""
        try:
            n = int(limit)
        except (TypeError, ValueError):
            n = 5
        if n <= 0:
            n = 5
        items = list(self.snapshots)
        items.reverse()
        return items[:n]

    def get_latest(self) -> NarrativeSnapshot:
        """获取最新快照"""
        if self.snapshots:
            return self.snapshots[-1]
        return NarrativeSnapshot()

    def get_changes(self, from_version: int, to_version: int) -> List[str]:
        """获取两个版本之间的变化"""
        changes = []
        for s in self.snapshots:
            if from_version < s.version <= to_version:
                changes.extend(s.major_changes)
        return changes

    def save(self, filepath: str):
        """持久化到 JSON 文件"""
        data = {
            "schema_version": self.SCHEMA_VERSION,
            "snapshots": [s.to_dict() for s in self.snapshots],
        }
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        # V1.1: 锁 + 原子写（替换裸 open("w") 截断写）
        with get_path_lock(str(path)):
            atomic_write_json(str(path), data)

    @classmethod
    def load(cls, filepath: str) -> "SelfNarrativeHistory":
        """从 JSON 文件加载（V1.1: 损坏时备份现场并返回空历史,不再抛出）"""
        history = cls()
        path = Path(filepath)
        if not path.exists():
            return history
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 未来可根据 schema_version 进行数据迁移
            history.snapshots = [
                NarrativeSnapshot.from_dict(s) for s in data.get("snapshots", [])
            ]
        except Exception:
            backup_corrupt_file(path)
        return history

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "snapshots": [s.to_dict() for s in self.snapshots],
        }