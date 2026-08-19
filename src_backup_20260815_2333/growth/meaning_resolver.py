"""
事件语义解析器 v0.7.6 → v3.8.1

统一的事件类型 → meaning 映射。
EventHistoryMatcher 和 GrowthEngine 共同依赖此模块，避免双重定义漂移。

v0.7.6:
- 增加 IDENTITY_TO_MEANING 映射，identity 优先于 event_type
- 修改、讨论等行为也能产生正确的成长方向

v3.8.1 (Phase 3.8.1):
- 新增 deep_resolve_meaning()：使用 LLM 做语义理解，区分同类事件的不同含义
- 保留 resolve_meaning() 作为规则 fallback
- 新增 _validate_meaning_against_identity()：IDENTITY_CORE 约束检查
"""

# 基础的事件类型 → meaning 映射
MEANING_ALIAS = {
    "birth": "birth",
    "identity": "identity_creation",
    "relationship": "relationship_start",
    "commitment": "promise",
    "growth": "growth_support",
    "memory": "companionship",
    "milestone": "birth",
    "creation": "creation",
    "emotional_expression": "emotional_expression",
}

# event_identity → meaning 映射（优先级高于 MEANING_ALIAS）
IDENTITY_TO_MEANING = {
    "ai_character_creation": "creation",
    "ai_character_modification": "growth_support",
    "character_discussion": "companionship",
    "ai_image_creation": "creation",
}


def resolve_meaning(event: dict) -> str:
    """
    从事件字典中解析 meaning。
    优先从 event_identity 推导，再回退到已有 meaning 字段，
    最后根据 event_type / category_id / category 推断。
    """
    # 1. 优先从 event_identity 推导
    identity = event.get("event_identity", "")
    if identity in IDENTITY_TO_MEANING:
        return IDENTITY_TO_MEANING[identity]

    # 2. 回退到事件已有的 meaning
    if event.get("meaning"):
        return event["meaning"]

    # 3. 最后从 event_type / category 推断
    key = (
        event.get("category_id")
        or event.get("category")
        or event.get("event_type")
        or ""
    )

    return MEANING_ALIAS.get(key, "")


# ============================================================
# Phase 3.8.1：深度语义理解
# ============================================================

import json as _json
import logging
from typing import Any, Dict, List, Optional

from src.growth.experience_meaning import (
    ExperienceMeaning,
    GrowthDirection,
    ValueAlignment,
    RiskFlag,
)
from src.personality.identity_core import IDENTITY_CORE

logger = logging.getLogger(__name__)


# ============================================================
# IDENTITY_CORE 约束检查
# ============================================================

def _validate_meaning_against_identity(
    meaning: ExperienceMeaning,
    identity_core: Optional[Dict] = None,
) -> ExperienceMeaning:
    """检查 ExperienceMeaning 是否违反 IDENTITY_CORE 的不可变原则。

    这是一个纯规则检查，不调用 LLM。
    检查每个 suggested_growth_direction 是否与 immutable_principles 冲突。

    Args:
        meaning: 已生成的 ExperienceMeaning
        identity_core: IDENTITY_CORE 字典（默认使用全局常量）

    Returns:
        更新后的 ExperienceMeaning（可能带有风险标记）
    """
    if identity_core is None:
        identity_core = IDENTITY_CORE

    principles = identity_core.get("immutable_principles", {})

    for direction in meaning.suggested_growth_directions:
        dim = direction.dimension

        # 检查 growth 原则：人格变化必须经过验证
        if dim in ("identity_strength", "self_awareness"):
            # 这些维度的变化需要严格审查
            if direction.magnitude > 0.15:
                meaning.risk_flags.append(RiskFlag(
                    flag_type="identity_conflict",
                    severity="warning",
                    description=(
                        f"成长方向 '{dim}' 建议幅度 {direction.magnitude:.2f} 较大，"
                        f"需确认不与 '人格变化必须经过验证' 原则冲突"
                    ),
                ))

        # 检查 relationship 原则：不产生依赖
        relationship_principles = principles.get("relationship", [])
        if dim in ("attachment", "dependence") and direction.direction == "increase":
            meaning.risk_flags.append(RiskFlag(
                flag_type="identity_conflict",
                severity="critical",
                description=(
                    f"成长方向 '{dim}' 增加可能违反不可变原则："
                    f"{'；'.join(relationship_principles[:2])}"
                ),
            ))

        # 检查 truthfulness 原则：不伪造
        truthfulness_principles = principles.get("truthfulness", [])
        if dim in ("simulated_emotion", "pretended_experience"):
            meaning.risk_flags.append(RiskFlag(
                flag_type="identity_conflict",
                severity="critical",
                description=(
                    f"成长方向 '{dim}' 可能违反不可变原则："
                    f"{'；'.join(truthfulness_principles[:2])}"
                ),
            ))

    # 检查整体：是否有 critical 风险
    if meaning.has_critical_risks():
        # 降低置信度
        meaning.confidence = min(meaning.confidence, 0.3)

    return meaning


# ============================================================
# LLM Prompt 构建
# ============================================================

_DEEP_MEANING_SYSTEM_PROMPT = """你是浅雾羽依的经历意义解析器。你的职责是从事件中理解深层含义，而不是简单分类。

## 核心原则
1. 只根据提供的 evidence 判断，不创造不存在的经历
2. 区分表层事件（发生了什么）和深层意义（这代表什么趋势/价值观）
3. 你不直接决定人格变化，只提供"理解"和"建议"
4. 如果不确定，降低 confidence，不要编造

## 输出格式（严格 JSON，不要 markdown 代码块标记）
{
  "surface_meaning": "一句话描述发生了什么",
  "deeper_significance": "这代表什么长期趋势或价值观",
  "suggested_growth_directions": [
    {"dimension":"维度名","direction":"increase","magnitude":0.05,"reason":"简短原因","evidence":["证据"]}
  ],
  "value_alignment": [
    {"value":"价值观名","alignment_score":0.8,"relevance":"简短说明"}
  ],
  "confidence": 0.7,
  "risk_flags": []
}

## 可用维度: curiosity, creativity, self_expression, initiative, relationship_orientation, long_term_focus, empathy, warmth, trust, self_confidence, self_awareness, identity_strength

## 注意
- suggested_growth_directions 最多 2 个
- magnitude 范围 0.03~0.12
- risk_flags 只在有风险时填写"""


def _build_deep_meaning_prompt(
    event: Dict[str, Any],
    identity_core: Optional[Dict] = None,
    current_growth_state: Optional[Dict] = None,
    related_memories: Optional[List[Dict]] = None,
    self_model: Optional[Dict] = None,
) -> str:
    """构建 deep_resolve_meaning 的 LLM prompt。"""
    if identity_core is None:
        identity_core = IDENTITY_CORE

    parts = []

    # 事件信息
    parts.append("## 事件信息")
    parts.append(f"- 事件类型: {event.get('event_type', 'unknown')}")
    parts.append(f"- 事件身份: {event.get('event_identity', '')}")
    parts.append(f"- 话题: {event.get('canonical_topic', event.get('topic', ''))}")
    parts.append(f"- 内容摘要: {event.get('summary', event.get('content', ''))}")
    parts.append(f"- 重要性: {event.get('importance', 0.5)}")
    if event.get("context"):
        parts.append(f"- 上下文: {event.get('context', '')}")

    # 羽依的核心身份（用于判断意义是否与身份一致）
    parts.append("")
    parts.append("## 羽依的核心身份（不可变）")
    parts.append(f"- 本质: {identity_core.get('essence', '')[:200]}")
    parts.append(f"- 核心价值: {', '.join(identity_core.get('core_values', [])[:5])}")

    # 当前成长状态
    if current_growth_state:
        parts.append("")
        parts.append("## 当前成长状态")
        # 只取前 5 个指标
        metrics = {
            k: v for k, v in current_growth_state.items()
            if isinstance(v, (int, float))
        }
        top_metrics = sorted(metrics.items(), key=lambda x: abs(x[1]), reverse=True)[:5]
        for k, v in top_metrics:
            parts.append(f"- {k}: {v:.3f}")

    # 相关记忆
    if related_memories:
        parts.append("")
        parts.append("## 相关记忆（最多 3 条）")
        for mem in related_memories[:3]:
            content = mem.get("summary", mem.get("content", ""))[:150]
            parts.append(f"- {content}")

    # 自我模型
    if self_model:
        parts.append("")
        parts.append("## 当前自我模型")
        stable_traits = self_model.get("stable_traits", [])
        if stable_traits:
            trait_strs = [
                f"{t.get('trait', '')}={t.get('value', 0)}"
                for t in stable_traits[:5]
            ]
            parts.append(f"- 稳定特质: {', '.join(trait_strs)}")

    parts.append("")
    parts.append("请分析以上事件，输出 JSON。")

    return "\n".join(parts)


# ============================================================
# 深度语义解析
# ============================================================

def deep_resolve_meaning(
    event: Dict[str, Any],
    identity_core: Optional[Dict] = None,
    current_growth_state: Optional[Dict] = None,
    related_memories: Optional[List[Dict]] = None,
    self_model: Optional[Dict] = None,
) -> ExperienceMeaning:
    """Phase 3.8.1：使用 LLM 深度理解事件含义。

    区分同类事件的不同深层意义。
    例如："用户开发AI伴侣" 和 "用户画了一幅画" 虽然都是 creation，
    但前者的深层意义是 relationship_design/human_understanding，
    后者是 creative_expression/artistic_exploration。

    Args:
        event: 事件字典（来自 EventExtractor）
        identity_core: IDENTITY_CORE 字典（用于约束检查）
        current_growth_state: 当前成长状态
        related_memories: 相关记忆列表
        self_model: 当前自我模型

    Returns:
        ExperienceMeaning: 经历意义理解结果

    Fallback:
        如果 LLM 调用失败，自动回退到 resolve_meaning() 规则映射。
    """
    import time
    from datetime import timezone

    if identity_core is None:
        identity_core = IDENTITY_CORE

    event_id = event.get("event_id", "")

    # 尝试 LLM 深度理解
    try:
        from src.response.llm import LLMClient

        prompt = _build_deep_meaning_prompt(
            event=event,
            identity_core=identity_core,
            current_growth_state=current_growth_state,
            related_memories=related_memories,
            self_model=self_model,
        )

        llm = LLMClient()
        response = llm.generate(
            messages=[
                {"role": "system", "content": _DEEP_MEANING_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
        )

        # 如果 LLM 返回空内容，重试一次
        if not response or response.startswith("走神"):
            logger.warning("deep_resolve_meaning: LLM 返回空内容，重试一次")
            time.sleep(1.0)
            response = llm.generate(
                messages=[
                    {"role": "system", "content": _DEEP_MEANING_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ]
            )

        # 解析 JSON 响应
        data = _parse_llm_json(response)

        # 构建 ExperienceMeaning
        meaning = ExperienceMeaning(
            surface_meaning=data.get("surface_meaning", ""),
            deeper_significance=data.get("deeper_significance", ""),
            confidence=min(max(data.get("confidence", 0.5), 0.0), 1.0),
            evidence_chain=data.get("evidence_chain", [event_id]),
            source_event_id=event_id,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            fallback_used=False,
        )

        # 解析成长方向
        for gd in data.get("suggested_growth_directions", []):
            if not isinstance(gd, dict):
                continue
            # 限制幅度
            magnitude = min(max(float(gd.get("magnitude", 0.05)), 0.0), 0.12)
            meaning.suggested_growth_directions.append(GrowthDirection(
                dimension=gd.get("dimension", ""),
                direction=gd.get("direction", "increase"),
                magnitude=round(magnitude, 3),
                reason=gd.get("reason", ""),
                evidence=gd.get("evidence", []),
            ))

        # 解析价值观对齐
        for va in data.get("value_alignment", []):
            if not isinstance(va, dict):
                continue
            meaning.value_alignment.append(ValueAlignment(
                value=va.get("value", ""),
                alignment_score=min(max(float(va.get("alignment_score", 0.5)), 0.0), 1.0),
                relevance=va.get("relevance", ""),
            ))

        # 解析风险标记
        for rf in data.get("risk_flags", []):
            if not isinstance(rf, dict):
                continue
            meaning.risk_flags.append(RiskFlag(
                flag_type=rf.get("flag_type", "unknown"),
                severity=rf.get("severity", "warning"),
                description=rf.get("description", ""),
            ))

        # IDENTITY_CORE 约束检查
        meaning = _validate_meaning_against_identity(meaning, identity_core)

        return meaning

    except Exception as e:
        # LLM 失败 → fallback 到规则映射
        logger.warning(
            f"deep_resolve_meaning LLM 调用失败，回退到规则映射: {e}"
        )

        rule_meaning = resolve_meaning(event)
        if rule_meaning:
            return ExperienceMeaning.from_rule_fallback(rule_meaning, event_id)
        else:
            return ExperienceMeaning.empty(event_id)


def _parse_llm_json(response: str) -> Dict[str, Any]:
    """从 LLM 响应中解析 JSON。

    处理常见的格式问题：markdown 代码块、尾部逗号、截断 JSON 等。
    """
    if not response or not isinstance(response, str):
        return {}

    text = response.strip()

    # 移除 markdown 代码块标记
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    # 尝试找到 JSON 对象的起止位置
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]

    # 尝试 1：直接解析
    try:
        return _json.loads(text)
    except _json.JSONDecodeError:
        pass

    # 尝试 2：修复尾部逗号
    try:
        import re
        fixed = re.sub(r',\s*([}\]])', r'\1', text)
        return _json.loads(fixed)
    except _json.JSONDecodeError:
        pass

    # 尝试 3：截断修复 —— 补全未闭合的括号和引号
    try:
        repaired = _repair_truncated_json(text)
        if repaired:
            return _json.loads(repaired)
    except _json.JSONDecodeError:
        pass

    # 尝试 4：提取已知字段（尽力而为）
    logger.warning(f"无法解析 LLM JSON 响应: {response[:200]}")
    return _extract_fields_fallback(text)


def _repair_truncated_json(text: str) -> str:
    """尝试修复被截断的 JSON。

    当 LLM 的 max_tokens 不足时，JSON 可能在中间被截断。
    尝试补全未闭合的括号、引号、数组等。
    """
    # 统计未闭合的括号
    brace_count = text.count("{") - text.count("}")
    bracket_count = text.count("[") - text.count("]")

    # 移除最后一个不完整的字段（如果最后是逗号或未闭合的字符串）
    # 找到最后一个完整的逗号位置
    last_comma = text.rfind(',"')
    if last_comma > 0:
        # 从最后一个完整字段处截断
        text = text[:last_comma]

    # 移除尾部逗号
    import re
    text = re.sub(r',\s*$', '', text)

    # 补全未闭合的括号
    text += "]" * bracket_count
    text += "}" * brace_count

    return text


def _extract_fields_fallback(text: str) -> Dict[str, Any]:
    """从无法解析的 JSON 文本中提取已知字段。

    当 _repair_truncated_json 也失败时，使用正则提取 surface_meaning 和 confidence。
    """
    import re

    result = {}

    # 提取 surface_meaning
    m = re.search(r'"surface_meaning"\s*:\s*"([^"]*)"', text)
    if m:
        result["surface_meaning"] = m.group(1)

    # 提取 deeper_significance
    m = re.search(r'"deeper_significance"\s*:\s*"([^"]*)"', text)
    if m:
        result["deeper_significance"] = m.group(1)

    # 提取 confidence
    m = re.search(r'"confidence"\s*:\s*([\d.]+)', text)
    if m:
        result["confidence"] = float(m.group(1))

    # 提取 suggested_growth_directions 中的 dimension
    dims = re.findall(r'"dimension"\s*:\s*"([^"]+)"', text)
    if dims:
        result["suggested_growth_directions"] = [
            {"dimension": d, "direction": "increase", "magnitude": 0.05,
             "reason": "（从截断响应中恢复）", "evidence": []}
            for d in dims[:2]
        ]

    return result