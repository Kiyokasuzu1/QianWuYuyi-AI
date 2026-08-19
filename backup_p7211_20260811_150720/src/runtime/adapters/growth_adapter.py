"""
GrowthAdapter —— 反思到成长的适配器

职责：
- 将 ReflectionInsight 转换为 GrowthProposal
- 存储提案到独立的 JSON 文件
- 不直接修改人格，只生成提案供审批

设计原则：
- 不修改 Growth 核心逻辑
- 不直接修改人格
- 所有变更必须通过 GrowthProposal 审批流程
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.contracts.experience_schema import (
    ReflectionInsight,
    GrowthAdapterBase,
)
from src.contracts.growth_schema import GrowthProposal, ChangeItem

logger = logging.getLogger(__name__)


class GrowthAdapter(GrowthAdapterBase):
    """
    Growth 适配器

    将 ReflectionInsight 转换为 GrowthProposal 并存储。
    不直接修改人格，所有变更必须通过审批。
    """

    def __init__(self, proposals_path: Optional[str] = None):
        """
        初始化 GrowthAdapter

        Args:
            proposals_path: 提案存储路径
        """
        self._path = Path(proposals_path or "data/proposals/growth_proposals.json")
        self._path.parent.mkdir(parents=True, exist_ok=True)

        # 确保文件存在
        if not self._path.exists():
            self._save([])

        # 洞察到提案的映射：insight_id -> proposal_id
        self._proposal_map: Dict[str, str] = {}

    def store_insight(self, insight: ReflectionInsight) -> bool:
        """
        将反思洞察存储为 GrowthProposal

        Args:
            insight: 反思洞察

        Returns:
            是否存储成功
        """
        if not insight:
            return False

        try:
            # 检查是否已存在对应的提案
            if insight.insight_id in self._proposal_map:
                logger.debug(f"洞察已存在提案: {insight.insight_id}")
                return True

            # 从洞察生成提案
            proposal = self._create_proposal(insight)

            # 存储提案
            proposals = self._load()
            proposals.append(proposal)
            self._save(proposals)

            # 记录映射
            self._proposal_map[insight.insight_id] = proposal.id

            logger.info(
                f"洞察已转为 GrowthProposal: {insight.insight_id} -> {proposal.id}"
            )
            return True

        except Exception as e:
            logger.error(f"存储洞察失败: {e}")
            return False

    def get_recent_insights(self, limit: int = 10) -> List[ReflectionInsight]:
        """
        获取最近的洞察（从提案转换回）

        Args:
            limit: 最大数量

        Returns:
            洞察列表
        """
        try:
            proposals = self._load()
            # 按时间倒序
            proposals.sort(
                key=lambda p: p.timestamp,
                reverse=True,
            )
            return [self._convert_to_insight(p) for p in proposals[:limit]]

        except Exception as e:
            logger.error(f"获取最近洞察失败: {e}")
            return []

    def create_proposal_from_insight(
        self,
        insight: ReflectionInsight,
    ) -> GrowthProposal:
        """
        从洞察创建 GrowthProposal

        Args:
            insight: 反思洞察

        Returns:
            GrowthProposal
        """
        return self._create_proposal(insight)

    def list_proposals(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[GrowthProposal]:
        """
        列出提案

        Args:
            status: 状态过滤（proposed/accepted/rejected）
            limit: 最大数量

        Returns:
            提案列表
        """
        try:
            proposals = self._load()

            # 按状态过滤
            if status:
                proposals = [p for p in proposals if p.status == status]

            # 按时间倒序
            proposals.sort(
                key=lambda p: p.timestamp,
                reverse=True,
            )

            return proposals[:limit]

        except Exception as e:
            logger.error(f"列出提案失败: {e}")
            return []

    def accept_proposal(self, proposal_id: str) -> Optional[GrowthProposal]:
        """
        接受提案（仅更新状态，不直接修改人格）

        Args:
            proposal_id: 提案 ID

        Returns:
            更新后的提案，若不存在返回 None
        """
        try:
            proposals = self._load()

            for p in proposals:
                if p.id == proposal_id:
                    if p.status == "proposed":
                        p.status = "accepted"
                        p.accepted_at = datetime.utcnow().isoformat() + "Z"
                        self._save(proposals)
                        logger.info(f"提案已接受: {proposal_id}")
                        return p
                    else:
                        logger.warning(f"提案状态非 proposed: {proposal_id}, 当前状态: {p.status}")
                        return p

            logger.warning(f"提案不存在: {proposal_id}")
            return None

        except Exception as e:
            logger.error(f"接受提案失败: {e}")
            return None

    def reject_proposal(self, proposal_id: str) -> Optional[GrowthProposal]:
        """
        拒绝提案

        Args:
            proposal_id: 提案 ID

        Returns:
            更新后的提案，若不存在返回 None
        """
        try:
            proposals = self._load()

            for p in proposals:
                if p.id == proposal_id:
                    if p.status == "proposed":
                        p.status = "rejected"
                        p.rejected_at = datetime.utcnow().isoformat() + "Z"
                        self._save(proposals)
                        logger.info(f"提案已拒绝: {proposal_id}")
                        return p
                    else:
                        return p

            logger.warning(f"提案不存在: {proposal_id}")
            return None

        except Exception as e:
            logger.error(f"拒绝提案失败: {e}")
            return None

    def update_proposal(self, proposal: GrowthProposal) -> bool:
        """
        更新提案

        Args:
            proposal: 更新后的提案

        Returns:
            是否成功
        """
        try:
            proposals = self._load()

            # 查找并更新
            found = False
            for i, p in enumerate(proposals):
                if p.id == proposal.id:
                    proposals[i] = proposal
                    found = True
                    break

            if not found:
                proposals.append(proposal)

            self._save(proposals)
            return True

        except Exception as e:
            logger.error(f"更新提案失败: {e}")
            return False

    def clear_history(self) -> None:
        """清空所有提案"""
        self._save([])
        self._proposal_map.clear()

    # ==================== 内部方法 ====================

    def _create_proposal(self, insight: ReflectionInsight) -> GrowthProposal:
        """
        从 ReflectionInsight 创建 GrowthProposal

        Args:
            insight: 反思洞察

        Returns:
            GrowthProposal
        """
        # 根据洞察类型生成变更建议
        changes = []

        if insight.insight_type == "pattern":
            # 模式类型：添加调节建议
            changes.append(ChangeItem(
                path="self_state.initiative",
                before=None,
                after=self._suggest_adjustment(insight, "initiative"),
                reason=f"模式检测: {insight.pattern_detected}",
            ))
        elif insight.insight_type == "problem":
            # 问题类型：添加修复建议
            changes.append(ChangeItem(
                path="self_state.energy_decay_rate",
                before=None,
                after=self._suggest_adjustment(insight, "energy_decay_rate"),
                reason=f"问题修复: {insight.summary}",
            ))

        # 添加通用调整建议
        if insight.suggested_adjustments:
            for i, adjustment in enumerate(insight.suggested_adjustments[:2]):
                changes.append(ChangeItem(
                    path=f"runtime.adjustment_{i}",
                    before=None,
                    after=adjustment,
                    reason=insight.summary,
                ))

        # 创建提案
        proposal = GrowthProposal(
            source_event_id=insight.insight_id,
            proposed_changes=changes,
            confidence=insight.confidence,
            evidence_ids=insight.experience_ids,
            evaluator_meta={
                "insight_type": insight.insight_type,
                "pattern_detected": insight.pattern_detected,
                "pattern_frequency": insight.pattern_frequency,
                "used_llm": insight.used_llm,
            },
            timestamp=insight.timestamp,
        )

        return proposal

    def _suggest_adjustment(
        self,
        insight: ReflectionInsight,
        parameter: str,
    ) -> float:
        """
        根据洞察建议参数调整值

        Args:
            insight: 反思洞察
            parameter: 参数名

        Returns:
            建议的调整值
        """
        # 基础调整逻辑
        if insight.pattern_detected == "high_frequency_proactive":
            if parameter == "initiative":
                return -0.1  # 降低主动性
            elif parameter == "social_need":
                return -0.05  # 轻微降低社交需求

        elif insight.pattern_detected == "low_success_rate":
            if parameter == "initiative":
                return -0.05  # 降低主动性，减少失败
            elif parameter == "energy_decay_rate":
                return -0.02  # 降低精力衰减

        elif insight.pattern_detected == "low_user_response":
            if parameter == "initiative":
                return -0.1  # 降低主动性
            elif parameter == "social_need":
                return -0.1  # 降低社交需求

        # 默认：不调整
        return 0.0

    def _convert_to_insight(self, proposal: GrowthProposal) -> ReflectionInsight:
        """
        将 GrowthProposal 转换回 ReflectionInsight

        Args:
            proposal: 提案

        Returns:
            反思洞察
        """
        meta = proposal.evaluator_meta
        return ReflectionInsight(
            insight_id=proposal.source_event_id or proposal.id,
            timestamp=proposal.timestamp,
            insight_type=meta.get("insight_type", "unknown"),
            summary=f"Proposal {proposal.status}: {len(proposal.proposed_changes)} changes",
            pattern_detected=meta.get("pattern_detected", ""),
            pattern_frequency=meta.get("pattern_frequency", 0),
            confidence=proposal.confidence,
            experience_ids=proposal.evidence_ids,
            used_llm=meta.get("used_llm", False),
        )

    def _load(self) -> List[GrowthProposal]:
        """加载所有提案"""
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return [GrowthProposal.from_dict(d) for d in data]
        except Exception:
            return []

    def _save(self, proposals: List[GrowthProposal]) -> None:
        """保存所有提案"""
        try:
            data = [p.to_dict() for p in proposals]
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存提案失败: {e}")


# ============================================================
# Phase 3.7.1: 新的抽象接口设计
# ============================================================
# 说明：
# - 上方 `GrowthAdapter` 是 Phase 3.5.x 反思→成长的具体实现（保留供现有代码使用）
# - 下方 `GrowthAdapterSpec` 是 Phase 3.7.1 Runtime Adapter Layer 设计层接口
# - Runtime 接入 Growth 时推荐使用 `GrowthAdapterSpec`（抽象接口）
# - 由于 Python 不允许同名类，新设计采用 `GrowthAdapterSpec` 命名以避免与遗留实现冲突
# - **关键约束**：所有 GrowthProposal 必须使用 canonical schema（来自 src.contracts.growth_schema）
#                  Spec 仅在接口层使用 Any，**不**复制 / 重新定义 GrowthProposal 字段

from src.runtime.adapters.base import AdapterBase as _AdapterBase
from src.runtime.events import Event as _Event
from src.contracts.growth_schema import GrowthProposal as _CanonicalGrowthProposal


class GrowthAdapterSpec(_AdapterBase):
    """Growth 模块 Adapter 抽象接口（Phase 3.7.1 / v1.0）

    Runtime 接入 Growth 系统的统一入口。

    职责：
    - 封装 Growth 系统的访问
    - Runtime 不直接依赖 GrowthEngine
    - **必须使用 canonical GrowthProposal**（来自 src.contracts.growth_schema）
    - 不绕过 Normalizer

    接口（v1.0 冻结）：
    - evaluate(event)   - 评估事件并生成 canonical GrowthProposal
    - submit(proposal)  - 提交 canonical GrowthProposal 给下游（Personality Adapter 等）

    继承：
    - AdapterBase（提供 attach / detach / health_check 生命周期）
    """
    name: str = "growth_adapter_spec"
    schema_version: str = "1.0"

    def __init__(self) -> None:
        super().__init__()

    # --------------------------------------------------------
    # AdapterBase 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        """接入 Growth 系统（设计阶段仅标记状态）。"""
        self._mark_attached()
        self._cache_health({
            "healthy": True,
            "name": self.name,
            "schema_version": self.schema_version,
        })

    def detach(self) -> None:
        """解除接入。"""
        self._mark_detached()

    def health_check(self) -> Dict[str, Any]:
        result = {
            "healthy": self.is_attached,
            "name": self.name,
            "schema_version": self.schema_version,
        }
        self._cache_health(result)
        return result

    # --------------------------------------------------------
    # Growth 业务接口（接口骨架，不实现）
    # --------------------------------------------------------
    def evaluate(self, event: _Event) -> Any:
        """评估事件并生成 canonical GrowthProposal（设计阶段仅返回 None）。

        业务实现约束：
        - 必须返回 List[GrowthProposal]（canonical schema）
        - 必须经过 Normalizer（如输入为 legacy schema）
        - schema_version 字段必须为 "1.0"

        Args:
            event: Runtime Event

        Returns:
            List[GrowthProposal]（canonical）
        """
        raise NotImplementedError(
            "GrowthAdapterSpec.evaluate() 接口未实现（设计阶段）"
        )

    def submit(self, proposal: _CanonicalGrowthProposal) -> Any:
        """提交 canonical GrowthProposal 给下游（设计阶段仅返回 None）。

        Args:
            proposal: canonical GrowthProposal（来自 src.contracts.growth_schema）

        Returns:
            提交结果（业务实现时返回 SubmissionResult）
        """
        raise NotImplementedError(
            "GrowthAdapterSpec.submit() 接口未实现（设计阶段）"
        )