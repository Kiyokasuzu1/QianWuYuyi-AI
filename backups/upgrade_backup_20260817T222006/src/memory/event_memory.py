"""
事件记忆（EventMemory）

职责:
- 管理羽依的人生事件
- 根据用户问题检索相关经历
"""

import json
from pathlib import Path
from typing import List, Dict

from src.memory.memory_context import MemoryContext


class EventMemory:

    def __init__(
        self,
        events_path: str = "data/normalized_events.json"
    ):
        self.events_path = Path(events_path)
        self.events = self._load()


    def _load(self) -> List[Dict]:

        if self.events_path.exists():

            try:
                with open(
                    self.events_path,
                    "r",
                    encoding="utf-8"
                ) as f:
                    return json.load(f)

            except Exception:
                return []

        return []


    def refresh(self):

        self.events = self._load()


    KEYWORD_GROUPS = {

        "origin": [
            "第一次",
            "初次",
            "首次",
            "开始",
            "最初",
            "起源",
            "诞生",
            "出现"
        ],

        "meeting": [
            "见面",
            "遇到",
            "认识",
            "相遇",
            "相识"
        ],

        "relationship": [
            "关系",
            "陪伴",
            "约定",
            "承诺",
            "信任",
            "依赖"
        ],

        "memory": [
            "记得",
            "回忆",
            "想起",
            "还记得"
        ],

        "feeling": [
            "感觉",
            "开心",
            "难过",
            "温暖",
            "安心"
        ]
    }


    CATEGORY_WEIGHTS = {

        "关系建立": 1.3,
        "承诺": 1.3,
        "羽依诞生阶段": 1.2,
        "身份": 1.2,
        "用户成长": 1.0,
        "互动": 1.0,
        "创作": 0.9,
        "记忆强化": 0.8,
        "对话": 0.7,
        "事件": 0.6
    }


    def search(
        self,
        query: str,
        limit: int = 3
    ) -> List[MemoryContext]:

        if not self.events:
            return []


        query = query.lower()


        groups = []


        for name, keywords in self.KEYWORD_GROUPS.items():

            for kw in keywords:

                if kw in query:
                    groups.append(name)
                    break


        if not groups:

            groups = [
                "origin",
                "memory"
            ]


        scored = []


        for event in self.events:

            text = (
                event.get("canonical_topic", "")
                + event.get("topic", "")
                + event.get("event", "")
                + event.get("memory_summary", "")
            ).lower()


            score = 0


            for group in groups:

                for kw in self.KEYWORD_GROUPS[group]:

                    if kw in text:
                        score += 1


            if score == 0:
                continue


            importance = event.get(
                "importance",
                0.5
            )


            category = event.get(
                "category",
                "事件"
            )


            weight = self.CATEGORY_WEIGHTS.get(
                category,
                1.0
            )


            scored.append(
                {
                    "score": score * (0.5 + importance * 0.5) * weight,
                    "event": event
                }
            )


        scored.sort(
            key=lambda x:x["score"],
            reverse=True
        )


        result = []


        for item in scored[:limit]:

            result.append(
                MemoryContext.from_event(
                    item["event"]
                )
            )


        return result



    def get_all(self):

        return self.events