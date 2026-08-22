"""
Phase 5.3: Admin Governance Provider

提供 Admin 对 羽依 认知系统 的只读和受控操作能力。

设计原则（强约束）：
  1. 不创建任何核心 Authority 实例（MemoryStore / PersonalityResolver / EmotionManager / GrowthState）
  2. 所有运行时状态读取必须经过 RuntimeProvider → RuntimeBridge → RuntimeCore
  3. 所有受控操作通过 GrowthProposal + ProposalStorage 间接完成
  4. 不直接 apply Proposal（保留 Phase 3.5.13 ApprovalManager 的审批机制）
  5. 不修改 RuntimeCore / PersonalityAdapter / GrowthAccumulator
  6. 复用 src.growth.proposal.proposal.GrowthProposal 与 ProposalStorage

数据流：
    Admin UI
        ↓
    Admin API 路由
        ↓
    GovernanceProvider
        ↓
    RuntimeProvider（只读桥接）    +    ProposalStorage（受控写入）
        ↓                                ↓
    RuntimeBridge → RuntimeCore       JSON 持久化（proposals.json）
        ↓
    核心 Authority 实例（只读引用）

禁止用法：
    GovernanceProvider() → MemoryStore()           ❌
    GovernanceProvider() → GrowthState()             ❌
    GovernanceProvider() → PersonalityResolver()     ❌

允许用法：
    GovernanceProvider() → runtime_provider.get_personality_resolver() ✅
    GovernanceProvider() → proposal_storage.save(proposal)              ✅
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.contracts.governance_schema import (
    MEMORY_ACTION_MARK_INCORRECT,
    MEMORY_ACTION_DELETE,
    MEMORY_ACTION_MERGE,
    ALL_MEMORY_ACTIONS,
    REVIEW_ACTION_APPROVE,
    REVIEW_ACTION_REJECT,
    REVIEW_ACTION_MODIFY,
    ALL_REVIEW_ACTIONS,
    METADATA_GOVERNANCE_SOURCE,
    METADATA_GOVERNANCE_VERSION,
    PersonalityChangeRequest,
    MemoryActionRequest,
    GrowthProposalReviewRequest,
    GovernanceSnapshot,
)
from src.growth.proposal.proposal import GrowthProposal
from src.growth.proposal.storage import (
    get_proposal_storage,
    ProposalStorage,
)
from src.growth.proposal.constants import (
    PROPOSAL_STATUS,
    PROPOSAL_TYPE,
    PRIORITY_LEVEL,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


class GovernanceProvider:
    """
    Phase 5.3: Admin 治理层 Provider

    职责：
      - 暴露只读视图：当前人格 / 最近记忆 / 成长快照
      - 提供受控操作：生成 GrowthProposal 写入 ProposalStorage
      - 禁止直接修改任何 Authority 实例
    """

    def __init__(
        self,
        runtime_provider=None,
        proposal_storage: Optional[ProposalStorage] = None,
        mirror_integration: Optional[Any] = None,
    ):
        """
        Args:
            runtime_provider: 已存在的 RuntimeProvider 实例（由调用方注入，便于测试）
                              若为 None，则通过 get_runtime_provider() 获取单例
            proposal_storage: 已存在的 ProposalStorage 实例（测试时可注入临时目录）
                              若为 None，则通过 get_proposal_storage() 获取单例
            mirror_integration: Phase 5.5 Integration 阶段新增：可选的 Type B 镜像集成
                                （默认 None，保持 Phase 5.4 行为；启用后 Admin 提交的
                                Type A proposal 会自动同步为 Type B 镜像，
                                可被 ApprovalManager 读取并最终 apply 到 PersonalityResolver）
        """
        # 注入式依赖（避免硬编码单例）
        if runtime_provider is not None:
            self._runtime_provider = runtime_provider
        else:
            from src.admin.runtime_provider import get_runtime_provider
            self._runtime_provider = get_runtime_provider()

        if proposal_storage is not None:
            self._storage = proposal_storage
        else:
            self._storage = get_proposal_storage()

        # Phase 5.5: 可选镜像集成（向后兼容：None 时行为与 Phase 5.4 一致）
        self._mirror_integration = mirror_integration

        # 审计（可选注入）
        self._audit = self._try_get_audit_logger()

    # ==================== Phase 5.5 镜像集成接口 ====================

    def set_mirror_integration(self, mirror_integration: Optional[Any]) -> None:
        """
        动态启用 / 禁用 / 替换镜像集成。

        Args:
            mirror_integration: None 表示禁用；传入 GovernanceMirrorIntegration 实例启用
        """
        self._mirror_integration = mirror_integration

    def get_mirror_integration(self) -> Optional[Any]:
        """获取当前镜像集成（仅用于测试与诊断）"""
        return self._mirror_integration

    def _mirror_if_enabled(self, proposal: GrowthProposal) -> Optional[Dict[str, Any]]:
        """
        若镜像集成已启用则执行镜像（best-effort；失败不影响主流程）。

        Returns:
            镜像结果 dict；若未启用则返回 None
        """
        if self._mirror_integration is None:
            return None
        try:
            result = self._mirror_integration.mirror_type_a_to_type_b(proposal)
            # 同步把 mirror 追踪字段写回 Type A metadata（便于审计与回溯）
            if result and isinstance(result, dict):
                meta_addon = self._mirror_integration.build_type_a_mirror_metadata(result)
                proposal.metadata = {**(proposal.metadata or {}), **meta_addon}
                # 持久化（不重新构造对象，仅保存当前状态）
                try:
                    self._storage.save(proposal)
                except Exception:
                    pass
            return result
        except Exception as e:
            logger.debug(f"GovernanceProvider: 镜像写入异常（非阻塞）: {e}")
            return None

    def _sync_mirror_status_if_enabled(
        self,
        proposal: GrowthProposal,
        new_type_a_status: str,
        reviewer_id: str = "",
        review_comment: str = "",
    ) -> None:
        """
        若镜像集成已启用则同步 Type B 镜像状态（best-effort）。
        """
        if self._mirror_integration is None:
            return
        try:
            self._mirror_integration.sync_status(
                proposal,
                new_type_a_status,
                reviewer_id=reviewer_id,
                review_comment=review_comment,
            )
        except Exception as e:
            logger.debug(f"GovernanceProvider: 镜像状态同步异常（非阻塞）: {e}")

    # ==================== 内部辅助 ====================

    def _try_get_audit_logger(self):
        """尝试获取审计 logger（容错，失败不影响主流程）"""
        try:
            from src.admin.core.audit import AuditLogger
            return AuditLogger.get_instance()
        except Exception:
            return None

    def _record_audit(
        self,
        action: str,
        target: str,
        result: str,
        actor: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """记录治理操作审计（容错，失败不影响主流程）"""
        if self._audit is None:
            return
        try:
            self._audit.record(
                event_type=action,
                operator=actor,
                operator_type="human",
                target=target,
                details=details or {},
                result=result,
            )
        except Exception as e:
            logger.debug(f"GovernanceProvider: 审计记录失败（非阻塞）: {e}")

    def _new_proposal_id(self) -> str:
        return f"gov_{uuid.uuid4().hex[:10]}"

    def _read_current_personality(self) -> Dict[str, Any]:
        """从 RuntimeProvider 读取当前人格数据（不缓存）"""
        try:
            return self._runtime_provider.get_personality_summary() or {}
        except Exception as e:
            logger.debug(f"GovernanceProvider: 读取 personality 失败: {e}")
            return {}

    def _read_current_memory(self, limit: int = 20) -> Dict[str, Any]:
        """从 RuntimeProvider 读取记忆概览"""
        try:
            return self._runtime_provider.get_memory_summary() or {}
        except Exception as e:
            logger.debug(f"GovernanceProvider: 读取 memory 失败: {e}")
            return {}

    def _read_current_growth(self) -> Dict[str, Any]:
        """从 RuntimeProvider 读取成长状态"""
        try:
            return self._runtime_provider.get_growth_summary() or {}
        except Exception as e:
            logger.debug(f"GovernanceProvider: 读取 growth 失败: {e}")
            return {}

    def _resolve_trait_value(self, trait: str, personality_data: Dict[str, Any]) -> Optional[float]:
        """
        从 personality_data 中解析指定 trait 的当前值。
        兼容：
          - personality_data["current"] 为 dict（{trait: value}）
          - personality_data["current"] 为 dataclass / object（有属性）
          - 直接顶层 key
        """
        if not personality_data:
            return None
        current = personality_data.get("current")
        if current is None:
            return None
        # dict 形式
        if isinstance(current, dict):
            if trait in current:
                try:
                    return float(current[trait])
                except (TypeError, ValueError):
                    return None
            return None
        # 对象形式
        if hasattr(current, trait):
            try:
                return float(getattr(current, trait))
            except (TypeError, ValueError):
                return None
        return None

    # ==================== Personality Governance ====================

    def get_personality_governance_snapshot(self) -> Dict[str, Any]:
        """
        只读：返回人格治理快照

        Returns:
            {
                "section": "personality",
                "available": bool,
                "data": {
                    "current": {...},        # 当前人格 resolve 结果
                    "state": {...},          # GrowthState 关键字段
                    "self_model": {...},     # SelfModelStore 关键字段
                    "traits": {...},         # 提取的特质列表
                    "recent_proposals": [...],  # 最近 5 条人格相关 Proposal
                },
                "error": str
            }
        """
        snapshot = GovernanceSnapshot(section="personality")
        try:
            personality = self._read_current_personality()
            available = bool(personality.get("available", False))
            snapshot.available = available
            if available:
                snapshot.data = {
                    "current": personality.get("current"),
                    "state": personality.get("state"),
                    "self_model": personality.get("self_model"),
                    "traits": self._extract_traits(personality),
                    "recent_proposals": [
                        p.to_dict() for p in self._storage.list_by_type(
                            PROPOSAL_TYPE["PERSONALITY"], limit=5
                        )
                    ],
                }
        except Exception as e:
            snapshot.error = str(e)
            logger.warning(f"GovernanceProvider.get_personality_governance_snapshot 异常: {e}")
        return snapshot.to_dict()

    def _extract_traits(self, personality: Dict[str, Any]) -> Dict[str, Any]:
        """
        从 personality 中提取可治理的特质列表。
        不修改原始数据；只生成快照。
        """
        if not personality:
            return {}
        current = personality.get("current")
        if current is None:
            return {}
        traits: Dict[str, Any] = {}
        if isinstance(current, dict):
            for k, v in current.items():
                try:
                    traits[str(k)] = {"value": float(v)}
                except (TypeError, ValueError):
                    traits[str(k)] = {"value": None, "raw": v}
        elif hasattr(current, "__dict__"):
            for k, v in vars(current).items():
                if k.startswith("_"):
                    continue
                try:
                    traits[str(k)] = {"value": float(v)}
                except (TypeError, ValueError):
                    traits[str(k)] = {"value": None, "raw": v}
        return traits

    def propose_personality_change(
        self,
        req: PersonalityChangeRequest,
        actor: str = "admin",
    ) -> Dict[str, Any]:
        """
        受控：生成 Personality Change Proposal 并写入 ProposalStorage。

        不直接修改 PersonalityResolver / TraitState。
        真正的应用由 PersonalityAdapter → GrowthAccumulator 流程处理（保留审批机制）。
        """
        if not req.trait:
            return {
                "success": False,
                "error": "trait 不能为空",
            }
        if not isinstance(req.delta, (int, float)):
            return {
                "success": False,
                "error": "delta 必须是数字",
            }

        # 读取当前值（用于填充 before_state）
        personality = self._read_current_personality()
        before_value = self._resolve_trait_value(req.trait, personality)
        if before_value is None:
            # 仍允许提交，但 before 留空
            before_value = 0.0

        after_value = before_value + float(req.delta)
        # 限幅到 [0, 1]，避免极端值
        after_value = max(0.0, min(1.0, after_value))

        # 构造 GrowthProposal
        proposal_id = self._new_proposal_id()
        proposal = GrowthProposal(
            proposal_id=proposal_id,
            timestamp=_now_iso(),
            proposal_type=PROPOSAL_TYPE["PERSONALITY"],
            status=PROPOSAL_STATUS["PENDING"],
            source=f"{METADATA_GOVERNANCE_SOURCE}:personality",
            source_event_id="",
            user_id=actor,
            affected_dimensions={req.trait: float(req.delta)},
            before_state={req.trait: float(before_value)},
            after_state={req.trait: float(after_value)},
            confidence=float(req.confidence),
            reason=req.reason or "admin_governance_proposal",
            evidence=list(req.evidence or []),
            priority=req.priority if req.priority in PRIORITY_LEVEL.values() else PRIORITY_LEVEL["MEDIUM"],
            metadata={
                METADATA_GOVERNANCE_SOURCE: True,
                METADATA_GOVERNANCE_VERSION: METADATA_GOVERNANCE_VERSION,
                "request_kind": "personality_change",
                "actor": actor,
            },
        )

        # 持久化
        try:
            self._storage.save(proposal)
        except Exception as e:
            self._record_audit(
                action="governance.personality.propose",
                target=proposal_id,
                result="failure",
                actor=actor,
                details={"error": str(e), "trait": req.trait},
            )
            return {
                "success": False,
                "error": f"写入 ProposalStorage 失败: {e}",
            }

        # Phase 5.5: Type A → Type B 镜像（可选集成；失败不阻塞主流程）
        self._mirror_if_enabled(proposal)

        # 审计
        self._record_audit(
            action="governance.personality.propose",
            target=proposal_id,
            result="success",
            actor=actor,
            details={
                "trait": req.trait,
                "delta": req.delta,
                "before": before_value,
                "after": after_value,
                "reason": req.reason,
            },
        )

        return {
            "success": True,
            "proposal_id": proposal_id,
            "proposal": proposal.to_dict(),
            "message": "Personality change proposal 已提交，等待审批",
        }

    # ==================== Memory Governance ====================

    def get_memory_governance_snapshot(self, limit: int = 20) -> Dict[str, Any]:
        """
        只读：返回记忆治理快照

        Returns:
            {
                "section": "memory",
                "available": bool,
                "data": {
                    "total_count": int,
                    "important_count": int,
                    "recent": [...],           # 最近 limit 条记忆
                    "important": [...],        # importance >= 0.7
                    "by_type": {...},          # 记忆类型分布
                    "recent_proposals": [...], # 最近 5 条记忆相关 Proposal
                },
                "error": str
            }
        """
        snapshot = GovernanceSnapshot(section="memory")
        try:
            memory = self._read_current_memory(limit=limit)
            available = bool(memory.get("available", False))
            snapshot.available = available

            if available:
                recent = memory.get("recent", []) or []
                important = [m for m in recent if float(m.get("importance", 0) or 0) >= 0.7]
                by_type: Dict[str, int] = {}
                for m in recent:
                    t = m.get("type", "unknown")
                    by_type[t] = by_type.get(t, 0) + 1

                # 来源事件：通过 source_event_id 字段聚合
                source_event_ids = sorted({
                    str(m.get("source_event_id", "")).strip()
                    for m in recent
                    if m.get("source_event_id")
                })

                snapshot.data = {
                    "total_count": int(memory.get("total_count", 0) or 0),
                    "important_count": int(memory.get("important_count", 0) or 0),
                    "user_id": memory.get("user_id"),
                    "recent": recent[:limit],
                    "important": important[:limit],
                    "by_type": by_type,
                    "source_event_ids": source_event_ids,
                    "recent_proposals": [
                        p.to_dict() for p in self._storage.list_all(limit=1000)
                        if (p.metadata or {}).get("request_kind", "").startswith("memory_") or
                           p.proposal_type == PROPOSAL_TYPE["IDENTITY"]
                    ][:5],
                }
        except Exception as e:
            snapshot.error = str(e)
            logger.warning(f"GovernanceProvider.get_memory_governance_snapshot 异常: {e}")
        return snapshot.to_dict()

    def propose_memory_action(
        self,
        req: MemoryActionRequest,
        actor: str = "admin",
    ) -> Dict[str, Any]:
        """
        受控：生成 MemoryAction Proposal 并写入 ProposalStorage。

        不直接修改 / 删除 MemoryStore 数据。
        action 支持：
            - "mark_incorrect": 标记错误
            - "delete":         申请删除
            - "merge":          申请合并（需提供 target_memory_id）
        """
        if not req.memory_id:
            return {"success": False, "error": "memory_id 不能为空"}
        if req.action not in ALL_MEMORY_ACTIONS:
            return {
                "success": False,
                "error": f"action 必须是 {sorted(ALL_MEMORY_ACTIONS)} 之一",
            }
        if req.action == MEMORY_ACTION_MERGE and not req.target_memory_id:
            return {
                "success": False,
                "error": "merge 操作必须提供 target_memory_id",
            }

        # 读取 memory 数据用于填充 before_state（仅做证据记录，不修改）
        before_payload: Dict[str, Any] = {}
        try:
            memory_snapshot = self._read_current_memory(limit=200)
            for m in memory_snapshot.get("recent", []) or []:
                if str(m.get("id") or m.get("memory_id") or "") == req.memory_id:
                    before_payload = {
                        "id": req.memory_id,
                        "type": m.get("type", ""),
                        "importance": m.get("importance", 0),
                        "content_preview": (m.get("content", "") or "")[:120],
                    }
                    break
        except Exception:
            before_payload = {}

        proposal_id = self._new_proposal_id()
        proposal = GrowthProposal(
            proposal_id=proposal_id,
            timestamp=_now_iso(),
            # 用 IDENTITY 类型承载"记忆处理"语义（保持 schema 兼容）
            proposal_type=PROPOSAL_TYPE["IDENTITY"],
            status=PROPOSAL_STATUS["PENDING"],
            source=f"{METADATA_GOVERNANCE_SOURCE}:memory",
            source_event_id="",
            user_id=actor,
            affected_dimensions={},  # 记忆操作不修改 personality 维度
            before_state=before_payload,
            after_state={"action": req.action},
            confidence=0.5,
            reason=req.reason or f"admin_memory_action:{req.action}",
            evidence=[req.memory_id],
            priority=req.priority if req.priority in PRIORITY_LEVEL.values() else PRIORITY_LEVEL["MEDIUM"],
            metadata={
                METADATA_GOVERNANCE_SOURCE: True,
                METADATA_GOVERNANCE_VERSION: METADATA_GOVERNANCE_VERSION,
                "request_kind": f"memory_{req.action}",
                "memory_id": req.memory_id,
                "memory_action": req.action,
                "target_memory_id": req.target_memory_id,
                "actor": actor,
            },
        )

        try:
            self._storage.save(proposal)
        except Exception as e:
            self._record_audit(
                action="governance.memory.propose",
                target=proposal_id,
                result="failure",
                actor=actor,
                details={"error": str(e), "memory_id": req.memory_id, "action": req.action},
            )
            return {
                "success": False,
                "error": f"写入 ProposalStorage 失败: {e}",
            }

        # Phase 5.5: Type A → Type B 镜像（可选集成；失败不阻塞主流程）
        self._mirror_if_enabled(proposal)

        self._record_audit(
            action="governance.memory.propose",
            target=proposal_id,
            result="success",
            actor=actor,
            details={
                "memory_id": req.memory_id,
                "action": req.action,
                "target_memory_id": req.target_memory_id,
                "reason": req.reason,
            },
        )

        return {
            "success": True,
            "proposal_id": proposal_id,
            "proposal": proposal.to_dict(),
            "message": f"Memory action '{req.action}' proposal 已提交，等待审批",
        }

    # ==================== Growth Governance ====================

    def get_growth_governance_snapshot(self, limit: int = 50) -> Dict[str, Any]:
        """
        只读：返回成长治理快照

        Returns:
            {
                "section": "growth",
                "available": bool,
                "data": {
                    "metrics": {...},            # GrowthState 关键指标
                    "shared_with_resolver": bool,
                    "pending": [...],            # pending proposals
                    "approved": [...],           # approved proposals
                    "rejected": [...],           # rejected proposals
                    "applied": [...],            # applied proposals
                    "by_type": {...},            # 各类型数量
                },
                "error": str
            }
        """
        snapshot = GovernanceSnapshot(section="growth")
        try:
            growth = self._read_current_growth()
            available = bool(growth.get("available", False))
            snapshot.available = available

            # 加载所有 proposals（按状态分组）
            try:
                all_proposals = self._storage.list_all(limit=max(limit, 200))
            except Exception as e:
                logger.debug(f"加载 proposals 失败: {e}")
                all_proposals = []

            by_status: Dict[str, List[Dict[str, Any]]] = {
                PROPOSAL_STATUS["PENDING"]: [],
                PROPOSAL_STATUS["APPROVED"]: [],
                PROPOSAL_STATUS["REJECTED"]: [],
                PROPOSAL_STATUS["APPLIED"]: [],
            }
            by_type_count: Dict[str, int] = {}
            for p in all_proposals:
                # Governance 治理的 proposal 优先展示
                is_gov = (p.metadata or {}).get(METADATA_GOVERNANCE_SOURCE, False)
                d = p.to_dict()
                d["is_governance"] = is_gov
                if p.status in by_status:
                    by_status[p.status].append(d)
                pt = p.proposal_type
                by_type_count[pt] = by_type_count.get(pt, 0) + 1

            snapshot.data = {
                "metrics": growth.get("metrics", {}),
                "shared_with_resolver": growth.get("shared_with_resolver", False),
                "pending": by_status[PROPOSAL_STATUS["PENDING"]][:limit],
                "approved": by_status[PROPOSAL_STATUS["APPROVED"]][:limit],
                "rejected": by_status[PROPOSAL_STATUS["REJECTED"]][:limit],
                "applied": by_status[PROPOSAL_STATUS["APPLIED"]][:limit],
                "by_type": by_type_count,
                "total": len(all_proposals),
            }
        except Exception as e:
            snapshot.error = str(e)
            logger.warning(f"GovernanceProvider.get_growth_governance_snapshot 异常: {e}")
        return snapshot.to_dict()

    def list_proposals(
        self,
        status: Optional[str] = None,
        proposal_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """列出 Proposal（可按状态、类型过滤）"""
        try:
            if status:
                proposals = self._storage.list_by_status(status, limit=limit + offset)
            elif proposal_type:
                proposals = self._storage.list_by_type(proposal_type, limit=limit + offset)
            else:
                proposals = self._storage.list_all(limit=limit + offset, offset=offset)

            return [p.to_dict() for p in proposals[offset:offset + limit]]
        except Exception as e:
            logger.warning(f"GovernanceProvider.list_proposals 异常: {e}")
            return []

    def get_proposal_detail(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        """获取 Proposal 详情"""
        try:
            p = self._storage.load(proposal_id)
            if p is None:
                return None
            d = p.to_dict()
            d["is_governance"] = (p.metadata or {}).get(METADATA_GOVERNANCE_SOURCE, False)
            return d
        except Exception as e:
            logger.debug(f"加载 proposal {proposal_id} 失败: {e}")
            return None

    def review_proposal(
        self,
        req: GrowthProposalReviewRequest,
        actor: str = "admin",
    ) -> Dict[str, Any]:
        """
        受控：审查 Proposal，更新状态。

        重要：本方法只标记 status（approved/rejected），不直接 apply。
        真正的 apply 由 PersonalityAdapter / GrowthAccumulator 路径处理。
        这样保留了 Phase 3.5.13 ApprovalManager 的审批机制。
        """
        if not req.proposal_id:
            return {"success": False, "error": "proposal_id 不能为空"}
        if req.action not in ALL_REVIEW_ACTIONS:
            return {
                "success": False,
                "error": f"action 必须是 {sorted(ALL_REVIEW_ACTIONS)} 之一",
            }

        try:
            proposal = self._storage.load(req.proposal_id)
        except Exception as e:
            return {"success": False, "error": f"加载 proposal 失败: {e}"}

        if proposal is None:
            return {
                "success": False,
                "error": f"proposal {req.proposal_id} 不存在",
            }

        # 状态流转
        if proposal.status in (PROPOSAL_STATUS["APPROVED"],
                               PROPOSAL_STATUS["REJECTED"],
                               PROPOSAL_STATUS["APPLIED"]):
            return {
                "success": False,
                "error": f"proposal {req.proposal_id} 已处于终态 ({proposal.status})，不能再次审查",
            }

        # 记录修改前状态
        before_status = proposal.status

        if req.action == REVIEW_ACTION_APPROVE:
            proposal.status = PROPOSAL_STATUS["APPROVED"]
        elif req.action == REVIEW_ACTION_REJECT:
            proposal.status = PROPOSAL_STATUS["REJECTED"]
        elif req.action == REVIEW_ACTION_MODIFY:
            # modify 必须提供 modified_changes
            if not req.modified_changes:
                return {
                    "success": False,
                    "error": "modify 操作必须提供 modified_changes",
                }
            # 合并修改：把 modified_changes 合并到 after_state
            for change in req.modified_changes:
                trait = change.get("trait") or change.get("path")
                if not trait:
                    continue
                new_after = change.get("after")
                if new_after is None:
                    continue
                try:
                    new_after = float(new_after)
                except (TypeError, ValueError):
                    continue
                # 更新 before / after
                old_after = proposal.after_state.get(trait)
                proposal.after_state[trait] = new_after
                if trait not in proposal.before_state and old_after is not None:
                    proposal.before_state[trait] = old_after
                # 重新计算 affected_dimensions delta
                before_v = proposal.before_state.get(trait)
                if before_v is not None:
                    proposal.affected_dimensions[trait] = round(new_after - float(before_v), 4)
            proposal.status = PROPOSAL_STATUS["APPROVED"]  # modify 也视为通过

        proposal.reviewer_id = actor
        proposal.review_comment = req.reason
        proposal.reviewed_at = _now_iso()

        # 元数据追加审查记录
        md = dict(proposal.metadata or {})
        md["last_review"] = {
            "action": req.action,
            "actor": actor,
            "reason": req.reason,
            "before_status": before_status,
            "after_status": proposal.status,
            "reviewed_at": proposal.reviewed_at,
        }
        proposal.metadata = md

        try:
            self._storage.save(proposal)
        except Exception as e:
            self._record_audit(
                action=f"governance.growth.review.{req.action}",
                target=req.proposal_id,
                result="failure",
                actor=actor,
                details={"error": str(e)},
            )
            return {"success": False, "error": f"保存 proposal 失败: {e}"}

        # Phase 5.5: 同步 Type A 状态到 Type B 镜像（可选；失败不阻塞）
        if proposal.status in (PROPOSAL_STATUS["APPROVED"], PROPOSAL_STATUS["REJECTED"]):
            self._sync_mirror_status_if_enabled(
                proposal,
                proposal.status,
                reviewer_id=actor,
                review_comment=req.reason or "",
            )

        self._record_audit(
            action=f"governance.growth.review.{req.action}",
            target=req.proposal_id,
            result="success",
            actor=actor,
            details={
                "before_status": before_status,
                "after_status": proposal.status,
                "reason": req.reason,
            },
        )

        return {
            "success": True,
            "proposal_id": req.proposal_id,
            "before_status": before_status,
            "after_status": proposal.status,
            "action": req.action,
            "proposal": proposal.to_dict(),
            "message": f"Proposal {req.proposal_id} 已 {req.action}",
        }

    # ==================== Goal Governance (v1.3 Phase 1.5) ====================

    def get_goal_governance_snapshot(self, limit: int = 20) -> Dict[str, Any]:
        """
        只读：返回 Goal 提案治理视图（仅读 B-store，不读不写 GoalState）。

        Returns:
            {
                "section": "goal",
                "available": bool,
                "data": {
                    "proposals": [
                        {
                            "proposal_id", "goal_id", "description",
                            "source_refs", "confidence", "priority",
                            "created_at", "proposal_status",
                            "reviewer_id", "reviewed_at", "applied_at", "reason"
                        }, ...
                    ],
                    "by_status": {...},   # 提案状态分布
                    "total": int,
                },
                "error": str
            }
        """
        snapshot = GovernanceSnapshot(section="goal")
        try:
            _cap = max(1, int(limit))
            proposals = self._storage.list_by_type(
                PROPOSAL_TYPE["GOAL"], limit=max(_cap, 200)
            )
            views = [self._goal_proposal_view(p) for p in proposals]
            by_status: Dict[str, int] = {}
            for v in views:
                _st = str(v.get("proposal_status") or "unknown")
                by_status[_st] = by_status.get(_st, 0) + 1
            snapshot.available = True
            snapshot.data = {
                "proposals": views[:_cap],
                "by_status": by_status,
                "total": len(views),
            }
        except Exception as e:
            snapshot.error = str(e)
            logger.warning(f"GovernanceProvider.get_goal_governance_snapshot 异常: {e}")
        return snapshot.to_dict()

    @staticmethod
    def _goal_proposal_view(p: GrowthProposal) -> Dict[str, Any]:
        """Goal 提案展示视图（只读投影，不触碰 GoalState）。

        goal 载荷键与 src/goal/goal_proposal.py 的 GOAL_PAYLOAD_KEY 一致
        （此处使用字面量，避免 admin 层引入 goal 包依赖）。
        """
        meta = getattr(p, "metadata", None)
        payload: Dict[str, Any] = {}
        if isinstance(meta, dict):
            _gp = meta.get("goal_proposal")
            if isinstance(_gp, dict):
                payload = _gp
        return {
            "proposal_id": str(getattr(p, "proposal_id", "") or ""),
            "goal_id": str(payload.get("goal_id", "") or ""),
            "description": str(payload.get("description", "") or ""),
            "source_refs": list(payload.get("source_refs", []) or []),
            "confidence": float(payload.get("confidence", 0.0) or 0.0),
            "priority": str(payload.get("priority", "") or ""),
            "created_at": str(getattr(p, "timestamp", "") or ""),
            "proposal_status": str(getattr(p, "status", "") or ""),
            "reviewer_id": str(getattr(p, "reviewer_id", "") or ""),
            "reviewed_at": getattr(p, "reviewed_at", None),
            "applied_at": getattr(p, "applied_at", None),
            "reason": str(getattr(p, "reason", "") or ""),
        }

    # ==================== 工具方法 ====================

    def get_storage_stats(self) -> Dict[str, Any]:
        """获取存储统计（用于诊断）"""
        try:
            total = self._storage.count()
            return {
                "total_proposals": total,
                "by_status": {
                    s: len(self._storage.list_by_status(s, limit=1000))
                    for s in PROPOSAL_STATUS.values()
                },
            }
        except Exception as e:
            return {"error": str(e)}


# 模块级单例
_provider_instance: Optional[GovernanceProvider] = None


def get_governance_provider() -> GovernanceProvider:
    """获取 GovernanceProvider 单例"""
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = GovernanceProvider()
    return _provider_instance


def reset_governance_provider_for_testing() -> None:
    """测试用：重置单例"""
    global _provider_instance
    _provider_instance = None
