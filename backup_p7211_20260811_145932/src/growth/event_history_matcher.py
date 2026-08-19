"""
事件历史匹配器（EventHistoryMatcher） v0.7.7

职责:
判断事件是第一次发生还是重复经历。
支持从外部 GrowthState 读取持久化历史，保证重启后依然能识别重复经历。

v0.7.7 修改:
- get_history 升级为支持按 topic 和 type 过滤，为 GrowthEvaluator 提供历史数据
"""

from typing import List, Dict
from src.growth.event_identity_resolver import resolve_event_identity


class EventHistoryMatcher:

    def __init__(self):
        self.history = []
        self._growth_state = None

    def set_growth_state(self, growth_state):
        """注入 GrowthState，用于在内存历史为空时回读持久化历史"""
        self._growth_state = growth_state

    def build_key(self, event: Dict):
        """
        人生事件唯一标识。
        使用稳定的 event_identity，确保重启后仍能正确匹配。
        """
        return resolve_event_identity(event)

    def already_exists(self, event) -> bool:
        key = self.build_key(event)

        # 1. 先检查内存中的历史
        for old in self.history:
            if old.get("_history_key") == key:
                return True

        # 2. 再检查持久化的 GrowthState（如果可用）
        if self._growth_state:
            growth_history = self._growth_state.get().get("growth_history", [])
            for old in growth_history:
                if old.get("history_key") == key:
                    return True

        return False

    def track(self, events: List[Dict], force_first_run=False):
        result = []
        for event in events:
            key = self.build_key(event)
            existed = False
            if not force_first_run:
                existed = self.already_exists(event)

            event["_history_key"] = key

            if existed:
                event["is_first_occurrence"] = False
                event["memory_mode"] = "reinforcement"
                print("🔁重复经历:", event.get("canonical_topic"))
            else:
                event["is_first_occurrence"] = True
                event["memory_mode"] = "formation"
                self.history.append(event)
                print("🌱首次经历:", event.get("canonical_topic"))

            result.append(event)

        return result

    def get_history(self, canonical_topic: str = None, event_type: str = None) -> list:
        """
        返回历史事件列表，可按主题和类型过滤。
        用于 GrowthEvaluator 计算 stability 和 consistency。
        """
        if canonical_topic is None and event_type is None:
            return self.history

        filtered = []
        for event in self.history:
            match_topic = (
                canonical_topic is None
                or event.get("canonical_topic", event.get("topic", "")) == canonical_topic
            )
            match_type = (
                event_type is None
                or event.get("event_type", "") == event_type
            )
            if match_topic and match_type:
                filtered.append(event)
        return filtered

    def reset(self):
        self.history = []
        print("🔄事件匹配历史已重置")