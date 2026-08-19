"""
Phase 4.2-D — RelationshipEvidenceAdapter（关系证据 → Growth 薄转换器）

定位：「证据翻译层」，放在 Growth 侧、GrowthPipeline 入口附近。

职责边界（顾问任务卡冻结）：
- Relationship 负责回答："发生了什么关系事件？"
- Growth 负责回答："这个事件有没有足够证据支持长期变化？"
- 本适配器只做类型如实的翻译与接线，**绝不**：
  - 直接生成 trait / personality_change（绕过 GrowthEvaluator）
  - 修改 GrowthEvaluator / PersonalityResolver / IDENTITY_CORE
  - 引入关系权重 / 亲密度评分
  - 让 Relationship 直接产生人格变化

转换规则（类型如实标注）：
- preference_learning → event_type="preference"（偏好证据，走既有 preference 域映射，
  受 evaluator 阈值 + ProposalManager 0.8 置信门槛 + Approval 三重门控）
- 其余全部关系类型 → event_type="relationship"（evaluator 硬限制：
  relationship_context 域 / 最高 context 层 / applied_delta=0）

链路：
    RelationshipEvent
        ↓ convert()（本层，唯一新代码）
    digest raw event（EventExtractor 风格 dict）
        ↓ EventNormalizer → （恢复结构化权威 event_type）→ EventValidator
        ↓ EventHistoryMatcher → GrowthEvaluator（红线在此生效）
        ↓ growth_allowed 才继续
    GrowthIntegrationService.process_event()（既有 Approval 流入口）
        ↓
    GrowthProposal（status=pending，evidence_ids + evaluator_meta.source=relationship）

关键设计说明：
- Normalizer 的关键词重分类是为「不可信自由文本」设计的（其 VALID_EVENT_TYPES
  白名单不含 "preference"）；RelationshipEvent 携带的是结构化权威类型，
  因此 normalize 之后由本层恢复 event_type，保证 evaluator 领域映射如实。
- relationship_context 域提案 proposed_changes 为空（纯证据记录，delta 恒 0）。
- 全部 fail-soft：任何异常 → 返回 error 状态字典，绝不抛给调用方。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.contracts.growth_schema import ChangeItem

logger = logging.getLogger(__name__)


class RelationshipEvidenceAdapter:
    """RelationshipEvent → Growth digest 薄转换器（Phase 4.2-D）"""

    # 偏好学习类事件：如实标注为 preference 证据
    PREFERENCE_SOURCE_TYPES = frozenset({"preference_learning"})

    # 证据来源标识（写入 evaluator_meta，验收要求 source=relationship 可追踪）
    SOURCE_TAG = "relationship"

    def __init__(
        self,
        integration_service: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        Args:
            integration_service: GrowthIntegrationService 实例（可选注入，便于测试隔离；
                缺省时首次使用时懒创建，走默认 Approval 配置：
                auto_accept=False / confidence_threshold=0.8，绝不弱化安全门槛）
            config: 预留配置（当前不使用，保持构造契约稳定）
        """
        self._integration_service = integration_service
        self._config = config or {}

    # ============================================================
    # 步骤 1：类型如实转换（纯函数，无副作用）
    # ============================================================
    def convert(self, rel_event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """RelationshipEvent → digest raw event（EventExtractor 风格 dict）。

        只做翻译：类型映射 + 证据锚定 + 来源标注。
        不计算变化量、不判断成长资格（那是 Evaluator 的职责）。

        Returns:
            digest raw event dict；输入无效时返回 None。
        """
        if not isinstance(rel_event, dict):
            return None

        rel_type = str(rel_event.get("type") or "")
        if not rel_type:
            return None

        content = str(rel_event.get("content") or "")
        confidence = rel_event.get("confidence", 0.5)
        try:
            importance = float(confidence)
        except (TypeError, ValueError):
            importance = 0.5
        importance = min(max(importance, 0.0), 1.0)

        # 证据锚定：优先 evidence_ids，降级 source_memory_id
        evidence_ids = [str(x) for x in (rel_event.get("evidence_ids") or []) if x]
        source_memory_id = str(rel_event.get("source_memory_id") or "")
        if not evidence_ids and source_memory_id:
            evidence_ids = [source_memory_id]

        # 类型如实标注（转换规则冻结，见模块 docstring）
        if rel_type in self.PREFERENCE_SOURCE_TYPES:
            event_type = "preference"
        else:
            event_type = "relationship"

        anchor_id = evidence_ids[0] if evidence_ids else ""
        return {
            "event": f"relationship:{rel_type}",
            "topic": content[:60] or f"relationship:{rel_type}",
            "event_type": event_type,
            "importance": importance,
            "evidence": [
                {
                    "text": content,
                    "role": "user",
                    "source_index": 0,
                    "memory_id": anchor_id,
                }
            ],
            "source_ids": evidence_ids,
            "metadata": {
                "source": self.SOURCE_TAG,
                "relationship_event_id": str(rel_event.get("id") or ""),
                "relationship_event_type": rel_type,
            },
        }

    # ============================================================
    # 步骤 2-6：走既有 digest 管线 + Approval 流（fail-soft）
    # ============================================================
    def process_relationship_event(
        self, rel_event: Dict[str, Any],
    ) -> Dict[str, Any]:
        """完整链路：RelationshipEvent → GrowthProposal(pending) → Approval。

        Returns:
            {
              "pipeline_state": str,     # converted / created / deduped /
                                         # rejected_* / no_growth / error
              "proposal_id": str|None,
              "evaluated": dict|None,  # evaluator 输出摘要（审计用）
              "reasons": [str, ...],
            }
        """
        result: Dict[str, Any] = {
            "pipeline_state": "",
            "proposal_id": None,
            "evaluated": None,
            "reasons": [],
        }
        try:
            raw = self.convert(rel_event)
            if raw is None:
                result["pipeline_state"] = "rejected_invalid_event"
                result["reasons"].append("convert: 输入不是有效 RelationshipEvent")
                return result
            rel_meta = raw["metadata"]

            # Step 2: Normalize（产出 canonical_topic / event_id / 标准化证据）
            from src.growth.event_normalizer import EventNormalizer

            normalized_list = EventNormalizer().normalize([raw])
            if not normalized_list:
                result["pipeline_state"] = "rejected_no_evidence"
                result["reasons"].append("Normalizer 返回空（丢弃）")
                return result
            normalized = normalized_list[0]

            # 恢复结构化权威 event_type：
            # Normalizer 的关键词重分类面向不可信自由文本（白名单不含 preference），
            # RelationshipEvent 的类型是结构化权威信号，必须如实交给 Evaluator。
            normalized["event_type"] = raw["event_type"]
            normalized.setdefault("metadata", {})
            if isinstance(normalized["metadata"], dict):
                normalized["metadata"].update(rel_meta)

            # Step 3: Validate
            from src.growth.event_validator import EventValidator

            validator = EventValidator()
            if not validator.should_keep(normalized):
                decision = validator.decide(normalized) or ("discard", 0.0, "validator")
                result["pipeline_state"] = "rejected_no_evidence"
                result["reasons"].append(f"Validator discard: {decision}")
                return result

            # Step 4: Match 历史
            from src.growth.event_history_matcher import EventHistoryMatcher

            matcher = EventHistoryMatcher()
            canonical_topic = normalized.get("canonical_topic") or normalized.get("topic", "")
            history = matcher.get_history(canonical_topic, raw["event_type"])

            # Step 5: Evaluate（红线在此生效：relationship 永不进 preference/trait，delta=0）
            from src.growth.growth_evaluator import GrowthEvaluator

            evaluated = GrowthEvaluator().evaluate(normalized, history) or {}
            result["evaluated"] = {
                "event_type": evaluated.get("event_type"),
                "growth_domain": evaluated.get("growth_domain"),
                "growth_level": evaluated.get("growth_level"),
                "growth_allowed": evaluated.get("growth_allowed"),
                "applied_delta": evaluated.get("applied_delta"),
                "confidence": evaluated.get("confidence"),
                "growth_signal": evaluated.get("growth_signal"),
                "target_candidates": evaluated.get("target_candidates"),
            }

            if not evaluated.get("growth_allowed"):
                result["pipeline_state"] = "no_growth"
                result["reasons"].append(
                    f"Evaluator growth_allowed=False; "
                    f"level={evaluated.get('growth_level')} "
                    f"confidence={evaluated.get('confidence')}"
                )
                return result

            # Step 6: 构造 proposed_changes（与 accept_experience Step 6 同一形态）
            # relationship_context 域：纯证据记录，proposed_changes 恒为空（delta=0）
            proposed_changes: List[Any] = []
            if evaluated.get("growth_domain") != "relationship_context":
                target_candidates = list(evaluated.get("target_candidates") or [])
                applied_delta = float(evaluated.get("applied_delta") or 0.0)
                if target_candidates and applied_delta > 0:
                    for candidate in target_candidates:
                        proposed_changes.append(ChangeItem(
                            path=f"personality.{candidate}",
                            before=0.0,
                            after=applied_delta,
                            reason=(
                                f"relationship_evidence: "
                                f"growth_level={evaluated.get('growth_level')} "
                                f"signal={evaluated.get('growth_signal')} "
                                f"confidence={evaluated.get('confidence')}"
                            ),
                        ))

            evidence_ids: List[str] = list(dict.fromkeys(
                str(x.get("memory_id") or "")
                for x in (normalized.get("evidence") or [])
                if x.get("memory_id")
            )) or list(raw.get("source_ids") or [])

            evaluator_output = dict(evaluated)
            evaluator_output["proposed_changes"] = proposed_changes
            evaluator_output["evidence_ids"] = evidence_ids
            # process_event 会把这三个键之外的全部字段拷进 evaluator_meta
            evaluator_output["source"] = self.SOURCE_TAG
            evaluator_output["relationship_event_id"] = rel_meta["relationship_event_id"]
            evaluator_output["relationship_event_type"] = rel_meta["relationship_event_type"]

            source_event = {
                "id": rel_meta["relationship_event_id"] or None,
                "event_id": f"evt_{rel_meta['relationship_event_id']}",
                "event_type": raw["event_type"],
                "topic": str(normalized.get("canonical_topic") or normalized.get("topic") or ""),
                "user_id": str(rel_event.get("user_id") or ""),
                "raw_content": str(rel_event.get("content") or ""),
                "source": self.SOURCE_TAG,
            }

            service = self._get_integration_service()
            process_result = service.process_event(source_event, evaluator_output)

            result["pipeline_state"] = process_result.get("pipeline_state", "error")
            result["proposal_id"] = process_result.get("proposal_id")
            result["reasons"].extend(process_result.get("reasons") or [])
            return result

        except Exception as exc:  # noqa: BLE001 — fail-soft，绝不抛给 Runtime
            logger.warning(
                "[RelationshipEvidenceAdapter] process 失败（已隔离）: %s", exc,
            )
            result["pipeline_state"] = "error"
            result["reasons"].append(f"exception: {exc!r}")
            return result

    # ============================================================
    # 内部：懒创建既有 Approval 流入口
    # ============================================================
    def _get_integration_service(self) -> Any:
        if self._integration_service is None:
            # Phase 2.4: 未注入时优先复用 Runtime 权威实例（与 RuntimeCore
            # 共享 growth_history / self_model_store）；仅在其不可用时才懒建
            # 独立实例（旧行为保留，供测试 / 独立调用场景，fail-soft）。
            self._integration_service = self._resolve_runtime_integration_service()
            if self._integration_service is None:
                from src.growth.growth_integration import GrowthIntegrationService

                self._integration_service = GrowthIntegrationService()
        return self._integration_service

    @staticmethod
    def _resolve_runtime_integration_service() -> Optional[Any]:
        """从 RuntimeBridge 取 RuntimeCore 的权威 GrowthIntegrationService。

        任何异常 / 装配缺失 → 返回 None（调用方回退旧逻辑）。
        """
        try:
            from src.runtime.runtime_bridge import get_runtime_bridge

            _core = get_runtime_bridge().get_runtime_core()
            _factory = getattr(_core, "_get_growth_integration_service", None)
            if _factory is None:
                return None
            return _factory()
        except Exception:
            return None
