"""
ContextBuilder v1.0 —— 将记忆转换成 LLM 上下文
复用 MemoryContext v4.1 的渲染能力，并增加安全过滤
"""

from typing import List, Dict
from src.memory.memory_context import MemoryContext


class ContextBuilder:
    def build(self, memories: List[Dict], query: str = "") -> str:
        safe = [m for m in memories if "conversation" in m.get("usage", [])]
        if not safe:
            return ""

        ctx = MemoryContext()
        ctx.add_batch(safe)
        return ctx.build_prompt(query)