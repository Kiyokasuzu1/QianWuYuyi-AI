# MemoryService: 提供检索封装与元数据过滤功能（支持 importance/time_range/emotion_tag）
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path

from src.memory.memory_store import MemoryStore
from src.memory.vector import VectorMemory

class MemoryService:
    def __init__(self, user_context=None, memory_path=None):
        # memory_path 或 user_context 任选其一
        self.store = MemoryStore(user_context or memory_path)
        self.vector = VectorMemory()

    def _filter_by_metadata(self, memories: List[Dict], importance_min: float = 0.0,
                            time_from: Optional[str] = None, time_to: Optional[str] = None,
                            emotion_tag: Optional[str] = None) -> List[Dict]:
        results = []
        for m in memories:
            imp = float(m.get("importance", 0.5) or 0.5)
            if imp < importance_min:
                continue
            ts = m.get("timestamp")
            if ts and time_from:
                try:
                    if datetime.fromisoformat(ts) < datetime.fromisoformat(time_from):
                        continue
                except Exception:
                    pass
            if ts and time_to:
                try:
                    if datetime.fromisoformat(ts) > datetime.fromisoformat(time_to):
                        continue
                except Exception:
                    pass
            if emotion_tag and m.get("emotion_tag", "") != emotion_tag:
                continue
            results.append(m)
        return results

    def semantic_search(self, query: str, top_k: int = 5,
                        importance_min: float = 0.0,
                        time_from: Optional[str] = None,
                        time_to: Optional[str] = None,
                        emotion_tag: Optional[str] = None) -> List[Dict]:
        """
        1) 使用向量搜索得到候选 mem_id
        2) 加载这些 Memory 并按元数据进行过滤
        3) 返回带元数据结果
        """
        vector_results = self.vector.search(query, top_k=top_k)
        # vector_results items contain mem_id in mem_id
        mem_ids = [r.get("mem_id") for r in vector_results if r.get("mem_id")]
        candidates = []
        for mid in mem_ids:
            mem = self.store.get_by_id(mid)
            if mem:
                candidates.append(mem)
        filtered = self._filter_by_metadata(candidates, importance_min, time_from, time_to, emotion_tag)
        # 返回原始向量评分与记忆文本的结合视图（保留 relevance 若有）
        out = []
        for vr in vector_results:
            mid = vr.get("mem_id")
            for f in filtered:
                if f.get("id") == mid:
                    entry = dict(f)
                    entry["relevance"] = vr.get("relevance", 1.0)
                    out.append(entry)
        # 按 relevance 排序
        out.sort(key=lambda x: x.get("relevance", 0), reverse=True)
        return out
