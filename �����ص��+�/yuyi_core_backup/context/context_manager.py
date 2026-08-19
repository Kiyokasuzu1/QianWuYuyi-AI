"""
上下文管理器。

负责从多个维度获取上下文：
- 对话历史（上一句话）
- 时间窗口（今天下午、最近N小时）
- 长期记忆（向量检索 + 时间过滤）
"""

from datetime import datetime
from typing import Dict, List, Optional

from src.context.time_window import TimeWindow


class ContextManager:
    """
    上下文管理器。

    整合对话历史、时间窗口记忆和向量检索，提供综合上下文。
    不修改现有 MemoryStore / VectorMemory，只读取数据。
    """

    def __init__(self, user_id: str = ""):
        self.user_id = user_id
        self._chat_history: List[Dict] = []
        self._max_history = 20

        # 延迟加载，避免初始化时失败
        self._memory_store = None
        self._vector_memory = None

    def _get_memory_store(self):
        if self._memory_store is None:
            try:
                from src.memory.memory_store import MemoryStore
                self._memory_store = MemoryStore()
            except Exception:
                pass
        return self._memory_store

    def _get_vector_memory(self):
        if self._vector_memory is None:
            try:
                from src.memory.vector import VectorMemory
                self._vector_memory = VectorMemory()
            except Exception:
                pass
        return self._vector_memory

    # ==========================
    # 对话历史
    # ==========================

    def update_chat_history(self, user_message: str, reply: str):
        """更新对话历史"""
        self._chat_history.append({
            "role": "user",
            "content": user_message,
            "timestamp": datetime.now().isoformat(),
        })
        self._chat_history.append({
            "role": "assistant",
            "content": reply,
            "timestamp": datetime.now().isoformat(),
        })

        # 保留最近 N 轮
        if len(self._chat_history) > self._max_history * 2:
            self._chat_history = self._chat_history[-(self._max_history * 2):]

    def get_immediate_context(self) -> Dict:
        """
        获取上一句话的上下文。

        Returns:
            {"last_user_message": str, "last_reply": str, "timestamp": str}
        """
        if len(self._chat_history) < 2:
            return {"last_user_message": "", "last_reply": "", "timestamp": ""}

        last_user = None
        last_reply = None
        for msg in reversed(self._chat_history):
            if msg["role"] == "assistant" and last_reply is None:
                last_reply = msg
            elif msg["role"] == "user" and last_user is None:
                last_user = msg
            if last_user and last_reply:
                break

        return {
            "last_user_message": last_user["content"] if last_user else "",
            "last_reply": last_reply["content"] if last_reply else "",
            "timestamp": last_user["timestamp"] if last_user else "",
        }

    def get_chat_history(self, limit: int = 10) -> List[Dict]:
        """获取最近的对话历史"""
        return self._chat_history[-(limit * 2):] if self._chat_history else []

    # ==========================
    # 时间窗口查询
    # ==========================

    def get_today_context(self, time_range: str = None) -> List[Dict]:
        """
        获取今天指定时间段的上下文。

        Args:
            time_range: 时间窗口类型（morning/afternoon/evening/night），
                       None 表示今天全天

        Returns:
            记忆列表
        """
        if time_range is None:
            start_iso, end_iso = TimeWindow.get_past_window(0)
        else:
            start_iso, end_iso = TimeWindow.get_time_range(time_range)

        return self._search_memories_by_time(start_iso, end_iso)

    def get_recent_context(self, hours: int = 24) -> List[Dict]:
        """
        获取最近 N 小时的上下文。

        Args:
            hours: 小时数

        Returns:
            记忆列表
        """
        start_iso, end_iso = TimeWindow.get_recent_hours(hours)
        return self._search_memories_by_time(start_iso, end_iso)

    # ==========================
    # 历史上下文（向量检索 + 时间过滤）
    # ==========================

    def get_historical_context(
        self,
        query: str,
        days_ago: int = 7,
        limit: int = 5
    ) -> List[Dict]:
        """
        获取 N 天前的相关话题（向量检索 + 时间过滤）。

        Args:
            query: 查询文本
            days_ago: 几天前
            limit: 最大返回数

        Returns:
            相关记忆列表
        """
        vector_memory = self._get_vector_memory()
        if vector_memory is None:
            return []

        # 向量检索
        results = vector_memory.search(query, top_k=limit * 3)

        # 时间过滤：只保留指定日期的记忆
        start_iso, end_iso = TimeWindow.get_past_window(days_ago)
        filtered = []
        for item in results:
            ts = item.get("timestamp", "")
            if start_iso <= ts < end_iso:
                filtered.append(item)

        return filtered[:limit]

    def get_combined_context(self, query: str = "") -> Dict:
        """
        获取综合上下文（上一句话 + 今天 + 历史）。

        Args:
            query: 当前查询（用于历史检索）

        Returns:
            {
                "immediate": {...},       # 上一句话
                "today": [...],           # 今天的记忆
                "historical": [...],      # 相关历史记忆
                "current_window": str,    # 当前时间窗口
            }
        """
        return {
            "immediate": self.get_immediate_context(),
            "today": self.get_today_context(),
            "historical": self.get_historical_context(query) if query else [],
            "current_window": TimeWindow.get_current_window(),
        }

    # ==========================
    # 内部工具
    # ==========================

    def _search_memories_by_time(
        self,
        start_iso: str,
        end_iso: str
    ) -> List[Dict]:
        """按时间范围搜索记忆"""
        memory_store = self._get_memory_store()
        if memory_store is None:
            return []

        all_memories = memory_store.load()
        if not all_memories:
            return []

        # 按用户过滤
        if self.user_id:
            all_memories = [
                m for m in all_memories
                if m.get("user_id") == self.user_id
            ]

        # 按时间过滤
        filtered = []
        for m in all_memories:
            ts = m.get("timestamp", "")
            if not ts:
                continue
            if start_iso <= ts < end_iso:
                filtered.append(m)

        # 按时间倒序
        filtered.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        return filtered


# 全局实例
_global_context_manager: Optional[ContextManager] = None


def get_context_manager(user_id: str = "") -> ContextManager:
    """获取全局上下文管理器实例"""
    global _global_context_manager
    if _global_context_manager is None:
        _global_context_manager = ContextManager(user_id=user_id)
    return _global_context_manager