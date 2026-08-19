# -*- coding: utf-8 -*-
"""
Phase 3.8.6: SelfModelUpdaterAdapter

RuntimeCore 旧 SelfModelUpdater API (Phase 3.5.8) 到
当前 SelfModelUpdater + SelfModelStore 架构 (Phase 3.8.5) 的适配器。

职责：
- 翻译 from_pcr() / from_insights() → GrowthRecord-like → create_proposal_from_growth()
- 翻译 apply_suggestion() → SelfModelStore.apply_change_proposal() + SelfModelManager 同步
- 不创建新的 SelfModel，不修改 Personality 内部状态

两阶段生命周期（硬契约）：
  from_pcr() / from_insights()
      → 只生成 SelfModelChangeProposal
      → 不写入 Store（snapshot_before == snapshot_after）
  apply_suggestion()
      → 写入 SelfModelStore（正式生效）
      → 同步 SelfModelManager 理解指标

设计原则：
- RULE 1: Proposal 生成和 Proposal 应用必须分离
- RULE 2: 不绕过 RuntimeCore 的 pending/approval 生命周期
- RULE 3: 所有写入可追溯
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now().isoformat()


class SelfModelUpdaterAdapter:
    """
    Phase 3.8.6: RuntimeCore 旧 API → 当前 SelfModel 架构的适配器。
    Phase 4.0.3-C: apply_suggestion() 增加安全闸，拒绝未审批 proposal 直接写入 Store。

    桥接 RuntimeCore 的 Phase 3.5.8 调用模式：
      from_pcr(pcr) → proposal → (GovernancePolicy.evaluate) → pending / auto_apply
      from_insights(insights) → proposals → (GovernancePolicy.evaluate) → pending / auto_apply
      apply_suggestion(manager, suggestion) → 安全闸 → 仅已批准 Proposal 可写入

    到当前 Phase 3.8.5 SelfModelUpdater + SelfModelStore 架构。
    """

    def __init__(
        self,
        self_model_store: Optional[Any] = None,
        self_model_updater: Optional[Any] = None,
        self_model_manager: Optional[Any] = None,
    ):
        """
        Args:
            self_model_store: SelfModelStore 实例（权威状态持有者）
            self_model_updater: SelfModelUpdater 实例（Proposal 生成器 + apply_proposal）
            self_model_manager: SelfModelManager 实例（向后兼容，可选）
        """
        self._store = self_model_store
        self._updater = self_model_updater
        self._manager = self_model_manager

    # ============================================================
    # 阶段一：生成 Proposal（不写入 Store）
    # ============================================================

    def from_pcr(
        self,
        pcr: Dict[str, Any],
    ) -> Optional[Any]:
        """
        Phase 3.8.6: 将 PersonalityChangeRequest 转换为 SelfModelChangeProposal。

        契约：不写入 SelfModelStore。调用前后 Store 快照一致。

        Args:
            pcr: PersonalityChangeRequest dict（来自 RuntimeCore）

        Returns:
            SelfModelChangeProposal 或 None
        """
        if not pcr or not self._updater:
            return None

        record = self._pcr_to_growth_record(pcr)
        if record is None:
            logger.debug("SelfModelUpdaterAdapter: PCR 转换 GrowthRecord 失败，跳过")
            return None

        return self._updater.create_proposal_from_growth(record)

    def from_insights(
        self,
        insights: List[Any],
    ) -> List[Any]:
        """
        Phase 3.8.6: 将 ReflectionInsights 转换为 SelfModelChangeProposal 列表。

        契约：不写入 SelfModelStore。调用前后 Store 快照一致。

        Args:
            insights: ReflectionInsight 列表

        Returns:
            SelfModelChangeProposal 列表
        """
        results: List[Any] = []
        if not insights or not self._updater:
            return results

        for ins in insights:
            record = self._insight_to_growth_record(ins)
            if record is None:
                continue
            proposal = self._updater.create_proposal_from_growth(record)
            if proposal is not None:
                results.append(proposal)

        return results

    # ============================================================
    # Phase 4.0.3-C: 公开 PCR → GrowthRecord 转换（供 Governance 评估）
    # ============================================================

    def pcr_to_growth_record(
        self,
        pcr: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Phase 4.0.3-C: 公开 PCR → GrowthRecord 转换。

        供 RuntimeCore 在 from_pcr() 之后调用 GovernancePolicy.evaluate()。
        与内部 _pcr_to_growth_record() 相同逻辑，但作为公开 API。
        """
        return self._pcr_to_growth_record(pcr)

    # ============================================================
    # 阶段二：应用 Proposal（正式写入 Store）
    # ============================================================

    def apply_suggestion(
        self,
        manager: Any = None,
        suggestion: Any = None,
        **kwargs: Any,
    ) -> None:
        """
        Phase 4.0.3-C: 安全闸 — 仅已批准 Proposal 可写入 Store。

        1) 安全闸：拒绝 requires_approval=True 的 proposal（未经过 ApprovalQueue.approve()）
        2) 写入 SelfModelStore（通过 Updater.apply_proposal()，不再直接操作 Store）
        3) 同步 SelfModelManager 理解指标（向后兼容）

        Args:
            manager: SelfModelManager 实例（可选，向后兼容）
            suggestion: SelfModelChangeProposal（必须已通过 ApprovalQueue.approve()）
        """
        if suggestion is None:
            return

        # Phase 4.0.3-C: 安全闸 — 拒绝未审批 proposal
        if getattr(suggestion, "requires_approval", True):
            logger.warning(
                "SelfModelUpdaterAdapter: 拒绝未审批的 proposal 写入 Store "
                "(suggestion_id=%s)。请先通过 ApprovalQueue.approve()。",
                getattr(suggestion, "suggestion_id", "unknown"),
            )
            return

        # 1) 通过 Updater.apply_proposal() 写入 Store（不再直接操作 _store）
        if self._updater is not None:
            try:
                self._updater.apply_proposal(suggestion)
            except Exception as e:
                logger.warning(
                    "SelfModelUpdaterAdapter: apply_proposal 失败（已隔离）: %s", e
                )
        elif self._store is not None:
            # 向后兼容：无 Updater 时回退到直接操作 Store
            try:
                if hasattr(self._store, "apply_change_proposal"):
                    self._store.apply_change_proposal(suggestion)
                else:
                    logger.warning(
                        "SelfModelUpdaterAdapter: Store 不支持 apply_change_proposal，"
                        "跳过写入"
                    )
            except Exception as e:
                logger.warning(
                    "SelfModelUpdaterAdapter: apply_change_proposal 失败（已隔离）: %s", e
                )

        # 2) 同步 SelfModelManager 理解指标（向后兼容）
        if manager is not None:
            self._sync_manager_understanding(manager, suggestion)

    # ============================================================
    # 内部：PCR / Insight → GrowthRecord-like 转换
    # ============================================================

    def _pcr_to_growth_record(
        self,
        pcr: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        将 PersonalityChangeRequest 转换为 GrowthRecord-like dict。

        PCR 结构（Phase 3.5.8）:
          {
            "request_id": str,
            "source_proposal_id": str,
            "source_insight_id": str,
            "confidence": float,
            "evidence_count": int,
            "reason": str,
            "evolution_record": {"changes": {"warmth": {"delta": 0.01}, ...}},
            "growth_records": [...],
            "timestamp": str,
          }

        → GrowthRecord-like:
          {
            "record_id": str,
            "source_type": "personality_change",
            "growth_signal": "personality_evolution",
            "growth_level": "trait",
            "affected_dimensions": {"warmth": 0.01, ...},
            "confidence": float,
            "reason": str,
            "source_event_id": str,
            "timestamp": str,
          }
        """
        if not pcr:
            return None

        pcr_id = pcr.get("request_id", "")
        if not pcr_id:
            return None

        # 提取 trait changes
        evo = pcr.get("evolution_record") or {}
        changes = evo.get("changes") or {}
        affected: Dict[str, float] = {}
        if isinstance(changes, dict):
            for trait, change in changes.items():
                if isinstance(change, dict):
                    delta = float(change.get("delta", 0.0) or 0.0)
                else:
                    delta = float(change or 0.0)
                if abs(delta) > 0:
                    affected[trait] = round(delta, 5)

        # 如果没有 trait changes，尝试从 growth_records 提取
        if not affected:
            for gr in pcr.get("growth_records") or []:
                ad = gr.get("affected_dimensions", {}) or {}
                for k, v in ad.items():
                    affected[k] = affected.get(k, 0.0) + float(v)

        if not affected:
            logger.debug(
                "SelfModelUpdaterAdapter: PCR %s 无有效 trait changes，跳过", pcr_id
            )
            return None

        record: Dict[str, Any] = {
            "record_id": pcr_id,
            "source_type": "personality_change_request",
            "growth_signal": "personality_evolution",
            "growth_level": "trait",
            "affected_dimensions": affected,
            "confidence": float(pcr.get("confidence", 0.5)),
            "reason": pcr.get("reason", ""),
            "source_event_id": pcr.get("source_proposal_id", "")
            or pcr.get("source_insight_id", ""),
            "evidence_ids": [
                pcr_id,
                pcr.get("source_proposal_id", ""),
                pcr.get("source_insight_id", ""),
            ],
            "timestamp": pcr.get("timestamp", _now_iso()),
        }

        # 清理空值
        record["evidence_ids"] = [e for e in record["evidence_ids"] if e]

        return record

    def _insight_to_growth_record(
        self,
        insight: Any,
    ) -> Optional[Dict[str, Any]]:
        """
        将 ReflectionInsight 转换为 GrowthRecord-like dict。

        Insight 结构（Phase 3.5.8）:
          {
            "insight_id": str,
            "pattern_detected": str,
            "pattern_frequency": int,
            "confidence": float,
            "summary": str,
            "timestamp": str,
          }

        → GrowthRecord-like:
          {
            "record_id": str,
            "source_type": "reflection_insight",
            "growth_signal": str,
            "growth_level": "pattern",
            "affected_dimensions": {"self_awareness": ...},
            "confidence": float,
            "reason": str,
            "source_event_id": str,
            "timestamp": str,
          }
        """
        if insight is None:
            return None

        iid = (
            getattr(insight, "insight_id", None)
            or (insight.get("insight_id") if isinstance(insight, dict) else "")
        )
        if not iid:
            return None

        pattern = (
            getattr(insight, "pattern_detected", "")
            or (insight.get("pattern_detected") if isinstance(insight, dict) else "")
        )
        frequency = (
            getattr(insight, "pattern_frequency", 0)
            or (insight.get("pattern_frequency") if isinstance(insight, dict) else 0)
            or 0
        )
        confidence = (
            getattr(insight, "confidence", 0.0)
            or (insight.get("confidence") if isinstance(insight, dict) else 0.0)
            or 0.0
        )
        summary = (
            getattr(insight, "summary", "")
            or (insight.get("summary") if isinstance(insight, dict) else "")
        )
        timestamp = (
            getattr(insight, "timestamp", "")
            or (insight.get("timestamp") if isinstance(insight, dict) else "")
            or _now_iso()
        )

        record: Dict[str, Any] = {
            "record_id": iid,
            "source_type": "reflection_insight",
            "growth_signal": pattern or "reflection_pattern",
            "growth_level": "pattern",
            "affected_dimensions": {
                "self_awareness": round(min(0.05, float(confidence) * 0.05), 5),
            },
            "confidence": float(confidence),
            "reason": summary or pattern or "",
            "source_event_id": iid,
            "evidence_ids": [iid],
            "timestamp": timestamp,
        }

        return record

    # ============================================================
    # 内部：SelfModelManager 指标同步
    # ============================================================

    def _sync_manager_understanding(
        self,
        manager: Any,
        proposal: Any,
    ) -> None:
        """
        将 Proposal 的变化同步到 SelfModelManager。

        1) 更新理解指标（experience_awareness / trait_awareness / identity_continuity）
        2) 更新 stable_traits（基于 proposal.source.affected_dimensions）

        Args:
            manager: SelfModelManager 实例
            proposal: SelfModelChangeProposal
        """
        if manager is None:
            return

        try:
            # 确保 identity 存在
            if not hasattr(manager, "identity") or manager.identity is None:
                return

            confidence = 0.5
            if hasattr(proposal, "source") and isinstance(proposal.source, dict):
                confidence = float(proposal.source.get("confidence", 0.5))

            increment = min(0.05, confidence * 0.05)

            # 更新理解指标
            identity = manager.identity

            if hasattr(identity, "experience_awareness"):
                identity.experience_awareness = round(
                    min(0.95, identity.experience_awareness + increment), 4
                )

            if hasattr(identity, "trait_awareness"):
                identity.trait_awareness = round(
                    min(0.95, identity.trait_awareness + increment * 0.8), 4
                )

            if hasattr(identity, "identity_continuity"):
                identity.identity_continuity = round(
                    min(0.95, identity.identity_continuity + increment * 0.6), 4
                )

            # 拆分 growth_level 信息
            growth_level = "context"
            if hasattr(proposal, "change") and isinstance(proposal.change, dict):
                growth_level = proposal.change.get("growth_level", "context")

            if growth_level in ("trait", "preference"):
                if hasattr(identity, "identity_continuity"):
                    identity.identity_continuity = round(
                        min(0.95, identity.identity_continuity + increment * 0.5), 4
                    )

            # Phase 3.8.6: 同步 stable_traits 更新
            # 从 proposal.source.affected_dimensions 提取 trait delta
            affected: Dict[str, float] = {}
            if hasattr(proposal, "source") and isinstance(proposal.source, dict):
                affected = proposal.source.get("affected_dimensions", {}) or {}

            if affected and hasattr(identity, "stable_traits"):
                for trait_name, delta in affected.items():
                    delta_f = float(delta)
                    if abs(delta_f) < 0.0001:
                        continue
                    # 查找已有 trait
                    found = False
                    for st in identity.stable_traits:
                        if hasattr(st, "trait") and st.trait == trait_name:
                            old_v = st.current_value
                            st.current_value = round(
                                max(0.0, min(1.0, old_v + delta_f)), 4
                            )
                            st.confidence = round(
                                min(0.95, st.confidence + increment * 0.5), 4
                            )
                            found = True
                            break
                    if not found:
                        # 新 trait：创建并追加
                        try:
                            from src.contracts.self_model_schema import StableTrait
                            new_trait = StableTrait(
                                trait=trait_name,
                                current_value=round(max(0.0, min(1.0, 0.5 + delta_f)), 4),
                                stability=0.5,
                                confidence=round(confidence * 0.8, 4),
                                sources=["self_model_updater_adapter"],
                            )
                            identity.stable_traits.append(new_trait)
                        except Exception:
                            pass  # 创建失败静默跳过

            # 版本 & 时间
            if hasattr(identity, "version"):
                identity.version += 1
            if hasattr(identity, "last_updated"):
                identity.last_updated = _now_iso()

            logger.debug(
                "SelfModelUpdaterAdapter: 同步 Manager 理解指标 "
                "exp=%.3f trait=%.3f cont=%.3f",
                getattr(identity, "experience_awareness", 0),
                getattr(identity, "trait_awareness", 0),
                getattr(identity, "identity_continuity", 0),
            )

        except Exception as e:
            logger.warning(
                "SelfModelUpdaterAdapter: 同步 Manager 指标失败（已隔离）: %s", e
            )