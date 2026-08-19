# -*- coding: utf-8 -*-
"""
src/runtime/adapters/growth_proposal_mirror.py

Phase 5.5 Integration + Phase 5.5.1 Hotfix

目的：
- 提供独立于 GrowthAdapter 的 Type B 镜像存储
- 供 ApprovalManager 读取（通过 MirrorReadOnlyAdapter 适配接口）
- 不修改 GrowthAdapter / RuntimeCore / Orchestrator
- 不删除任何旧 Schema

强约束：
1. 镜像存储只用于 Type A → Type B 的镜像写入，不参与任何反射/生成
2. 镜像存储与 GrowthAdapter 物理隔离（不同文件、不同模块）
3. 提供只读 MirrorReadOnlyAdapter 用于 ApprovalManager 透明读取
4. 不持有任何 Authority 引用

Phase 5.5.1 Hotfix 改动：
- [2] Mirror Authority 修正: 引入 MirrorReadOnlyAdapter（只读），保留 MirrorBackedAdapter
  用于向后兼容（已 deprecated）
- 状态变更唯一来源：GovernanceProvider.sync_status()
  禁止外部（包括 ApprovalManager）直接修改镜像状态

存储位置：data/growth/type_b_mirror/mirror_proposals.json
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


class GrowthProposalMirrorStorage:
    """
    Type B GrowthProposal 镜像存储（Phase 5.5 Integration 阶段新增）。

    职责：
    - 存储经 GrowthProposalAdapter 转换后的 Type B 镜像
    - 提供与 GrowthAdapter 兼容的最小查询接口
    - 状态同步：Type A 状态变更时可同步更新 Type B 镜像

    限制：
    - 镜像存储不参与 Runtime Growth 流程
    - 镜像存储不参与 Reflection → Growth 链路
    - 仅服务于 Admin Proposal → ApprovalManager 的桥接
    """

    DEFAULT_PATH = "data/growth/type_b_mirror/mirror_proposals.json"

    def __init__(self, storage_path: Optional[str] = None):
        """
        Args:
            storage_path: 自定义镜像存储路径（测试时可注入临时目录）
        """
        self._path = Path(storage_path or self.DEFAULT_PATH)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._init_file()

    def _init_file(self) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump({"version": "1.0", "proposals": []}, f, ensure_ascii=False, indent=2)

    def _load(self) -> List[Dict[str, Any]]:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("proposals", [])
        except Exception as e:
            logger.error(f"GrowthProposalMirrorStorage 加载失败: {e}")
            return []

    def _save(self, proposals: List[Dict[str, Any]]) -> None:
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(
                    {"version": "1.0", "proposals": proposals},
                    f, ensure_ascii=False, indent=2,
                )
        except Exception as e:
            logger.error(f"GrowthProposalMirrorStorage 保存失败: {e}")

    # ============================================================
    # 写入接口
    # ============================================================

    def save_type_b(self, type_b_dict: Dict[str, Any]) -> bool:
        """
        保存 Type B 镜像（已序列化的 dict）。

        Args:
            type_b_dict: Type B GrowthProposal.to_dict() 序列化结果

        Returns:
            是否成功
        """
        try:
            pid = type_b_dict.get("id", "")
            if not pid:
                logger.error("GrowthProposalMirrorStorage.save_type_b: 缺少 id")
                return False
            proposals = self._load()
            found = False
            for i, p in enumerate(proposals):
                if p.get("id") == pid:
                    proposals[i] = type_b_dict
                    found = True
                    break
            if not found:
                proposals.append(type_b_dict)
            self._save(proposals)
            logger.debug(f"GrowthProposalMirrorStorage: 镜像已保存 {pid}")
            return True
        except Exception as e:
            logger.error(f"GrowthProposalMirrorStorage.save_type_b 失败: {e}")
            return False

    def update_status(
        self,
        proposal_id: str,
        new_status: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        更新 Type B 镜像状态（与 Type A 同步）。

        Args:
            proposal_id: 提案 ID
            new_status: 目标状态（proposed/approved/rejected）
            extra: 额外元数据

        Returns:
            是否成功
        """
        try:
            proposals = self._load()
            for p in proposals:
                if p.get("id") == proposal_id:
                    p["status"] = new_status
                    if extra:
                        meta = p.get("evaluator_meta", {}) or {}
                        meta.update(extra)
                        p["evaluator_meta"] = meta
                    if new_status == "approved":
                        p["accepted_at"] = _now_iso()
                    elif new_status == "rejected":
                        p["rejected_at"] = _now_iso()
                    self._save(proposals)
                    return True
            return False
        except Exception as e:
            logger.error(f"GrowthProposalMirrorStorage.update_status 失败: {e}")
            return False

    # ============================================================
    # 读取接口（与 GrowthAdapter 兼容的子集）
    # ============================================================

    def list_proposals(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        列出 Type B 镜像（按时间倒序）。

        Args:
            status: 状态过滤
            limit: 最大数量

        Returns:
            镜像列表（dict 形式，可由 MirrorBackedAdapter 转回 GrowthProposal）
        """
        proposals = self._load()
        if status:
            proposals = [p for p in proposals if p.get("status") == status]
        proposals.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return proposals[:limit]

    def get_proposal(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        """按 ID 获取 Type B 镜像"""
        for p in self._load():
            if p.get("id") == proposal_id:
                return p
        return None

    def count(self) -> int:
        return len(self._load())

    def clear(self) -> None:
        """清空所有镜像（仅供测试）"""
        self._save([])


# ============================================================
# Phase 5.5.1 Hotfix [2]: MirrorReadOnlyAdapter（只读，Authority 隔离）
# ============================================================

class MirrorReadOnlyAdapter:
    """
    将 GrowthProposalMirrorStorage 包装为只读接口，
    供 ApprovalManager 安全读取（不修改状态）。

    Phase 5.5.1 Hotfix [2] Mirror Authority 修正：
    - 禁止调用方通过本接口修改状态
    - 状态变更唯一来源：GovernanceProvider.sync_status()
    - 不修改 ApprovalManager / GrowthAdapter

    设计：
    - 显式只提供 get_proposal / list_proposals
    - 不暴露 accept_proposal / reject_proposal / update_proposal
    - 防止 ApprovalManager 误改镜像状态
    """

    def __init__(self, mirror_storage: GrowthProposalMirrorStorage):
        self._mirror = mirror_storage

    def get_proposal(self, proposal_id: str, **kwargs: Any) -> Optional[Any]:
        """获取单个提案（只读）"""
        from src.contracts.growth_schema import GrowthProposal
        data = self._mirror.get_proposal(proposal_id)
        if data is None:
            return None
        try:
            return GrowthProposal.from_dict(data)
        except Exception:
            return None

    def list_proposals(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        **kwargs: Any,
    ) -> List[Any]:
        """列出提案（只读）"""
        from src.contracts.growth_schema import GrowthProposal
        items = self._mirror.list_proposals(status=status, limit=limit)
        out: List[Any] = []
        for d in items:
            try:
                out.append(GrowthProposal.from_dict(d))
            except Exception:
                continue
        return out

    # 明确不提供：accept_proposal / reject_proposal / update_proposal
    # 任何尝试调用这些方法的代码应使用 MirrorBackedAdapter（已 deprecated）


# ============================================================
# MirrorBackedAdapter（DEPRECATED - 仅供向后兼容）
# ============================================================

class MirrorBackedAdapter:
    """
    将 GrowthProposalMirrorStorage 包装为与 GrowthAdapter 兼容的接口。

    ⚠️ Phase 5.5.1 Hotfix [2]: 已 deprecated。
    状态变更应通过 GovernanceProvider.sync_status() 统一处理，
    不应由本接口直接修改（违反 Authority 边界）。

    仅保留用于：
    1. 向后兼容（Phase 5.5 Integration 阶段测试）
    2. 单元测试中模拟可写 Adapter

    新代码应使用 MirrorReadOnlyAdapter。
    """

    def __init__(self, mirror_storage: GrowthProposalMirrorStorage):
        self._mirror = mirror_storage

    def get_proposal(self, proposal_id: str, **kwargs: Any) -> Optional[Any]:
        """获取单个提案"""
        from src.contracts.growth_schema import GrowthProposal
        data = self._mirror.get_proposal(proposal_id)
        if data is None:
            return None
        try:
            return GrowthProposal.from_dict(data)
        except Exception:
            return None

    def list_proposals(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        **kwargs: Any,
    ) -> List[Any]:
        """列出提案"""
        from src.contracts.growth_schema import GrowthProposal
        items = self._mirror.list_proposals(status=status, limit=limit)
        out: List[Any] = []
        for d in items:
            try:
                out.append(GrowthProposal.from_dict(d))
            except Exception:
                continue
        return out

    def accept_proposal(self, proposal_id: str, **kwargs: Any) -> Optional[Any]:
        """
        ⚠️ DEPRECATED: 状态变更应由 GovernanceProvider.sync_status() 处理。

        保留此方法仅为向后兼容。新代码应避免调用。
        """
        from src.contracts.growth_schema import GrowthProposal
        self._mirror.update_status(proposal_id, "approved", extra={"approved_via": "mirror_adapter"})
        data = self._mirror.get_proposal(proposal_id)
        if data is None:
            return None
        return GrowthProposal.from_dict(data)

    def reject_proposal(self, proposal_id: str, **kwargs: Any) -> Optional[Any]:
        """
        ⚠️ DEPRECATED: 状态变更应由 GovernanceProvider.sync_status() 处理。
        """
        from src.contracts.growth_schema import GrowthProposal
        self._mirror.update_status(proposal_id, "rejected", extra={"rejected_via": "mirror_adapter"})
        data = self._mirror.get_proposal(proposal_id)
        if data is None:
            return None
        return GrowthProposal.from_dict(data)

    def update_proposal(self, proposal: Any, **kwargs: Any) -> bool:
        """
        ⚠️ DEPRECATED: 提案修改应由 GovernanceProvider 处理。
        """
        try:
            self._mirror.save_type_b(proposal.to_dict())
            return True
        except Exception:
            return False


# 模块级单例（与 ProposalStorage / GrowthAdapter 解耦）
_mirror_instance: Optional[GrowthProposalMirrorStorage] = None


def get_growth_proposal_mirror_storage() -> GrowthProposalMirrorStorage:
    """获取 GrowthProposalMirrorStorage 单例"""
    global _mirror_instance
    if _mirror_instance is None:
        _mirror_instance = GrowthProposalMirrorStorage()
    return _mirror_instance


def reset_mirror_storage_for_testing() -> None:
    """测试用：重置单例"""
    global _mirror_instance
    _mirror_instance = None
