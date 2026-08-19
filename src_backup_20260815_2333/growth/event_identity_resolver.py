"""
事件身份解析器 v0.7.6

为事件生成稳定的 event_identity，用于跨会话匹配。
解析后会将结果写回 event 字典，确保身份唯一。

v0.7.6:
- 由 behavior_resolver 决定行为类型（create / modify / discuss）
- 增强文本收集：除了 evidence，还从 topic / canonical_topic / event 字段提取信息
- 增加 _identity_source 校验，防止缓存污染
"""

from src.growth.behavior_resolver import resolve_behavior


def resolve_event_identity(event: dict) -> str:
    # 只有 resolver 自己写的标记才信任
    if (
        event.get("_identity_resolved")
        and event.get("_identity_source") == "resolver"
    ):
        return event["event_identity"]

    behavior = resolve_behavior(event)

    # 收集所有可用文本用于身份推断
    text = ""
    for e in event.get("evidence", []):
        text += e.get("text", "") + " "

    # 如果 evidence 为空，退而求其次使用主题和事件名称
    if not text.strip():
        text = " ".join([
            event.get("topic", ""),
            event.get("canonical_topic", ""),
            event.get("event", ""),
            event.get("name", "")
        ])

    identity = "unknown"

    if behavior == "create":
        if any(x in text for x in ["AI角色", "AI人物", "虚拟角色", "虚拟人物"]):
            identity = "ai_character_creation"
        elif any(x in text for x in ["图片", "绘画", "画图"]):
            identity = "ai_image_creation"

    elif behavior == "modify":
        if any(x in text for x in ["AI角色", "AI人物", "虚拟角色", "虚拟人物"]):
            identity = "ai_character_modification"

    elif behavior == "discuss":
        if any(x in text for x in ["AI角色", "AI人物", "虚拟角色", "虚拟人物"]):
            identity = "character_discussion"

    if identity == "unknown":
        identity = event.get("canonical_topic") or event.get("topic") or "unknown"

    event["event_identity"] = identity
    event["_identity_resolved"] = True
    event["_identity_source"] = "resolver"
    return identity