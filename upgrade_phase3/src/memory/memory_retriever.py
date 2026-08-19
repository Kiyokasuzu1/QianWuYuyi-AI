"""
MemoryRetriever v1.0 —— 简单关键词记忆检索
使用双字片段 (bigram) 提高中文匹配率
"""

from typing import List, Dict


class MemoryRetriever:
    def __init__(self, store):
        self.store = store

    def search(self, query: str, limit: int = 5) -> List[Dict]:
        memories = self.store.load()
        keywords = self._keywords(query)
        scored = []

        for mem in memories:
            content = mem.get("content", "")
            score = sum(1 for kw in keywords if kw in content)
            if score > 0:
                mem_copy = mem.copy()
                mem_copy["_score"] = score
                scored.append(mem_copy)

        scored.sort(key=lambda x: x["_score"], reverse=True)
        return scored[:limit]

    def _keywords(self, text: str) -> List[str]:
        if len(text) < 2:
            return [text]
        return [text[i:i+2] for i in range(len(text)-1)]