"""
事件语义解析器 v0.7.6

统一的事件类型 → meaning 映射。
EventHistoryMatcher 和 GrowthEngine 共同依赖此模块，避免双重定义漂移。

v0.7.6:
- 增加 IDENTITY_TO_MEANING 映射，identity 优先于 event_type
- 修改、讨论等行为也能产生正确的成长方向
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