# -*- coding: utf-8 -*-
"""
src/runtime/policy/lifecycle/snapshot.py

Phase C.10.10 — Policy Snapshot / Restore

本文件实现:
- ThrottleSnapshot         throttle 单模块的 snapshot
- SnapshotDiff             snapshot 之间差异
- PolicySnapshotManager    创建 / 恢复 / 比较 snapshot

应用场景:
- Apply 之前:create_snapshot() 记录当前 ThrottleRegistry / RuntimeBudget
- Apply 失败:restore_snapshot() 恢复
- Verify:    compare_snapshot() 检查 apply 是否生效

设计原则:
- snapshot 是不可变 dict(创建后不可改)
- 任何异常 fail-soft,失败时 manager 返回空 snapshot
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# ThrottleSnapshot
# ============================================================
@dataclass
class ThrottleSnapshot:
    """单模块 throttle 快照(不可变)。"""

    module: str = ""
    interval: int = 0
    throttle: float = 1.0
    cooldown_seconds: float = 0.0
    captured_at: float = 0.0
    snapshot_id: str = ""

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            self.snapshot_id = f"snap_{uuid.uuid4().hex[:12]}"
        if not self.captured_at:
            self.captured_at = float(time.time())
        self.module = str(self.module or "").strip().lower()

    def to_dict(self) -> Dict[str, Any]:
        try:
            return {
                "snapshot_id": str(self.snapshot_id),
                "module": str(self.module),
                "interval": int(self.interval),
                "throttle": float(self.throttle),
                "cooldown_seconds": float(self.cooldown_seconds),
                "captured_at": float(self.captured_at),
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("ThrottleSnapshot.to_dict 异常(已隔离): %s", exc)
            return {
                "snapshot_id": str(self.snapshot_id),
                "module": "",
                "interval": 0,
                "throttle": 1.0,
                "cooldown_seconds": 0.0,
                "captured_at": 0.0,
            }


# ============================================================
# SnapshotDiff
# ============================================================
@dataclass
class SnapshotDiff:
    """两个 snapshot 之间的差异。

    字段:
    - module:           模块名
    - before:           before 状态(to_dict)
    - after:            after 状态(to_dict)
    - changed_fields:   改动的字段列表
    """

    module: str = ""
    before: Dict[str, Any] = field(default_factory=dict)
    after: Dict[str, Any] = field(default_factory=dict)
    changed_fields: List[str] = field(default_factory=list)

    @property
    def has_diff(self) -> bool:
        return len(self.changed_fields) > 0

    def to_dict(self) -> Dict[str, Any]:
        try:
            return {
                "module": str(self.module),
                "before": dict(self.before or {}),
                "after": dict(self.after or {}),
                "changed_fields": list(self.changed_fields or []),
                "has_diff": bool(self.has_diff),
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("SnapshotDiff.to_dict 异常(已隔离): %s", exc)
            return {
                "module": "",
                "before": {},
                "after": {},
                "changed_fields": [],
                "has_diff": False,
            }


# ============================================================
# PolicySnapshotManager
# ============================================================
class PolicySnapshotManager:
    """Snapshot 集中管理。

    接口:
    - create_snapshot(modules) → Dict[module, ThrottleSnapshot]
    - restore_snapshot(snapshots, throttle_registry) → bool
    - compare_snapshot(before, after) → SnapshotDiff
    - list_snapshots() / get_snapshot(id) / clear
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshots: Dict[str, ThrottleSnapshot] = {}
        # snapshot_id → {module: snapshot}
        self._groups: Dict[str, Dict[str, ThrottleSnapshot]] = {}

    # --------------------------------------------------------
    # 创建 snapshot
    # --------------------------------------------------------
    def create_snapshot(
        self,
        throttle_registry: Any = None,
        modules: Optional[List[str]] = None,
        group_id: str = "",
    ) -> Dict[str, ThrottleSnapshot]:
        """从 ThrottleRegistry 捕获一组 module 的 snapshot。

        - throttle_registry: ThrottleRegistry 实例(或 None)
        - modules:            要捕获的模块列表(None = 全部已注册)
        - group_id:           snapshot 组 ID(便于回滚定位)

        返回:{module: ThrottleSnapshot};任何异常 → 返回 {}
        """
        snapshots: Dict[str, ThrottleSnapshot] = {}
        try:
            if throttle_registry is None:
                return snapshots
            try:
                registry_snap = throttle_registry.snapshot() or {}
            except Exception as exc:  # noqa: BLE001
                logger.debug("PolicySnapshotManager.create_snapshot 异常: %s", exc)
                return snapshots
            target_modules: List[str]
            if modules:
                target_modules = [str(m).strip().lower() for m in modules if m]
            else:
                target_modules = list(registry_snap.keys())
            for module in target_modules:
                try:
                    state = registry_snap.get(module) or {}
                    snap = ThrottleSnapshot(
                        module=str(module),
                        interval=int(state.get("interval", 0) or 0),
                        throttle=float(state.get("throttle", 1.0) or 1.0),
                        cooldown_seconds=float(
                            state.get("cooldown_seconds", 0.0) or 0.0
                        ),
                    )
                    snapshots[str(module)] = snap
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "create_snapshot(%s) 异常(已隔离): %s", module, exc,
                    )
            with self._lock:
                for snap in snapshots.values():
                    self._snapshots[snap.snapshot_id] = snap
                if snapshots:
                    gid = group_id or f"grp_{uuid.uuid4().hex[:10]}"
                    self._groups[gid] = dict(snapshots)
            return snapshots
        except Exception as exc:  # noqa: BLE001
            logger.debug("PolicySnapshotManager.create_snapshot 顶层异常: %s", exc)
            return snapshots

    # --------------------------------------------------------
    # 恢复 snapshot
    # --------------------------------------------------------
    def restore_snapshot(
        self,
        snapshots: Dict[str, ThrottleSnapshot],
        throttle_registry: Any = None,
    ) -> bool:
        """根据 snapshot 恢复 ThrottleRegistry 状态。

        任何异常 → fail-soft,部分恢复成功也返回 True
        """
        if throttle_registry is None:
            return False
        try:
            ok = False
            for module, snap in (snapshots or {}).items():
                try:
                    set_method = getattr(throttle_registry, "set", None)
                    if callable(set_method):
                        set_method(
                            module=str(module),
                            interval=int(snap.interval),
                            throttle=float(snap.throttle),
                            cooldown_seconds=float(snap.cooldown_seconds),
                        )
                        ok = True
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "restore_snapshot(%s) 异常(已隔离): %s", module, exc,
                    )
            return ok
        except Exception as exc:  # noqa: BLE001
            logger.debug("PolicySnapshotManager.restore_snapshot 异常: %s", exc)
            return False

    # --------------------------------------------------------
    # 比较 snapshot
    # --------------------------------------------------------
    def compare_snapshot(
        self,
        before: ThrottleSnapshot,
        after: ThrottleSnapshot,
    ) -> SnapshotDiff:
        """比较两个 snapshot,返回差异。"""
        try:
            if before is None or after is None:
                return SnapshotDiff()
            b = before.to_dict()
            a = after.to_dict()
            changed: List[str] = []
            for key in ("interval", "throttle", "cooldown_seconds"):
                if b.get(key) != a.get(key):
                    changed.append(key)
            return SnapshotDiff(
                module=str(before.module or after.module or ""),
                before=b,
                after=a,
                changed_fields=changed,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("compare_snapshot 异常(已隔离): %s", exc)
            return SnapshotDiff()

    def compare_to_current(
        self,
        snapshot: ThrottleSnapshot,
        throttle_registry: Any = None,
    ) -> SnapshotDiff:
        """比较 snapshot 与 ThrottleRegistry 当前状态。"""
        try:
            if throttle_registry is None or snapshot is None:
                return SnapshotDiff()
            try:
                registry_snap = throttle_registry.snapshot() or {}
            except Exception:  # noqa: BLE001
                return SnapshotDiff()
            state = registry_snap.get(snapshot.module) or {}
            current = ThrottleSnapshot(
                module=str(snapshot.module),
                interval=int(state.get("interval", 0) or 0),
                throttle=float(state.get("throttle", 1.0) or 1.0),
                cooldown_seconds=float(
                    state.get("cooldown_seconds", 0.0) or 0.0
                ),
            )
            return self.compare_snapshot(snapshot, current)
        except Exception as exc:  # noqa: BLE001
            logger.debug("compare_to_current 异常(已隔离): %s", exc)
            return SnapshotDiff()

    # --------------------------------------------------------
    # snapshot 查询
    # --------------------------------------------------------
    def get_snapshot(self, snapshot_id: str) -> Optional[ThrottleSnapshot]:
        try:
            with self._lock:
                return self._snapshots.get(str(snapshot_id or ""))
        except Exception:  # noqa: BLE001
            return None

    def list_snapshots(self) -> List[ThrottleSnapshot]:
        try:
            with self._lock:
                return list(self._snapshots.values())
        except Exception:  # noqa: BLE001
            return []

    def get_group(self, group_id: str) -> Dict[str, ThrottleSnapshot]:
        try:
            with self._lock:
                g = self._groups.get(str(group_id or ""))
                return dict(g) if g else {}
        except Exception:  # noqa: BLE001
            return {}

    def list_groups(self) -> List[str]:
        try:
            with self._lock:
                return list(self._groups.keys())
        except Exception:  # noqa: BLE001
            return []

    def snapshot(self) -> Dict[str, Any]:
        try:
            with self._lock:
                return {
                    "total_snapshots": len(self._snapshots),
                    "total_groups": len(self._groups),
                    "groups": list(self._groups.keys()),
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("PolicySnapshotManager.snapshot 异常: %s", exc)
            return {"total_snapshots": 0, "total_groups": 0, "groups": []}

    def clear(self) -> None:
        """测试用。"""
        with self._lock:
            self._snapshots.clear()
            self._groups.clear()


# ============================================================
# 工厂
# ============================================================
def build_throttle_snapshot(
    module: str,
    interval: int = 0,
    throttle: float = 1.0,
    cooldown_seconds: float = 0.0,
) -> ThrottleSnapshot:
    return ThrottleSnapshot(
        module=str(module or "").strip().lower(),
        interval=int(interval),
        throttle=float(throttle),
        cooldown_seconds=float(cooldown_seconds),
    )


def build_default_snapshot_manager() -> PolicySnapshotManager:
    return PolicySnapshotManager()
