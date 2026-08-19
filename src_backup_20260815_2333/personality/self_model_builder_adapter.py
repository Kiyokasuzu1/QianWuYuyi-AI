"""
SelfModelBuilder 适配器 (SelfModelBuilderAdapter) v1.0

Phase 3.8.5 — 双 Builder 架构桥接层

职责：
- 桥接旧 Builder (personality/self_model_builder.py) 和新 Builder (self_model/self_model_builder.py)
- 默认使用旧 Builder (use_new_builder=False)，不破坏现有行为
- 支持 A/B 对比验证
- 支持渐进迁移

设计：
    SelfModelStore
           │
           ▼
    SelfModelBuilderAdapter  ← config 控制 use_new_builder
           │
       ┌───┴───┐
       │       │
       ▼       ▼
    Legacy    New
    Builder   Builder
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SelfModelBuilderAdapter:
    """双 Builder 桥接层，支持 A/B 对比和渐进迁移。

    Args:
        use_new_builder: 是否使用新 Builder。默认 False（使用旧 Builder）。
    """

    def __init__(self, use_new_builder: bool = False):
        self._use_new = use_new_builder
        self._legacy = None  # 延迟初始化
        self._new = None     # 延迟初始化

    # ============================================================
    # 主入口
    # ============================================================

    def build(
        self,
        history: Any = None,
        trait_states: Any = None,
        base_identity: Optional[str] = None,
        capability_limitations: Optional[List[str]] = None,
        growth_history: Any = None,
    ) -> Dict[str, Any]:
        """
        构建 SelfModel。默认使用旧 Builder，可通过配置切换到新 Builder。

        Args:
            history: PersonalityGrowthHistory 实例
            trait_states: Dict[str, TraitState]
            base_identity: 基础身份描述
            capability_limitations: 能力限制列表
            growth_history: 兼容旧参数名（等同于 history）

        Returns:
            SelfModel dict（旧格式，兼容现有调用方）
        """
        if self._use_new:
            return self._build_with_new(
                history=history or growth_history,
                trait_states=trait_states,
                base_identity=base_identity,
                capability_limitations=capability_limitations,
            )
        else:
            return self._build_with_legacy(
                history=history,
                trait_states=trait_states,
                base_identity=base_identity,
                capability_limitations=capability_limitations,
                growth_history=growth_history,
            )

    def build_both(
        self,
        history: Any = None,
        trait_states: Any = None,
        base_identity: Optional[str] = None,
        capability_limitations: Optional[List[str]] = None,
        growth_history: Any = None,
    ) -> Dict[str, Any]:
        """
        A/B 对比模式：同时用新旧 Builder 构建，返回两者的结果。

        Returns:
            {"legacy": Dict, "new": Dict}
        """
        legacy_result = self._build_with_legacy(
            history=history, trait_states=trait_states,
            base_identity=base_identity, capability_limitations=capability_limitations,
            growth_history=growth_history,
        )
        new_result = self._build_with_new(
            history=history or growth_history, trait_states=trait_states,
            base_identity=base_identity, capability_limitations=capability_limitations,
        )
        return {"legacy": legacy_result, "new": new_result}

    # ============================================================
    # 旧 Builder 路径
    # ============================================================

    def _build_with_legacy(
        self,
        history: Any = None,
        trait_states: Any = None,
        base_identity: Optional[str] = None,
        capability_limitations: Optional[List[str]] = None,
        growth_history: Any = None,
    ) -> Dict[str, Any]:
        """使用旧 Builder 构建"""
        if self._legacy is None:
            from src.personality.self_model_builder import SelfModelBuilder
            self._legacy = SelfModelBuilder()

        return self._legacy.build(
            history=history,
            trait_states=trait_states,
            base_identity=base_identity,
            capability_limitations=capability_limitations,
            growth_history=growth_history,
        )

    # ============================================================
    # 新 Builder 路径
    # ============================================================

    def _build_with_new(
        self,
        history: Any = None,
        trait_states: Any = None,
        base_identity: Optional[str] = None,
        capability_limitations: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        使用新 Builder 构建，并转换为旧格式兼容输出。

        新 Builder 输入模型不同，需要适配：
        - 旧: PersonalityGrowthHistory, Dict[str, TraitState], base_identity, capability_limitations
        - 新: IdentityAnchorManager, PersonalityState, EvolutionRecord list
        """
        if self._new is None:
            from src.self_model.self_model_builder import SelfModelBuilder as NewBuilder
            self._new = NewBuilder()

        try:
            # 转换输入：旧格式 → 新格式
            # 新 Builder 的三个输入均可为 None，None 时返回空 view
            snapshot = self._new.build(
                identity_anchor_manager=None,  # 暂无 IdentityAnchorManager
                personality_state=self._convert_trait_states(trait_states),
                evolution_records=self._convert_history(history),
            )

            # 转换输出：新格式 → 旧格式（兼容现有调用方）
            return self._new_snapshot_to_legacy_dict(snapshot, base_identity, capability_limitations)

        except Exception as exc:
            logger.warning(
                "SelfModelBuilderAdapter: 新 Builder 构建失败，回退到旧 Builder: %s", exc,
            )
            return self._build_with_legacy(
                history=history, trait_states=trait_states,
                base_identity=base_identity, capability_limitations=capability_limitations,
            )

    # ============================================================
    # 输入转换
    # ============================================================

    @staticmethod
    def _convert_trait_states(trait_states: Any) -> Optional[Dict[str, float]]:
        """将 Dict[str, TraitState] 转换为 Dict[str, float]"""
        if trait_states is None:
            return None
        result = {}
        for key, val in trait_states.items():
            if isinstance(val, dict):
                result[key] = float(val.get("current_value", 0.5))
            elif hasattr(val, "current_value"):
                result[key] = float(getattr(val, "current_value", 0.5))
            elif isinstance(val, (int, float)):
                result[key] = float(val)
        return result if result else None

    @staticmethod
    def _convert_history(history: Any) -> List[Dict[str, Any]]:
        """将 PersonalityGrowthHistory 转换为 EvolutionRecord list"""
        if history is None:
            return []
        try:
            records = history.all() if hasattr(history, "all") else []
            result = []
            for r in records:
                if not isinstance(r, dict):
                    continue
                changes = r.get("changes", {})
                before = {}
                after = {}
                for dim, change in changes.items():
                    if isinstance(change, dict):
                        before[dim] = change.get("before", 0.5)
                        after[dim] = change.get("after", 0.5)
                result.append({
                    "record_id": r.get("record_id", ""),
                    "timestamp": r.get("timestamp", ""),
                    "change_type": "trait_delta",
                    "before": before,
                    "after": after,
                    "reasons": [r.get("meaning", "")],
                    "confidence": r.get("confidence", 0.5),
                })
            return result
        except Exception:
            return []

    # ============================================================
    # 输出转换：新格式 → 旧格式
    # ============================================================

    @staticmethod
    def _new_snapshot_to_legacy_dict(
        snapshot: Any,
        base_identity: Optional[str],
        capability_limitations: Optional[List[str]],
    ) -> Dict[str, Any]:
        """将新 SelfModelSnapshot 转换为旧 dict 格式"""
        from datetime import datetime

        model = snapshot.model if hasattr(snapshot, "model") else {}

        # 从新格式提取数据
        identity_view = model.get("identity_view", {})
        personality_view = model.get("personality_view", {})
        development_view = model.get("development_view", {})
        contradiction_view = model.get("contradiction_view", {})
        capability_view = model.get("capability_view", {})

        # 构造旧格式兼容字段
        identity_name = base_identity or "浅雾羽依"
        stable_traits = list(personality_view.get("stable_traits", {}).keys())
        developing_traits = list(personality_view.get("evolving_traits", {}).keys())

        # 从 development_view 提取 growth_narratives
        growth_narratives = []
        for entry in development_view.get("evolution_history", []):
            if entry.get("reason_summary"):
                growth_narratives.append({
                    "record_id": entry.get("record_id", ""),
                    "dimension": "",
                    "event": "",
                    "narrative": "; ".join(entry.get("reason_summary", [])),
                    "meaning": "; ".join(entry.get("reason_summary", [])),
                    "timestamp": entry.get("timestamp", ""),
                })

        # 从 capability_view 提取 limitations
        known_limitations = capability_limitations or []
        known_limitations.extend(capability_view.get("limitations", []))

        identity_summary = identity_name
        if stable_traits:
            identity_summary += "。" + "".join(stable_traits[:3])

        return {
            "identity_id": identity_view.get("identity_id", ""),
            "identity_name": identity_name,
            "self_description": {
                "text": identity_summary,
                "sources": ["growth_history"],
                "confidence": 0.8,
            },
            "growth_narratives": growth_narratives,
            "current_traits": personality_view.get("stable_traits", {}),
            "personality_tensions": [
                {
                    "trait_a": t.get("trait_a", ""),
                    "trait_b": t.get("trait_b", ""),
                    "trait_values": {t.get("trait_a", ""): t.get("value_a", 0), t.get("trait_b", ""): t.get("value_b", 0)},
                    "description": t.get("description", ""),
                    "intensity": 0.5,
                }
                for t in contradiction_view.get("detected_tensions", [])
            ],
            "self_understanding": {
                "experience_awareness": 0.3,
                "trait_awareness": 0.2,
                "identity_continuity": 0.4,
                "overall": 0.3,
            },
            "last_updated": snapshot.generated_at if hasattr(snapshot, "generated_at") else datetime.now().isoformat(),
            "stable_traits": stable_traits,
            "developing_traits": developing_traits,
            "known_limitations": list(set(known_limitations)),
            "identity_summary": identity_summary,
        }