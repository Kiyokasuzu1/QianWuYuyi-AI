# -*- coding: utf-8 -*-
"""
src/runtime/adapters/governance_mirror_integration.py

Phase 5.5 Integration + Phase 5.5.1 Hotfix

目的：
- 在 GovernanceProvider 提交 / 审批 Proposal 时，自动调用 Adapter 生成 Type B 镜像
- 提供可选注入的 mirror 集成，不强制要求启用（向后兼容 Phase 5.4 行为）
- 不修改 ApprovalManager / PersonalityAdapter / RuntimeCore / Orchestrator

强约束：
1. 集成是可选的（默认 disabled；通过构造器参数显式启用）
2. 不持有 Authority 引用
3. 不绕过 ApprovalManager（仅做镜像写入；审批仍走 ApprovalManager）
4. 不修改 RuntimeCore
5. 失败不 silent fail（Phase 5.5.1 [4]：失败写入 retry queue + Type A metadata）

Phase 5.5.1 Hotfix 改动：
- [1] Proposal Traceability: evaluator_meta 追加 source_proposal_id
- [2] Mirror Authority 修正: 提供只读接口（MirrorReadOnlyAdapter）
- [3] Memory Action 隔离: 识别 action_scope=memory，跳过镜像
- [4] Mirror Failure Handling: 失败写入 retry queue + Type A metadata
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
from src.runtime.adapters.growth_proposal_mirror import GrowthProposalMirrorStorage
from src.growth.state_machines import (
    ACTION_SCOPE_MEMORY,
    ACTION_SCOPE_PERSONALITY,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


class GovernanceMirrorIntegration:
    """
    GovernanceProvider 与 GrowthProposalAdapter 的集成胶水。

    职责：
    - 接收 Type A proposal
    - 通过 Adapter 转换为 Type B
    - 写入镜像存储（独立于 GrowthAdapter 的物理存储）
    - 在 Type A metadata 中追加 mirror_proposal_id 追踪字段

    行为：
    - 默认 disabled（向后兼容 Phase 5.4）
    - 显式构造即 enabled
    - 失败不 silent fail：写入 retry queue + Type A metadata（Phase 5.5.1 [4]）
    """

    def __init__(
        self,
        mirror_storage: Optional[GrowthProposalMirrorStorage] = None,
        enabled: bool = True,
        retry_queue: Optional[Any] = None,
    ):
        """
        Args:
            mirror_storage: 镜像存储实例（None 则使用默认单例）
            enabled: 是否启用镜像（默认 True；显式构造时启用）
            retry_queue: Phase 5.5.1 [4] 新增：失败重试队列（None 则不写入）
        """
        self._enabled = enabled
        if mirror_storage is not None:
            self._mirror = mirror_storage
        else:
            self._mirror = GrowthProposalMirrorStorage()

        # Phase 5.5.1 [4]: 失败重试队列
        self._retry_queue = retry_queue

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def _get_action_scope(self, type_a_proposal: Any) -> str:
        """
        Phase 5.5.1 [3]: 提取 action_scope。

        优先级：
        1. Type A metadata.action_scope（显式）
        2. Type A proposal_type=identity → memory（约定）
        3. 默认 personality
        """
        meta = getattr(type_a_proposal, "metadata", {}) or {}
        scope = meta.get("action_scope")
        if scope:
            return scope
        # 约定：identity 类型的 proposal 默认是 memory action
        ptype = getattr(type_a_proposal, "proposal_type", "")
        if ptype == "identity":
            # 进一步检查 metadata.request_kind
            request_kind = meta.get("request_kind", "")
            if request_kind.startswith("memory_"):
                return ACTION_SCOPE_MEMORY
        return ACTION_SCOPE_PERSONALITY

    def mirror_type_a_to_type_b(self, type_a_proposal: Any) -> Optional[Dict[str, Any]]:
        """
        将 Type A proposal 转换为 Type B 并写入镜像存储。

        Args:
            type_a_proposal: src.growth.proposal.proposal.GrowthProposal 实例

        Returns:
            写入结果 dict: {"success": bool, "mirror_proposal_id": str, "error": str}
        """
        if not self._enabled:
            return {"success": False, "mirror_proposal_id": "", "error": "mirror_disabled"}

        if type_a_proposal is None:
            return {"success": False, "mirror_proposal_id": "", "error": "type_a_proposal is None"}

        # Phase 5.5.1 [3]: Memory Action 隔离
        action_scope = self._get_action_scope(type_a_proposal)
        if action_scope == ACTION_SCOPE_MEMORY:
            return {
                "success": False,
                "mirror_proposal_id": "",
                "error": "memory_action_skipped",
                "action_scope": action_scope,
                "skipped": True,
            }

        try:
            # 1. 转换 Type A → Type B
            type_b = GrowthProposalAdapter.type_a_to_type_b(type_a_proposal)

            # 2. 追加 mirror 元数据（Phase 5.5.1 [1] Proposal Traceability）
            meta = dict(type_b.evaluator_meta or {})
            meta["mirrored_from_type_a"] = True
            meta["source_proposal_id"] = getattr(type_a_proposal, "proposal_id", "")
            meta["mirror_source_proposal_id"] = getattr(type_a_proposal, "proposal_id", "")
            meta["mirrored_at"] = _now_iso()
            meta["action_scope"] = action_scope
            type_b.evaluator_meta = meta

            # 3. 序列化并写入镜像存储
            type_b_dict = type_b.to_dict()
            saved = self._mirror.save_type_b(type_b_dict)
            if not saved:
                # Phase 5.5.1 [4]: 不 silent fail，写入 retry queue
                self._record_failure(
                    proposal_id=getattr(type_a_proposal, "proposal_id", ""),
                    operation="mirror",
                    error_message="mirror_save_failed",
                    payload={"type_b_dict": type_b_dict, "action_scope": action_scope},
                )
                return {
                    "success": False,
                    "mirror_proposal_id": "",
                    "error": "mirror_save_failed",
                    "action_scope": action_scope,
                }

            mirror_id = type_b.id
            logger.info(
                f"GovernanceMirrorIntegration: Type A → Type B 镜像成功 "
                f"({getattr(type_a_proposal, 'proposal_id', '?')} → {mirror_id}, "
                f"scope={action_scope})"
            )
            return {
                "success": True,
                "mirror_proposal_id": mirror_id,
                "mirror_status": type_b.status,
                "action_scope": action_scope,
                "error": "",
            }
        except Exception as e:
            # Phase 5.5.1 [4]: 异常不 silent fail
            error_msg = f"exception: {e}"
            logger.error(f"GovernanceMirrorIntegration.mirror_type_a_to_type_b 异常: {e}")
            self._record_failure(
                proposal_id=getattr(type_a_proposal, "proposal_id", ""),
                operation="mirror",
                error_message=error_msg,
                payload={"action_scope": action_scope},
            )
            return {
                "success": False,
                "mirror_proposal_id": "",
                "error": error_msg,
                "action_scope": action_scope,
            }

    def sync_status(
        self,
        type_a_proposal: Any,
        new_type_a_status: str,
        reviewer_id: str = "",
        review_comment: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        同步 Type A 状态变更到 Type B 镜像。

        Args:
            type_a_proposal: 已更新的 Type A proposal
            new_type_a_status: Type A 新状态 (approved/rejected)
            reviewer_id: 审查者 ID
            review_comment: 审查评论

        Returns:
            同步结果 dict
        """
        if not self._enabled:
            return {"success": False, "error": "mirror_disabled"}

        if type_a_proposal is None:
            return {"success": False, "error": "type_a_proposal is None"}

        try:
            pid = getattr(type_a_proposal, "proposal_id", "")
            # 状态映射：Type A → Type B
            if new_type_a_status == "approved":
                new_type_b_status = "approved"
            elif new_type_a_status == "rejected":
                new_type_b_status = "rejected"
            else:
                # 其它状态（如 cancelled）暂不映射
                return {
                    "success": False,
                    "error": f"unsupported status sync: {new_type_a_status}",
                }

            # 构造 source_status 透传扩展状态
            extra: Dict[str, Any] = {
                "source_status": new_type_a_status,
                "reviewer_id": reviewer_id,
                "review_comment": review_comment,
                "synced_at": _now_iso(),
            }

            ok = self._mirror.update_status(pid, new_type_b_status, extra=extra)
            if not ok:
                # Phase 5.5.1 [4]: 不 silent fail
                self._record_failure(
                    proposal_id=pid,
                    operation="sync_status",
                    error_message="mirror_update_failed",
                    payload={
                        "new_status": new_type_b_status,
                        "reviewer_id": reviewer_id,
                        "review_comment": review_comment,
                    },
                )
                return {
                    "success": False,
                    "mirror_proposal_id": pid,
                    "mirror_status": new_type_b_status,
                    "error": "mirror_update_failed",
                }

            return {
                "success": True,
                "mirror_proposal_id": pid,
                "mirror_status": new_type_b_status,
                "error": "",
            }
        except Exception as e:
            error_msg = f"exception: {e}"
            logger.error(f"GovernanceMirrorIntegration.sync_status 异常: {e}")
            self._record_failure(
                proposal_id=getattr(type_a_proposal, "proposal_id", ""),
                operation="sync_status",
                error_message=error_msg,
                payload={"new_type_a_status": new_type_a_status},
            )
            return {"success": False, "error": error_msg}

    def _record_failure(
        self,
        proposal_id: str,
        operation: str,
        error_message: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Phase 5.5.1 [4]: 记录失败到 retry queue（不 silent fail）。
        """
        if self._retry_queue is None:
            # retry queue 未注入，仅记日志（仍然不 silent fail）
            logger.warning(
                f"GovernanceMirrorIntegration 失败（无 retry queue）: "
                f"{operation} {proposal_id} - {error_message}"
            )
            return
        try:
            self._retry_queue.add_failure(
                proposal_id=proposal_id,
                operation=operation,
                error_message=error_message,
                payload=payload or {},
            )
        except Exception as e:
            logger.error(f"写入 retry queue 失败: {e}")

    def build_type_a_mirror_metadata(
        self,
        mirror_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        构造追加到 Type A metadata 的镜像追踪字段。

        Phase 5.5.1 [4]: 增加 mirror_sync_status 字段，避免 silent fail。
        """
        if not mirror_result:
            return {
                "mirror_attempted": True,
                "mirror_success": False,
                "mirror_sync_status": "unknown",
                "mirror_error": "no_result",
            }

        if mirror_result.get("skipped"):
            # memory action 跳过
            return {
                "mirror_attempted": True,
                "mirror_success": False,
                "mirror_skipped": True,
                "mirror_skip_reason": mirror_result.get("error", "unknown"),
                "action_scope": mirror_result.get("action_scope", "unknown"),
            }

        success = mirror_result.get("success", False)
        return {
            "mirror_attempted": True,
            "mirror_success": success,
            "mirror_sync_status": "success" if success else "failed",
            "mirror_proposal_id": mirror_result.get("mirror_proposal_id", ""),
            "mirror_schema": "type_b",
            "mirror_status": mirror_result.get("mirror_status", ""),
            "mirror_error": mirror_result.get("error", "") if not success else "",
            "action_scope": mirror_result.get("action_scope", ""),
            "mirrored_at": _now_iso(),
        }

    def get_mirror_storage(self) -> GrowthProposalMirrorStorage:
        """获取内部镜像存储实例（用于测试与集成）"""
        return self._mirror

    def get_retry_queue(self) -> Optional[Any]:
        """Phase 5.5.1 [4]: 获取 retry queue（用于测试）"""
        return self._retry_queue


# 模块级单例（默认 disabled；调用方显式构造并注入）
_default_integration: Optional[GovernanceMirrorIntegration] = None


def get_default_mirror_integration() -> Optional[GovernanceMirrorIntegration]:
    """获取默认镜像集成单例（可能为 None）"""
    return _default_integration


def set_default_mirror_integration(integration: Optional[GovernanceMirrorIntegration]) -> None:
    """设置默认镜像集成（用于全局启用/禁用）"""
    global _default_integration
    _default_integration = integration
