"""
记忆摘要器。

将多条检索到的记忆合并为一段摘要，
放在系统提示中，减少逐条发送的 token 消耗。
"""

from typing import Dict, List


class MemorySummarizer:
    """
    记忆摘要器。

    设计原则：
    - 按重要性排序，高重要性优先
    - 低重要性记忆可以跳过
    - 合并成一段文本放在系统提示里
    - 不改变记忆检索逻辑
    """

    def __init__(self, max_memories: int = 5, importance_threshold: float = 0.2):
        self.max_memories = max_memories
        self.importance_threshold = importance_threshold

    def summarize(self, memories: List[Dict]) -> str:
        """
        生成记忆摘要。

        Args:
            memories: 记忆列表

        Returns:
            记忆摘要字符串（适合放在系统提示中）
        """
        if not memories:
            return ""

        # 1. 过滤低重要性
        filtered = [
            m for m in memories
            if m.get("importance", 0.5) >= self.importance_threshold
        ]

        if not filtered:
            # 如果全部低于阈值，取前 2 条保底
            filtered = memories[:2]

        # 2. 按重要性降序
        filtered.sort(
            key=lambda x: x.get("importance", 0.5),
            reverse=True
        )

        # 3. 取前 N 条
        selected = filtered[:self.max_memories]

        # 4. 构建摘要
        return self._build_summary(selected)

    def _build_summary(self, memories: List[Dict]) -> str:
        """构建摘要文本"""
        parts = []

        for i, m in enumerate(memories, 1):
            content = m.get("content", "").strip()
            if not content:
                continue

            importance = m.get("importance", 0.5)
            emotion = m.get("emotion_tag", "")

            # 重要性标记
            if importance >= 0.8:
                prefix = "【重要记忆】"
            elif importance >= 0.5:
                prefix = "【记忆】"
            else:
                prefix = "【旧记忆】"

            # 单条记忆长度限制
            if len(content) > 80:
                content = content[:80] + "..."

            if emotion:
                parts.append(f"{prefix}[{emotion}] {content}")
            else:
                parts.append(f"{prefix}{content}")

        if not parts:
            return ""

        return "\n".join(parts)
