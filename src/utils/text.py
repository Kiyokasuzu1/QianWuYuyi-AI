import re


def clean_content(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'<system_reminder>.*?</system_reminder>', '', text, flags=re.DOTALL)
    text = re.sub(r'\[Image[^\]]*\]', '', text)
    text = re.sub(r'<image_caption>.*?</image_caption>', '', text, flags=re.DOTALL)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def truncate(text: str, max_len: int = 100, suffix: str = "...") -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - len(suffix)] + suffix


# V1.1.1 Context Continuity: 宿主插件会把检索记忆块 / 系统提醒拼接在
# 用户消息内传给引擎。历史消息在持久化时保留原始形态，但进入 Prompt /
# 记忆检索 query 前必须剥离，否则 20 轮历史会重复携带 ~5KB/条的过期记忆。
_RAG_MEMORY_BLOCK_RE = re.compile(
    r'<RAG-Faiss-Memory>.*?</RAG-Faiss-Memory>', re.DOTALL,
)
_SYSTEM_REMINDER_RE = re.compile(
    r'<system_reminder>.*?</system_reminder>', re.DOTALL,
)


def strip_context_blocks(text: str) -> str:
    """剥离宿主注入的上下文块（<RAG-Faiss-Memory> / <system_reminder>）。

    仅用于 Prompt 注入与检索 query 构建；持久化数据保留原始形态。
    非 str 输入返回空字符串。
    """
    if not isinstance(text, str) or not text:
        return ""
    text = _RAG_MEMORY_BLOCK_RE.sub('', text)
    text = _SYSTEM_REMINDER_RE.sub('', text)
    return text.strip()