"""
行为解析器 v0.7.6

从事件的 evidence 文本中识别用户的核心行为类型：
- create: 创造、制作、设计
- modify: 修改、调整、优化
- discuss: 讨论、分析、聊
- unknown: 无法识别
"""


def resolve_behavior(event: dict) -> str:
    text = ""
    for e in event.get("evidence", []):
        text += e.get("text", "") + " "

    # 如果 evidence 为空，从 topic / event 字段获取
    if not text.strip():
        text = " ".join([
            event.get("topic", ""),
            event.get("canonical_topic", ""),
            event.get("event", ""),
        ])

    # 创造行为
    if any(k in text for k in ["创造了", "做了一个", "设计了一个", "制作了", "第一次创造"]):
        return "create"

    # 修改行为
    if any(k in text for k in ["修改", "调整", "优化", "改一下", "重新设计"]):
        return "modify"

    # 讨论行为
    if any(k in text for k in ["讨论", "聊", "分析", "设定怎么样"]):
        return "discuss"

    return "unknown"