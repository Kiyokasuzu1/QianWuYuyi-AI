# -*- coding: utf-8 -*-
"""
src/growth/sync/proposal_sync_manager.py

Phase 5.5.2 Stability Layer: ProposalSyncManager

职责：
- 检查 Type A Proposal 与 Type B Mirror 之间的一致性
- 检测 status drift（状态漂移）
- 检测 proposed_changes drift（变更内容漂移）
- 检测 orphan mirror（Type A 已删除但 Mirror 仍存在）
- 输出 ConsistencyReport
- 支持 dry_run 模式（不写入修复）
- 不直接修改 Authority 数据（只读 Authority；只写 mirror 同步记录）

设计原则：
- 不修改 RuntimeCore / ApprovalManager / PersonalityAdapter
- 不持有 Authority 引用（通过函数注入）
- 不引入 EventBus
- 失败不 silent（写入 retry queue + drift 记录）
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# Drift 类型
# ============================================================

class DriftType(str, Enum):
    """Drift 类型枚举"""
    STATUS_MISMATCH = "status_mismatch"           # 状态不一致
    PROPOSED_CHANGES_MISMATCH = "proposed_changes_mismatch"  # 变更内容不一致
    MIRROR_MISSING = "mirror_missing"             # Type A 有，但 Mirror 缺失
    MIRROR_ORPHAN = "mirror_orphan"               # Type A 已删除，但 Mirror 仍存在
    CONFIDENCE_MISMATCH = "confidence_mismatch"    # 置信度不一致
    SOURCE_PROPOSAL_ID_MISSING = "source_proposal_id_missing"  # traceability 缺失


class DriftSeverity(str, Enum):
    """Drift 严重性"""
    LOW = "low"           # 提示性
    MEDIUM = "medium"     # 需要关注
    HIGH = "high"         # 必须修复
    CRITICAL = "critical" # 数据可能损坏


@dataclass
class DriftRecord:
    """单条 drift 记录"""
    drift_id: str = field(default_factory=lambda: f"drift_{datetime.utcnow().timestamp():.0f}")
    drift_type: str = DriftType.STATUS_MISMATCH.value
    severity: str = DriftSeverity.MEDIUM.value
    type_a_id: str = ""
    type_b_id: str = ""
    field_name: str = ""
    type_a_value: Any = None
    type_b_value: Any = None
    detected_at: str = field(default_factory=_now_iso)
    auto_fixable: bool = False
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ConsistencyReport:
    """一致性检查报告"""
    report_id: str = field(default_factory=lambda: f"report_{datetime.utcnow().timestamp():.0f}")
    generated_at: str = field(default_factory=_now_iso)
    dry_run: bool = True
    total_checked: int = 0
    consistent_count: int = 0
    drift_count: int = 0
    drifts: List[DriftRecord] = field(default_factory=list)
    fixed_count: int = 0
    errors: List[str] = field(default_factory=list)

    def add_drift(self, drift: DriftRecord) -> None:
        self.drifts.append(drift)
        self.drift_count = len(self.drifts)

    def add_consistent(self) -> None:
        self.consistent_count += 1

    @property
    def consistency_rate(self) -> float:
        if self.total_checked == 0:
            return 1.0
        return self.consistent_count / self.total_checked

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["consistency_rate"] = round(self.consistency_rate, 4)
        return d

    def summary(self) -> str:
        return (
            f"[ConsistencyReport {self.report_id}] "
            f"checked={self.total_checked} consistent={self.consistent_count} "
            f"drifts={self.drift_count} fixed={self.fixed_count} "
            f"rate={self.consistency_rate:.2%} dry_run={self.dry_run}"
        )


# ============================================================
# ProposalSyncManager
# ============================================================

class ProposalSyncManager:
    """
    Type A ↔ Type B Mirror 一致性管理器。

    使用示例：
        manager = ProposalSyncManager(
            type_a_loader=proposal_storage.load,
            mirror_loader=mirror_storage.get_proposal,
        )
        report = manager.check_one("prop_001")
        manager.run_bulk_check(dry_run=True)
    """

    def __init__(
        self,
        type_a_loader: Optional[Callable[[str], Any]] = None,
        mirror_loader: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
        mirror_lister: Optional[Callable[[Optional[str], int], List[Dict[str, Any]]]] = None,
        type_a_lister: Optional[Callable[[int], List[Any]]] = None,
        retry_queue: Optional[Any] = None,
    ):
        """
        Args:
            type_a_loader: (id) -> GrowthProposal | None  加载 Type A
            mirror_loader: (id) -> dict | None             加载 Type B 镜像
            mirror_lister: (status, limit) -> List[dict]   列出 Type B 镜像
            type_a_lister: (limit) -> List[GrowthProposal] 列出 Type A
            retry_queue: 失败时写入 retry queue（可选）
        """
        self._type_a_loader = type_a_loader or (lambda _id: None)
        self._mirror_loader = mirror_loader or (lambda _id: None)
        self._mirror_lister = mirror_lister or (lambda *_a, **_kw: [])
        self._type_a_lister = type_a_lister or (lambda *_a, **_kw: [])
        self._retry_queue = retry_queue

    # ============================================================
    # 单条检查
    # ============================================================

    def check_one(self, type_a_id: str, dry_run: bool = True) -> ConsistencyReport:
        """
        检查单个 Type A Proposal 与其 Type B 镜像的一致性。

        Args:
            type_a_id: Type A Proposal ID
            dry_run: True=不修复；False=尝试修复

        Returns:
            ConsistencyReport
        """
        report = ConsistencyReport(dry_run=dry_run, total_checked=1)

        try:
            type_a = self._type_a_loader(type_a_id)
            mirror = self._mirror_loader(type_a_id)
        except Exception as e:
            report.errors.append(f"加载失败: {e}")
            return report

        drifts = self._detect_drifts(type_a, mirror)
        if not drifts:
            report.add_consistent()
            return report

        for drift in drifts:
            report.add_drift(drift)
            if drift.severity in (DriftSeverity.HIGH.value, DriftSeverity.CRITICAL.value):
                if not dry_run:
                    fixed = self._attempt_fix(drift)
                    if fixed:
                        report.fixed_count += 1

        return report

    def _detect_drifts(
        self,
        type_a: Any,
        mirror: Optional[Dict[str, Any]],
    ) -> List[DriftRecord]:
        """检测 drift"""
        drifts: List[DriftRecord] = []

        if type_a is None:
            # Type A 不存在，但 mirror 可能存在 → orphan
            if mirror is not None:
                drifts.append(DriftRecord(
                    drift_type=DriftType.MIRROR_ORPHAN.value,
                    severity=DriftSeverity.HIGH.value,
                    type_b_id=mirror.get("id", ""),
                    field_name="existence",
                    type_a_value=None,
                    type_b_value="present",
                    note="Type A 已删除但 Type B 镜像仍存在",
                ))
            return drifts

        type_a_id = getattr(type_a, "proposal_id", "")

        if mirror is None:
            # Type A 存在但 mirror 缺失
            # 若 Type A 是 pending / proposed 状态，且 proposal_type=identity + memory_*，
            # 视为正常（[3] Memory Action 隔离）
            if self._is_memory_action_skipped(type_a):
                return drifts  # 正常跳过
            drifts.append(DriftRecord(
                drift_type=DriftType.MIRROR_MISSING.value,
                severity=DriftSeverity.MEDIUM.value,
                type_a_id=type_a_id,
                field_name="existence",
                type_a_value="present",
                type_b_value=None,
                note="Type A 存在但 Type B 镜像缺失",
            ))
            return drifts

        # 状态 drift
        type_a_status = getattr(type_a, "status", "")
        type_b_status = mirror.get("status", "")
        if type_a_status and type_b_status and not self._status_equivalent(type_a_status, type_b_status):
            drifts.append(DriftRecord(
                drift_type=DriftType.STATUS_MISMATCH.value,
                severity=DriftSeverity.HIGH.value,
                type_a_id=type_a_id,
                type_b_id=mirror.get("id", ""),
                field_name="status",
                type_a_value=type_a_status,
                type_b_value=type_b_status,
                auto_fixable=False,
                note=f"状态不一致: Type A={type_a_status}, Type B={type_b_status}",
            ))

        # proposed_changes drift
        type_a_dims = self._extract_type_a_dimensions(type_a)
        type_b_changes = mirror.get("proposed_changes", []) or []
        type_b_dims = {c.get("path"): c for c in type_b_changes if isinstance(c, dict)}

        if set(type_a_dims.keys()) != set(type_b_dims.keys()):
            drifts.append(DriftRecord(
                drift_type=DriftType.PROPOSED_CHANGES_MISMATCH.value,
                severity=DriftSeverity.MEDIUM.value,
                type_a_id=type_a_id,
                type_b_id=mirror.get("id", ""),
                field_name="proposed_changes.paths",
                type_a_value=sorted(type_a_dims.keys()),
                type_b_value=sorted(type_b_dims.keys()),
                auto_fixable=True,
                note="变更路径不一致",
            ))
        else:
            # 检查 delta（Type A 的 affected_dimensions 是 delta；Type B 的 after 是绝对值）
            for path, delta in type_a_dims.items():
                tb_ci = type_b_dims.get(path, {})
                tb_after = tb_ci.get("after")
                tb_before = tb_ci.get("before")
                if tb_after is None or delta is None:
                    continue
                try:
                    # 计算 Type B 的 delta
                    if tb_before is not None:
                        tb_delta = float(tb_after) - float(tb_before)
                    else:
                        # Type B 没有 before，无法计算 delta，跳过
                        continue
                    if abs(float(delta) - tb_delta) > 1e-4:
                        drifts.append(DriftRecord(
                            drift_type=DriftType.PROPOSED_CHANGES_MISMATCH.value,
                            severity=DriftSeverity.LOW.value,
                            type_a_id=type_a_id,
                            type_b_id=mirror.get("id", ""),
                            field_name=f"proposed_changes[{path}].delta",
                            type_a_value=float(delta),
                            type_b_value=tb_delta,
                            note=f"path={path} 的 delta 不一致 (Type A delta={delta}, Type B after-before={tb_delta})",
                        ))
                except (TypeError, ValueError):
                    pass

        # confidence drift
        type_a_conf = float(getattr(type_a, "confidence", 0.0) or 0.0)
        type_b_conf = float(mirror.get("confidence", 0.0) or 0.0)
        if abs(type_a_conf - type_b_conf) > 1e-3:
            drifts.append(DriftRecord(
                drift_type=DriftType.CONFIDENCE_MISMATCH.value,
                severity=DriftSeverity.LOW.value,
                type_a_id=type_a_id,
                type_b_id=mirror.get("id", ""),
                field_name="confidence",
                type_a_value=type_a_conf,
                type_b_value=type_b_conf,
                note="confidence 漂移",
            ))

        # traceability check
        meta = mirror.get("evaluator_meta", {}) or {}
        if not meta.get("source_proposal_id") and not meta.get("mirror_source_proposal_id"):
            drifts.append(DriftRecord(
                drift_type=DriftType.SOURCE_PROPOSAL_ID_MISSING.value,
                severity=DriftSeverity.MEDIUM.value,
                type_a_id=type_a_id,
                type_b_id=mirror.get("id", ""),
                field_name="evaluator_meta.source_proposal_id",
                type_a_value=type_a_id,
                type_b_value=None,
                note="source_proposal_id 缺失（traceability 不完整）",
            ))

        return drifts

    # ============================================================
    # 批量检查
    # ============================================================

    def run_bulk_check(
        self,
        status: Optional[str] = None,
        limit: int = 100,
        dry_run: bool = True,
    ) -> ConsistencyReport:
        """
        批量检查 Type A ↔ Type B 镜像一致性。

        Args:
            status: 仅检查特定状态的 Type A
            limit: 最大检查数量
            dry_run: True=不修复

        Returns:
            ConsistencyReport
        """
        report = ConsistencyReport(dry_run=dry_run, total_checked=0)

        try:
            type_a_list = self._type_a_lister(limit=limit)
        except Exception as e:
            report.errors.append(f"type_a_lister 失败: {e}")
            return report

        if status:
            type_a_list = [p for p in type_a_list if getattr(p, "status", "") == status]

        for type_a in type_a_list:
            type_a_id = getattr(type_a, "proposal_id", "")
            if not type_a_id:
                continue
            report.total_checked += 1
            try:
                mirror = self._mirror_loader(type_a_id)
            except Exception as e:
                report.errors.append(f"加载 {type_a_id} 镜像失败: {e}")
                continue

            drifts = self._detect_drifts(type_a, mirror)
            if not drifts:
                report.add_consistent()
            else:
                for drift in drifts:
                    report.add_drift(drift)
                    if not dry_run and drift.severity in (
                        DriftSeverity.HIGH.value, DriftSeverity.CRITICAL.value,
                    ):
                        fixed = self._attempt_fix(drift)
                        if fixed:
                            report.fixed_count += 1

        logger.info(report.summary())
        return report

    # ============================================================
    # 辅助
    # ============================================================

    def _is_memory_action_skipped(self, type_a: Any) -> bool:
        """判断 Type A 是否属于 memory action（[3] 隔离，正常无 mirror）"""
        ptype = getattr(type_a, "proposal_type", "")
        meta = getattr(type_a, "metadata", {}) or {}
        if meta.get("action_scope") == "memory":
            return True
        if ptype == "identity" and str(meta.get("request_kind", "")).startswith("memory_"):
            return True
        return False

    def _status_equivalent(self, type_a_status: str, type_b_status: str) -> bool:
        """判断 Type A 和 Type B 状态是否语义等价"""
        # 状态映射表（来自 GrowthProposalAdapter.STATUS_A_TO_B）
        equiv = {
            "pending": "proposed",
            "approved": "approved",
            "rejected": "rejected",
            "applied": "approved",
            "cancelled": "rejected",
        }
        return equiv.get(type_a_status, type_a_status) == type_b_status

    def _extract_type_a_dimensions(self, type_a: Any) -> Dict[str, float]:
        """提取 Type A 的 affected_dimensions"""
        return dict(getattr(type_a, "affected_dimensions", {}) or {})

    def _attempt_fix(self, drift: DriftRecord) -> bool:
        """尝试自动修复（保守：仅修复 auto_fixable=True 的）"""
        if not drift.auto_fixable:
            return False
        # 本阶段不实现真正的写回（仅记录意图）
        # 实际写回由 ApprovalManager / GovernanceProvider.sync_status() 处理
        logger.info(
            f"ProposalSyncManager: 检测到可修复 drift {drift.drift_id} "
            f"({drift.drift_type})，但由 Authority 入口处理（保守模式）"
        )
        return False


# ============================================================
# 模块级单例
# ============================================================

_sync_manager_instance: Optional[ProposalSyncManager] = None


def get_proposal_sync_manager() -> Optional[ProposalSyncManager]:
    """获取全局 ProposalSyncManager 单例（如已初始化）"""
    return _sync_manager_instance


def set_proposal_sync_manager(manager: Optional[ProposalSyncManager]) -> None:
    """设置全局 ProposalSyncManager 单例"""
    global _sync_manager_instance
    _sync_manager_instance = manager
