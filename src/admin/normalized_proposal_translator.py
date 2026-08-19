# -*- coding: utf-8 -*-
"""
src/admin/normalized_proposal_translator.py

Phase 3.6.3: 业务层 GrowthProposal 统一入口。

背景：
- Phase 3.6.2 完成 GrowthProposalNormalizer（A/B schema 转换层，lossless）
- Phase 3.6.3 接入主链路：业务模块统一调用本类
- 下游（SelfModelConsumer / PersonalityAdapter）只处理 canonical dict
- 不再有 ``if "proposal_id" / elif "id"`` 双重判断扩散

职责：
1. detect(proposal) -> str
   探测 proposal 形态（"canonical" / "governance" / "unknown"）
   委托给 GrowthProposalNormalizer.detect

2. normalize(proposal) -> dict
   把任意 schema 归一为 canonical dict（7 字段 + extra）
   委托给 GrowthProposalNormalizer.normalize_to_canonical

3. translate(proposal, ...) -> dict
   标准入口：先归一化，再调用 GrowthProposalTranslator._translate_canonical
   返回 PCR dict
   下游（SelfModelConsumer）应当只使用本方法

设计原则：
- 不持有任何状态（纯函数 + 静态方法）
- 不依赖 Runtime / Memory / Emotion / Growth Pipeline
- Normalizer 失败时回退到内嵌双分支逻辑（不抛异常）
- 与 GrowthProposalTranslator 完全兼容（测试与审计不受影响）
- 0 副作用：不做 I/O、不发事件、不写盘

迁移指南（Phase 3.6.3 之后）：
    # 旧：
    from src.admin.selfmodel_consumer import GrowthProposalTranslator
    pcr = GrowthProposalTranslator.translate(proposal)

    # 新：
    from src.admin.normalized_proposal_translator import NormalizedProposalTranslator
    pcr = NormalizedProposalTranslator.translate(proposal)

行为兼容性：
- canonical 输入：与原 GrowthProposalTranslator.translate 输出一致
- governance 输入：先归一化再翻译，输出字段保持兼容；
  evaluator_meta 多了 _normalized / _normalizer_version 标记
- _source_schema 仍记录原始 schema（"growth_schema" / "proposal"）
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ============================================================
# NormalizedProposalTranslator
# ============================================================

class NormalizedProposalTranslator:
    """
    GrowthProposal 业务层统一入口（Phase 3.6.3）。

    用法：
        from src.admin.normalized_proposal_translator import NormalizedProposalTranslator

        # 标准翻译入口
        pcr = NormalizedProposalTranslator.translate(proposal)

        # 仅归一化（不翻译）
        canonical = NormalizedProposalTranslator.normalize(proposal)

        # 仅检测 schema
        kind = NormalizedProposalTranslator.detect(proposal)

    所有方法均为静态接口；保留类形式仅为上层调用便利与未来扩展。
    """

    # ============================================================
    # Schema 检测
    # ============================================================

    @staticmethod
    def detect(proposal: Any) -> str:
        """
        探测 proposal 形态。

        Returns:
            "canonical" | "governance" | "unknown"

        Notes:
            委托给 GrowthProposalNormalizer.detect；
            本方法保留是为了业务层显式表达"我关心 schema 形态"这一语义。
        """
        try:
            from src.contracts.proposal_normalizer import detect as _detect
            return _detect(proposal)
        except Exception as e:
            logger.warning(f"NormalizedProposalTranslator.detect 异常: {e}")
            return "unknown"

    # ============================================================
    # 归一化
    # ============================================================

    @staticmethod
    def normalize(proposal: Any) -> Dict[str, Any]:
        """
        把任意 dict 归一为 canonical 形态（7 字段 + extra）。

        Returns:
            canonical dict（id / proposed_changes / evidence_ids / confidence /
            evaluator_meta / status / timestamp）

        Notes:
            委托给 GrowthProposalNormalizer.normalize_to_canonical；
            本方法保留是为了让业务层能显式获取 canonical 中间产物。
        """
        try:
            from src.contracts.proposal_normalizer import (
                normalize_to_canonical as _normalize,
            )
            return _normalize(proposal)
        except Exception as e:
            logger.warning(
                f"NormalizedProposalTranslator.normalize 异常: {e}"
            )
            # 兜底：返回最小合法 canonical dict
            return _safe_empty_canonical()

    # ============================================================
    # 标准翻译入口
    # ============================================================

    @staticmethod
    def translate(
        proposal: Any,
        source_insight_id: Optional[str] = None,
        extra_evaluator_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        GrowthProposal dict → PCR dict 的标准入口（Phase 3.6.3 推荐）。

        流程：
            proposal (A/B/unknown)
              ↓ GrowthProposalNormalizer.normalize_to_canonical
            canonical dict
              ↓ GrowthProposalTranslator._translate_canonical
            PCR dict

        Args:
            proposal: 任意 GrowthProposal dict（A / B / unknown）
            source_insight_id: 可选，覆盖 source_insight_id
            extra_evaluator_meta: 可选，合并到 evaluator_meta

        Returns:
            PCR dict（与 GrowthProposalTranslator.translate 同结构）

        Raises:
            ValueError: proposal 不是 dict / 完全无法识别 / proposal_id 缺失
        """
        # 1) 基础校验
        if not isinstance(proposal, dict):
            raise ValueError(
                f"proposal must be dict, got {type(proposal).__name__}"
            )

        # 2) 探测 schema
        try:
            schema_kind = NormalizedProposalTranslator.detect(proposal)
        except Exception:
            schema_kind = "unknown"

        # 3) 归一化（B / unknown 都先归一）
        #    归一化失败时回退到旧双分支（通过 GrowthProposalTranslator 入口）
        if schema_kind != "canonical":
            try:
                canonical = NormalizedProposalTranslator.normalize(proposal)
                if not isinstance(canonical, dict) or not canonical.get("id"):
                    # 归一化失败或产出空 id：回退到 GrowthProposalTranslator
                    raise ValueError("normalize produced empty canonical")
            except Exception as e:
                logger.warning(
                    f"NormalizedProposalTranslator.translate: 归一化失败，"
                    f"回退到 GrowthProposalTranslator 双分支路径: {e}"
                )
                from src.admin.selfmodel_consumer import (
                    GrowthProposalTranslator,
                )
                return GrowthProposalTranslator.translate(
                    proposal,
                    source_insight_id=source_insight_id,
                    extra_evaluator_meta=extra_evaluator_meta,
                )

            # 把 _source_schema 写回（保持审计兼容）
            em = canonical.get("evaluator_meta")
            if not isinstance(em, dict):
                em = {}
                canonical["evaluator_meta"] = em
            if "_source_schema" not in em:
                em["_source_schema"] = (
                    "proposal" if schema_kind == "governance" else "unknown"
                )

            # 4) 委托给 _translate_canonical（仅 canonical 路径）
            try:
                from src.admin.selfmodel_consumer import (
                    GrowthProposalTranslator,
                )
                return GrowthProposalTranslator._translate_canonical(
                    proposal=canonical,
                    schema=em.get("_source_schema", "unknown"),
                    source_insight_id=source_insight_id,
                    extra_evaluator_meta=extra_evaluator_meta,
                    _normalized=True,
                )
            except Exception as e:
                logger.warning(
                    f"NormalizedProposalTranslator.translate: _translate_canonical "
                    f"异常，回退到 GrowthProposalTranslator.translate: {e}"
                )
                from src.admin.selfmodel_consumer import (
                    GrowthProposalTranslator,
                )
                return GrowthProposalTranslator.translate(
                    proposal,
                    source_insight_id=source_insight_id,
                    extra_evaluator_meta=extra_evaluator_meta,
                )

        # 5) 已是 canonical：A 路径直通
        try:
            from src.admin.selfmodel_consumer import (
                GrowthProposalTranslator,
            )
            return GrowthProposalTranslator._translate_canonical(
                proposal=proposal,
                schema="growth_schema",
                source_insight_id=source_insight_id,
                extra_evaluator_meta=extra_evaluator_meta,
                _normalized=False,
            )
        except Exception as e:
            logger.warning(
                f"NormalizedProposalTranslator.translate: canonical 路径异常，"
                f"回退到 GrowthProposalTranslator.translate: {e}"
            )
            from src.admin.selfmodel_consumer import (
                GrowthProposalTranslator,
            )
            return GrowthProposalTranslator.translate(
                proposal,
                source_insight_id=source_insight_id,
                extra_evaluator_meta=extra_evaluator_meta,
            )

    # ============================================================
    # 便捷方法
    # ============================================================

    @staticmethod
    def is_canonical(proposal: Any) -> bool:
        """判断 proposal 是否已是 canonical 形态。"""
        return NormalizedProposalTranslator.detect(proposal) == "canonical"

    @staticmethod
    def is_governance(proposal: Any) -> bool:
        """判断 proposal 是否是 governance（B schema）形态。"""
        return NormalizedProposalTranslator.detect(proposal) == "governance"

    @staticmethod
    def needs_normalization(proposal: Any) -> bool:
        """判断 proposal 是否需要归一化（非 canonical 即可）。"""
        return NormalizedProposalTranslator.detect(proposal) != "canonical"


# ============================================================
# 工具函数
# ============================================================

def _safe_empty_canonical() -> Dict[str, Any]:
    """返回最小合法 canonical dict（兜底用）。"""
    from src.contracts.proposal_normalizer import _now_iso
    return {
        "id": "",
        "proposed_changes": [],
        "evidence_ids": [],
        "confidence": 0.0,
        "evaluator_meta": {},
        "status": "proposed",
        "timestamp": _now_iso(),
    }


# ============================================================
# 公开 API
# ============================================================

__all__ = [
    "NormalizedProposalTranslator",
]
