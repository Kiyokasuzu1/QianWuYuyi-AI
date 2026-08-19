"""
记忆格式化器（MemoryFormatter）
职责：将 MemoryContext 列表格式化为 Prompt 文本
"""

from typing import List
from src.memory.memory_context import MemoryContext


class MemoryFormatter:
    @staticmethod
    def format_for_prompt(events: List[MemoryContext]) -> str:
        if not events:
            return ""

        lines = ["【你记得的重要经历】"]
        for i, ctx in enumerate(events, 1):
            lines.append(ctx.to_prompt_text(i - 1))

        return "\n".join(lines)

    @staticmethod
    def format_as_memory_text(events: List[MemoryContext]) -> str:
        if not events:
            return ""
        return "你经历过这些事：\n" + "\n".join(f"- {ctx.summary}" for ctx in events)