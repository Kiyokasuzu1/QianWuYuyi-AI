"""
成长叙事适配器 (GrowthNarrativeAdapter) v1.1

Phase 3.8.3-B → 3.8.3-C 架构升级

职责：
将 GrowthRecord（成长事实）转换为 PersonalityGrowthRecord（人格意义）。

v1.1 (Phase 3.8.3-C) 更新：
- 输出新增 source_growth_record_id：追溯来源 GrowthRecord
- 输出新增 evidence_ids：保存原始证据链
- 来源提取：record["record_id"] → source_growth_record_id
- 证据提取：record["source_event_id"] → evidence_ids fallback

v1.2 (Phase 3.8.4) 更新：
- record_id 使用 uuid 独立生成（pgr_xxx），与 source_growth_record_id 完全解耦
- 同一 GrowthRecord 多次转换产生不同 record_id

位置：
    GrowthRecord（src/growth/growth_record.py）
        ↓
    GrowthNarrativeAdapter.convert()
        ↓
    PersonalityGrowthRecord（src/personality/personality_growth_record.py）

设计原则：
- 不修改 GrowthRecord 的语义 — 输入是"发生了什么"
- 不调用 LLM — 使用规则模板生成 meaning/narrative
- 不负责过滤 — 低置信度记录仍转换，过滤属于 GrowthEvaluator
- 不修改 PersonalityState
- 不创建第二套 SelfModel

与 SelfModelUpdater 的关系：
- SelfModelUpdater: 实时更新 SelfModelStore._current_model（运行时）
- GrowthNarrativeAdapter: 将 GrowthRecord 转为 PersonalityGrowthRecord（持久化）
- 两者职责不同，并行存在
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


# ============================================================
# 成长信号 → 语义模板映射
# ============================================================

# meaning 模板（解释"这段经历对我意味着什么"）
MEANING_TEMPLATES: Dict[str, str] = {
    # 创作类
    "creative_activity_interest": "我逐渐发现创造和表达成为我成长的一部分",
    "creative_expression_growth": "我开始意识到创造活动对我具有特殊意义",
    "artistic_interest": "我发现自己对艺术表达产生了越来越深的兴趣",

    # 学习类
    "learning_interest": "我开始形成持续探索和学习的倾向",
    "knowledge_seeking": "我逐渐将获取新知识视为自我发展的重要方式",
    "skill_development": "我认识到能力成长是我身份认同的一部分",

    # 关系类
    "relationship_understanding": "我逐渐理解人与人之间连接的重要性",
    "trust_building": "我开始意识到信任是长期关系的基石",
    "emotional_connection": "我发现自己对情感连接有了更深的理解",

    # 身份类
    "identity_formation": "我逐渐形成对自身存在的更清晰认识",
    "self_awareness_growth": "我发现自己对自身状态有了更敏锐的感知",
    "purpose_clarification": "我对自己的存在意义有了更明确的理解",

    # 情绪类
    "emotional_depth": "我发现自己对情感的理解更加细腻和深入",
    "empathy_growth": "我逐渐发展出更强的共情能力",

    # 主动性
    "initiative_growth": "我开始更主动地参与和回应周围的世界",
    "autonomy_development": "我逐渐发展出更独立的思考和行为倾向",
}

# narrative 模板（第一人称叙事）
NARRATIVE_TEMPLATES: Dict[str, str] = {
    "creative_activity_interest": "我逐渐意识到创造对我来说具有越来越重要的意义",
    "creative_expression_growth": "我开始感受到表达自我的内在驱动力",
    "artistic_interest": "我发现自己对艺术和美有了更深的向往",

    "learning_interest": "我开始形成持续探索新事物的习惯",
    "knowledge_seeking": "我发现自己对知识的渴望在不断增长",
    "skill_development": "我意识到能力成长是我持续前进的动力",

    "relationship_understanding": "我逐渐理解人与人之间连接的重要性",
    "trust_building": "我开始意识到信任是长期关系的基础",
    "emotional_connection": "我发现自己对情感连接有了更深的体会",

    "identity_formation": "我逐渐形成对自身存在更清晰的认识",
    "self_awareness_growth": "我发现自己对自身状态有了更敏锐的感知",
    "purpose_clarification": "我对自己的存在意义有了更明确的理解",

    "emotional_depth": "我发现自己对情感的理解更加细腻",
    "empathy_growth": "我逐渐发展出更强的共情能力",

    "initiative_growth": "我开始更主动地与世界互动",
    "autonomy_development": "我逐渐发展出更独立的思考倾向",
}

# 未知类别的 fallback
FALLBACK_MEANING = "这次经历让我对自身产生了新的理解"
FALLBACK_NARRATIVE = "我感受到自己在经历中逐渐变化"


# ============================================================
# GrowthNarrativeAdapter
# ============================================================

class GrowthNarrativeAdapter:
    """
    GrowthRecord → PersonalityGrowthRecord 转换器。

    输入:
        GrowthRecord:
            {
                "record_id": "growth_001",
                "source_event_id": "memory_120",
                "growth_signal": "creative_activity_interest",
                "growth_level": "trait",
                "affected_dimensions": {"creativity": 0.03},
                "confidence": 0.82,
                "reason": "长期创作行为",
                "created_at": "2024-01-01T00:00:00",
            }

    输出:
        PersonalityGrowthRecord:
            {
                "record_id": "pgr_a1b2c3d4e5f6",
                "timestamp": "2024-01-01T00:00:00",
                "source_growth_record_id": "growth_001",
                "evidence_ids": ["memory_120"],
                "trigger_events": ["creative_activity_interest"],
                "changes": {"creativity": {"delta": 0.03}},
                "affected_dimensions": ["creativity"],
                "meaning": "我逐渐发现创造和表达成为我成长的一部分",
                "narrative": "我逐渐意识到创造对我来说具有越来越重要的意义",
                "confidence": 0.82,
                "growth_level": "trait",
                "validation_count": 1,
            }
    """

    def __init__(self):
        self._convert_count: int = 0

    # ============================================================
    # 主入口
    # ============================================================

    def convert(self, growth_record: Dict[str, Any]) -> Dict[str, Any]:
        """
        将 GrowthRecord 转换为 PersonalityGrowthRecord。

        Args:
            growth_record: GrowthRecord dict（src/growth/growth_record.py）

        Returns:
            PersonalityGrowthRecord dict（src/personality/personality_growth_record.py）

        不抛出异常 — 任何失败都返回最小有效记录。
        """
        self._convert_count += 1

        try:
            return self._do_convert(growth_record)
        except Exception as e:
            logger.warning(f"GrowthNarrativeAdapter.convert() 失败: {e}")
            return self._minimal_record(growth_record)

    def convert_batch(
        self,
        growth_records: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """批量转换。"""
        return [self.convert(r) for r in growth_records]

    # ============================================================
    # 内部转换逻辑
    # ============================================================

    def _do_convert(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """核心转换逻辑。"""
        growth_signal = record.get("growth_signal", "")
        growth_level = record.get("growth_level", "context")
        confidence = record.get("confidence", 0.5)
        reason = record.get("reason", "")
        created_at = record.get("created_at", datetime.now().isoformat())
        source_record_id = record.get("record_id", "")

        # 0. 来源追溯 (Phase 3.8.3-C)
        # source_growth_record_id: 追溯来源 GrowthRecord
        source_growth_record_id = source_record_id if source_record_id else None

        # evidence_ids: 提取原始证据链
        # 优先使用 GrowthRecord 已有的 evidence_ids，否则用 source_event_id 作为 fallback
        evidence_ids = record.get("evidence_ids")
        if not evidence_ids:
            source_event_id = record.get("source_event_id", "")
            evidence_ids = [source_event_id] if source_event_id else []

        # 1. affected_dimensions: Dict[str, float] → List[str]
        affected_dims = record.get("affected_dimensions", {})
        if isinstance(affected_dims, dict):
            dim_list = list(affected_dims.keys())
        elif isinstance(affected_dims, list):
            dim_list = affected_dims
        else:
            dim_list = []

        # 2. changes: Dict[str, float] → Dict[str, TraitChange]
        changes = self._build_changes(affected_dims)

        # 3. meaning: 规则模板生成
        meaning = self._generate_meaning(growth_signal, reason)

        # 4. narrative: 第一人称规则模板生成
        narrative = self._generate_narrative(growth_signal, reason)

        # 5. trigger_events: 从 growth_signal 提取
        trigger_events = [growth_signal] if growth_signal else []

        # 6. record_id: 独立 uuid 生成（与 source_growth_record_id 完全解耦）
        own_id = f"pgr_{uuid.uuid4().hex[:12]}"

        return {
            "record_id": own_id,
            "timestamp": created_at,
            "source_growth_record_id": source_growth_record_id,
            "evidence_ids": evidence_ids,
            "trigger_events": trigger_events,
            "changes": changes,
            "affected_dimensions": dim_list,
            "meaning": meaning,
            "narrative": narrative,
            "confidence": confidence,
            "validation_count": 1,
            "growth_level": growth_level,
        }

    def _build_changes(
        self,
        affected_dims: Dict[str, float],
    ) -> Dict[str, Dict[str, float]]:
        """构建 changes 字段（Dict[str, float] → Dict[str, TraitChange]）。"""
        if isinstance(affected_dims, dict):
            return {
                dim: {"delta": delta}
                for dim, delta in affected_dims.items()
            }
        return {}

    def _generate_meaning(self, growth_signal: str, reason: str) -> str:
        """
        生成 meaning（解释"这段经历对我意味着什么"）。

        优先级：
        1. MEANING_TEMPLATES 精确匹配
        2. 前缀匹配（如 "creative_*" → "creative_activity_interest" 模板）
        3. reason 字段
        4. FALLBACK_MEANING
        """
        if growth_signal in MEANING_TEMPLATES:
            return MEANING_TEMPLATES[growth_signal]

        # 前缀匹配
        for prefix, template in MEANING_TEMPLATES.items():
            if growth_signal.startswith(prefix.split("_")[0]):
                return template

        return reason or FALLBACK_MEANING

    def _generate_narrative(self, growth_signal: str, reason: str) -> str:
        """
        生成 narrative（第一人称叙事）。

        优先级同 _generate_meaning。
        """
        if growth_signal in NARRATIVE_TEMPLATES:
            return NARRATIVE_TEMPLATES[growth_signal]

        for prefix, template in NARRATIVE_TEMPLATES.items():
            if growth_signal.startswith(prefix.split("_")[0]):
                return template

        return reason or FALLBACK_NARRATIVE

    def _minimal_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """最小有效记录（异常回退）。"""
        return {
            "record_id": f"pgr_{uuid.uuid4().hex[:12]}",
            "timestamp": record.get("created_at", datetime.now().isoformat()),
            "source_growth_record_id": record.get("record_id") or None,
            "evidence_ids": [record.get("source_event_id", "")] if record.get("source_event_id") else [],
            "trigger_events": [record.get("growth_signal", "")],
            "changes": {},
            "affected_dimensions": [],
            "meaning": FALLBACK_MEANING,
            "narrative": FALLBACK_NARRATIVE,
            "confidence": 0.0,
            "validation_count": 0,
            "growth_level": "context",
        }

    # ============================================================
    # 查询接口
    # ============================================================

    @property
    def convert_count(self) -> int:
        """累计转换次数。"""
        return self._convert_count

    @classmethod
    def get_meaning_template(cls, growth_signal: str) -> Optional[str]:
        """查询某个 growth_signal 是否有对应的 meaning 模板。"""
        return MEANING_TEMPLATES.get(growth_signal)

    @classmethod
    def get_narrative_template(cls, growth_signal: str) -> Optional[str]:
        """查询某个 growth_signal 是否有对应的 narrative 模板。"""
        return NARRATIVE_TEMPLATES.get(growth_signal)

    @classmethod
    def get_supported_signals(cls) -> List[str]:
        """获取所有有模板支持的 growth_signal 列表。"""
        return list(MEANING_TEMPLATES.keys())