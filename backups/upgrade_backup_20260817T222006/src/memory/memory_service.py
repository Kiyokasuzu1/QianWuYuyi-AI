# MemoryService: 提供检索封装、元数据过滤和相关性排序
from datetime import datetime
from typing import List, Dict, Optional

from src.memory.memory_store import MemoryStore
from src.memory.memory_provider import MemoryProvider
from src.memory.memory_relevance_evaluator import MemoryRelevanceEvaluator


class MemoryService:
    def __init__(self, user_context=None, memory_path=None, relevance_evaluator=None, vector_memory=None):
        # Phase 4.4.2 Memory Authority 收口：优先通过 RuntimeBridge 获取共享实例
        # user_context / memory_path / MemoryStore 实例三选一
        if isinstance(user_context, MemoryStore):
            self.store = user_context
        else:
            # 优先 RuntimeBridge
            self.store = None
            try:
                from src.runtime.runtime_bridge import get_runtime_bridge
                _bridge = get_runtime_bridge()
                _shared_store = _bridge.get_memory_store()
                if _shared_store is not None:
                    self.store = _shared_store
            except Exception:
                pass
            # Fallback：RuntimeBridge 不可用时 → MemoryProvider 共享单例
            if self.store is None:
                # Phase 4.0-R2.3.1: 不再 MemoryStore(user_context or memory_path) 自建
                # 用进程唯一的共享单例 Authority。
                # 注意：显式传入 user_context/memory_path 本来就走上面 RuntimeBridge
                # 优先分支；走到此处时这些参数语义上被忽略，统一走 MemoryProvider。
                self.store = MemoryProvider.get_store()

        # Phase 4.4.2 VectorMemory Authority 收口：优先通过 RuntimeBridge 获取共享实例
        if vector_memory is not None:
            self.vector = vector_memory
        else:
            self.vector = None
            try:
                from src.runtime.runtime_bridge import get_runtime_bridge
                _bridge = get_runtime_bridge()
                _shared_vm = _bridge.get_vector_memory()
                if _shared_vm is not None:
                    self.vector = _shared_vm
            except Exception:
                pass
            # Fallback：RuntimeBridge 不可用时自建
            if self.vector is None:
                from src.memory.vector import VectorMemory
                self.vector = VectorMemory()
        self.relevance_evaluator = relevance_evaluator or MemoryRelevanceEvaluator()

    def _filter_by_metadata(
        self,
        memories: List[Dict],
        importance_min: float = 0.0,
        time_from: Optional[str] = None,
        time_to: Optional[str] = None,
        emotion_tag: Optional[str] = None,
    ) -> List[Dict]:
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

    def semantic_search(
        self,
        query: str,
        top_k: int = 5,
        importance_min: float = 0.0,
        time_from: Optional[str] = None,
        time_to: Optional[str] = None,
        emotion_tag: Optional[str] = None,
        relationship_focus: bool = False,
        identity_focus: bool = False,
        current_emotion: Optional[str] = None,
    ) -> List[Dict]:
        """
        1) 使用向量搜索得到候选 mem_id
        2) 加载这些 Memory 并按元数据进行过滤
        3) 用 MemoryRelevanceEvaluator 排序
        """
        vector_results = self.vector.search(query, top_k=top_k)
        mem_ids = [r.get("mem_id") for r in vector_results if r.get("mem_id")]
        candidates = []
        for mid in mem_ids:
            mem = self.store.get_by_id(mid)
            if mem:
                candidates.append(mem)
        filtered = self._filter_by_metadata(candidates, importance_min, time_from, time_to, emotion_tag)

        out = []
        for vr in vector_results:
            mid = vr.get("mem_id")
            for f in filtered:
                if f.get("id") == mid:
                    entry = dict(f)
                    entry["_vector_relevance"] = vr.get("relevance", 1.0)
                    entry["semantic_relevance"] = vr.get("relevance", 1.0)
                    out.append(entry)

        ranked = self.relevance_evaluator.rank_memories(
            out,
            query=query,
            context={
                "emotion_tag": emotion_tag,
                "current_emotion": current_emotion or emotion_tag,
                "relationship_focus": relationship_focus,
                "identity_focus": identity_focus,
            },
            top_k=top_k,
        )
        for entry in ranked:
            entry["relevance"] = entry.get("memory_relevance", entry.get("semantic_relevance", 0.0))
        return ranked

    def rank_memories(self, memories: List[Dict], query: str = "", **context) -> List[Dict]:
        """对任意记忆候选列表执行统一相关性排序。"""
        return self.relevance_evaluator.rank_memories(memories, query=query, context=context)

    def get_relevance_history(self, limit: int = 50) -> List[Dict]:
        return [item.to_dict() for item in self.relevance_evaluator.get_history(limit=limit)]

    def get_relevance_snapshot(self) -> Dict:
        return self.relevance_evaluator.get_snapshot().to_dict()
