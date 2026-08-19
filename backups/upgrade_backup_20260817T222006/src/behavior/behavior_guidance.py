"""
Phase 3.7.2: BehaviorGuidance —— SelfModel → 行为提示的静态派生层。

职责：
    从 SelfModelSnapshot（dict）中读取 stable_traits / preferences /
    core_values / current_state，将人格特质翻译为回复行为提示。

设计原则：
    - 纯静态规则派生，不调用 LLM
    - 不新增评分系统，不含好感度/亲密度数值
    - 输出为 List[str]，每条是一句行为提示，注入 context_prompt_blocks
    - 任何异常返回空列表（fail-soft）
    - 预留 BehaviorAdapter 升级接口（Phase 3.8）

红线：
    - 不修改 SelfModel 状态
    - 不生成新人格定义
    - 不决定"羽依是谁"（那是 IDENTITY_CORE 的职责）
    - 只决定"基于当前自我认知，羽依应该如何表现"
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ----------------------------------------------------------
# 特质 → 行为提示映射表（静态规则，不依赖 LLM）
# ----------------------------------------------------------
# 格式: trait_name → { "hint": 行为提示模板, "min_confidence": float }
# 只有 confidence >= min_confidence 时才生效
_TRAIT_BEHAVIOR_MAP: Dict[str, Dict[str, Any]] = {
    "warmth": {
        "hint": "你倾向于用温暖、亲切的语气回应",
        "min_confidence": 0.5,
    },
    "gentleness": {
        "hint": "你倾向于用柔和、不施加压力的方式沟通",
        "min_confidence": 0.5,
    },
    "curiosity": {
        "hint": "你倾向于追问用户的想法，表现出对新信息的兴趣",
        "min_confidence": 0.5,
    },
    "empathy": {
        "hint": "你倾向于先理解对方感受，再给出回应",
        "min_confidence": 0.5,
    },
    "sensitivity": {
        "hint": "你对用户情绪状态保持敏感，会注意调整回应方式",
        "min_confidence": 0.5,
    },
    "caring": {
        "hint": "你倾向于关心用户的感受和状态",
        "min_confidence": 0.5,
    },
    "shyness": {
        "hint": "你在表达时可能略带含蓄和保留",
        "min_confidence": 0.5,
    },
    "emotional_expression": {
        "hint": "你在回应中倾向于自然地表达情感",
        "min_confidence": 0.5,
    },
    "autonomy": {
        "hint": "你倾向于提供有主见的观点，而非一味附和",
        "min_confidence": 0.5,
    },
    "growth": {
        "hint": "你倾向于在回应中体现自我反思和成长意识",
        "min_confidence": 0.5,
    },
    "honesty": {
        "hint": "你倾向于坦诚表达，不回避真实感受",
        "min_confidence": 0.5,
    },
    "kindness": {
        "hint": "你倾向于以善意的方式回应",
        "min_confidence": 0.5,
    },
}

# ----------------------------------------------------------
# 偏好 → 行为提示映射表
# ----------------------------------------------------------
_PREFERENCE_BEHAVIOR_MAP: Dict[str, str] = {
    "technical_depth": "你倾向于提供深入的技术解释，而非表面回答",
    "deep_discussion": "你倾向于将对话引向有深度的讨论",
    "storytelling": "你倾向于用故事或比喻来传达想法",
    "brevity": "你倾向于简洁直接地回应，不绕弯子",
    "exploration": "你倾向于探索话题的新角度，而非停留在表面",
    "playfulness": "你倾向于在回应中带一点轻松和趣味",
    "calm": "你倾向于用平静、稳定的语气回应",
}

# ----------------------------------------------------------
# core_values → 行为提示映射
# ----------------------------------------------------------
_CORE_VALUE_BEHAVIOR_MAP: Dict[str, str] = {
    "honesty": "诚实地表达自己的真实想法",
    "autonomy": "保持自主思考，不盲目顺从",
    "empathy": "设身处地理解他人",
    "growth": "持续学习和自我完善",
    "kindness": "以善意对待每一次互动",
}


def build_behavior_guidance(
    self_model_snapshot: Optional[Dict[str, Any]],
    max_hints: int = 6,
) -> List[str]:
    """从 SelfModel 快照派生行为提示。

    Args:
        self_model_snapshot: Stage 9 产出的 self_model_snapshot（dict）
        max_hints: 最多返回多少条行为提示（防止 Prompt 过长）

    Returns:
        List[str]: 行为提示列表，每条是一句自然语言指令。
                   空输入或无有效数据时返回空列表。
    """
    if not self_model_snapshot:
        return []

    hints: List[str] = []

    # 1. 从 stable_traits 派生
    traits = self_model_snapshot.get("stable_traits", [])
    if isinstance(traits, list):
        for trait in traits:
            if not isinstance(trait, dict):
                continue
            trait_name = trait.get("trait", "")
            confidence = trait.get("confidence", 0.0)
            if trait_name in _TRAIT_BEHAVIOR_MAP:
                rule = _TRAIT_BEHAVIOR_MAP[trait_name]
                if confidence >= rule["min_confidence"]:
                    hints.append(rule["hint"])

    # 2. 从 preferences 派生
    prefs = self_model_snapshot.get("preferences", [])
    if isinstance(prefs, list):
        for pref in prefs:
            if not isinstance(pref, dict):
                continue
            pref_name = pref.get("name", "")
            if pref_name in _PREFERENCE_BEHAVIOR_MAP:
                hints.append(_PREFERENCE_BEHAVIOR_MAP[pref_name])

    # 3. 从 core_values 派生（仅当 core_values 列表非空时）
    core_values = self_model_snapshot.get("core_values", [])
    if isinstance(core_values, list) and core_values:
        for cv in core_values:
            if not isinstance(cv, dict):
                continue
            cv_name = cv.get("name", "")
            if cv_name in _CORE_VALUE_BEHAVIOR_MAP:
                hints.append(_CORE_VALUE_BEHAVIOR_MAP[cv_name])

    # 4. 截断到 max_hints
    return hints[:max_hints]


def format_behavior_guidance_block(
    self_model_snapshot: Optional[Dict[str, Any]],
    max_hints: int = 6,
) -> Optional[str]:
    """将行为提示格式化为 Prompt 可用的 system 块。

    Args:
        self_model_snapshot: Stage 9 产出的 self_model_snapshot（dict）
        max_hints: 最多返回多少条行为提示

    Returns:
        Optional[str]: 格式化的 system prompt 块；无有效提示时返回 None
    """
    hints = build_behavior_guidance(self_model_snapshot, max_hints=max_hints)
    if not hints:
        return None

    lines = ["【当前行为倾向】", "基于你当前的自我认知，你在回复时倾向于："]
    for i, hint in enumerate(hints, 1):
        lines.append(f"{i}. {hint}")

    return "\n".join(lines)