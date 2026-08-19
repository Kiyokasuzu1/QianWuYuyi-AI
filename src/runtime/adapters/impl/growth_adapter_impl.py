# -*- coding: utf-8 -*-
"""
src/runtime/adapters/impl/growth_adapter_impl.py

Phase 3.7.2: GrowthAdapterImpl —— Growth 模块 Adapter 实现

职责：
- 桥接 Runtime Adapter 接口（GrowthAdapterSpec）到 GrowthEngine
- 强制使用 canonical GrowthProposal（来自 src.contracts.growth_schema）
- legacy schema 走 ProposalNormalizer 转换
- 不修改 GrowthEngine / Normalizer

Runtime → GrowthAdapterSpec → GrowthAdapterImpl → GrowthEngine + Normalizer

约束：
- submit(proposal) 必须接收 canonical GrowthProposal（schema_version="1.0"）
- evaluate(event) 输出 List[GrowthProposal]（canonical）
- 不重定义 GrowthProposal 字段
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.runtime.adapters.base import AdapterBase
from src.runtime.adapters.growth_adapter import GrowthAdapterSpec
from src.runtime.events import Event

logger = logging.getLogger(__name__)


class GrowthAdapterImpl(GrowthAdapterSpec):
    """Growth 模块 Adapter 实现（Phase 3.7.2 / v1.0）

    桥接 Runtime 抽象接口到现有 GrowthEngine，
    所有输出 GrowthProposal 均为 canonical schema。
    """

    name: str = "growth_adapter_impl"
    schema_version: str = "1.0"

    def __init__(self, growth_engine: Optional[Any] = None) -> None:
        super().__init__()
        self._engine: Optional[Any] = growth_engine
        self._last_proposals: List[Any] = []

    # --------------------------------------------------------
    # AdapterBase 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        """注入 GrowthEngine（默认自建）。"""
        if self._engine is None:
            try:
                from src.growth.growth_engine import GrowthEngine
                self._engine = GrowthEngine()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "GrowthAdapterImpl.attach() 自建 GrowthEngine 失败: %s", exc
                )
                self._engine = None
        self._mark_attached()
        self._cache_health({
            "healthy": self._engine is not None,
            "name": self.name,
            "schema_version": self.schema_version,
        })

    def detach(self) -> None:
        """解除接入（不关闭业务实例,只清空引用）。"""
        self._engine = None
        self._mark_detached()

    def health_check(self) -> Dict[str, Any]:
        healthy = self.is_attached and self._engine is not None
        result = {
            "healthy": healthy,
            "name": self.name,
            "schema_version": self.schema_version,
        }
        self._cache_health(result)
        return result

    # --------------------------------------------------------
    # Growth 业务接口实现
    # --------------------------------------------------------
    def evaluate(self, event: Event) -> List[Any]:
        """评估事件并生成 canonical GrowthProposal 列表。

        流程：
        1) 把 Event 转换为 GrowthEngine.apply 接受的 dict 形式
        2) 调 GrowthEngine.apply 让状态更新
        3) 用 canonical GrowthProposal 包装结果（schema_version="1.0"）
        """
        if not self.is_attached or self._engine is None:
            return []

        try:
            from src.contracts.growth_schema import (
                GrowthProposal,
                ChangeItem,
                CANONICAL_SCHEMA_VERSION,
            )

            event_dict: Dict[str, Any] = {
                "event_id": getattr(event, "id", ""),
                "type": getattr(event, "type", "user_input"),
                "source": getattr(event, "source", None),
                "timestamp": getattr(event, "timestamp", None),
                "payload": getattr(event, "payload", {}) or {},
                "is_first_occurrence": True,
            }

            result = self._engine.apply(event_dict)
            if not isinstance(result, dict):
                result = {}

            # 构建 canonical GrowthProposal
            metrics = result.get("metrics", {}) or {}
            changes: List[ChangeItem] = []
            for path, after in metrics.items():
                changes.append(ChangeItem(
                    path=f"growth_state.metrics.{path}",
                    before=None,
                    after=after,
                    reason=f"GrowthEngine.apply 评估事件 {event_dict['event_id']}",
                ))

            proposal = GrowthProposal(
                source_event_id=event_dict["event_id"],
                proposed_changes=changes,
                confidence=0.6,
                evidence_ids=[event_dict["event_id"]],
                evaluator_meta={
                    "_source": "GrowthAdapterImpl.evaluate",
                    "milestone": result.get("milestone", False),
                },
                schema_version=CANONICAL_SCHEMA_VERSION,
            )

            proposals: List[GrowthProposal] = [proposal]
            self._last_proposals = proposals
            return proposals
        except Exception as exc:  # noqa: BLE001
            logger.warning("GrowthAdapterImpl.evaluate() 失败: %s", exc)
            return []

    def submit(self, proposal: Any) -> Any:
        """提交 canonical GrowthProposal 给下游（PersonalityAdapter 等）。

        校验：
        - proposal 必须是 canonical GrowthProposal
        - schema_version 必须是 "1.0"
        - legacy schema 走 Normalizer
        """
        if not self.is_attached:
            return {"accepted": False, "reason": "not_attached"}

        try:
            from src.contracts.growth_schema import (
                GrowthProposal,
                CANONICAL_SCHEMA_VERSION,
            )
            from src.contracts.proposal_normalizer import (
                normalize_to_canonical,
                detect,
            )

            # 已经是 canonical
            if isinstance(proposal, GrowthProposal):
                if proposal.schema_version != CANONICAL_SCHEMA_VERSION:
                    return {
                        "accepted": False,
                        "reason": (
                            f"schema_version {proposal.schema_version!r} "
                            f"!= canonical {CANONICAL_SCHEMA_VERSION!r}"
                        ),
                    }
                return {
                    "accepted": True,
                    "proposal_id": proposal.id,
                    "schema_version": proposal.schema_version,
                }

            # dict 输入：先检测 schema
            if isinstance(proposal, dict):
                kind = detect(proposal)
                if kind == "canonical":
                    if proposal.get("schema_version") != CANONICAL_SCHEMA_VERSION:
                        return {
                            "accepted": False,
                            "reason": (
                                f"schema_version {proposal.get('schema_version')!r} "
                                f"!= canonical {CANONICAL_SCHEMA_VERSION!r}"
                            ),
                        }
                    return {
                        "accepted": True,
                        "proposal_id": proposal.get("id"),
                        "schema_version": proposal.get("schema_version"),
                    }

                if kind == "governance":
                    canonical = normalize_to_canonical(proposal)
                    # Normalizer 不自动写 schema_version;Phase 3.7.2 补齐
                    canonical.setdefault(
                        "schema_version", CANONICAL_SCHEMA_VERSION
                    )
                    return {
                        "accepted": True,
                        "proposal_id": canonical.get("id"),
                        "schema_version": canonical.get("schema_version"),
                        "normalized": True,
                    }

                # unknown 仍尝试 normalize（Normalizer 内部防御）
                canonical = normalize_to_canonical(proposal)
                canonical.setdefault(
                    "schema_version", CANONICAL_SCHEMA_VERSION
                )
                return {
                    "accepted": True,
                    "proposal_id": canonical.get("id"),
                    "schema_version": canonical.get("schema_version"),
                    "normalized": True,
                }

            return {"accepted": False, "reason": f"unsupported type: {type(proposal)!r}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("GrowthAdapterImpl.submit() 失败: %s", exc)
            return {"accepted": False, "reason": str(exc)}

    # --------------------------------------------------------
    # 状态查询
    # --------------------------------------------------------
    @property
    def last_proposals(self) -> List[Any]:
        return list(self._last_proposals)
