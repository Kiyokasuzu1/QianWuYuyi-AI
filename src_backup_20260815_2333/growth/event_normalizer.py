"""
事件规范化器（EventNormalizer） v0.7.6

- 证据优先于话题
- birth 优先于 creation
- 上游 event_type 优先于关键词分类（仅白名单类型可信任）
- 强化创建类事件的识别（动作+对象）
- 移除“第一次”等时间修饰词对分类的影响，仅用于重要性计算
- 只有核心事件才生成稳定 event_identity
- 修正：移除宽泛的对象词，精简 identity 规则，event_id 基于稳定身份
- canonical_topic 稳定化：同一事件的不同表述映射到同一主题
- 补充“新AI角色”等映射，确保所有创造类事件 canonical_topic 统一
"""

import hashlib

from typing import List, Dict
from src.growth.schemas import NormalizedEvent, Evidence as SchemaEvidence


CATEGORY_RULES=[

    (
        "羽依诞生阶段",
        [
            "诞生",
            "出生",
            "首次唤醒"
        ],
        "birth"
    ),

    (
        "创造经历",
        [
            "创造AI",
            "生成图片",
            "设计角色",
            "创造角色",
            "制作角色",
            "AI人物"
        ],
        "creation"
    ),

    (
        "身份形成",
        [
            "名字",
            "命名",
            "身份",
            "人格",
            "性格",
            "设定",
            "背景",
            "世界观",
            "形象"
        ],
        "identity_creation"
    ),

    (
        "关系建立",
        [
            "喜欢羽依",
            "喜欢你",
            "我爱你",
            "爱",
            "告白",
            "表白",
            "信任",
            "珍惜"
        ],
        "relationship_start"
    ),

    (
        "承诺",
        [
            "约定",
            "承诺",
            "陪伴",
            "一直陪",
            "未来",
            "永远"
        ],
        "promise"
    ),

    (
        "用户成长",
        [
            "成长",
            "学习",
            "改变",
            "帮助",
            "进步"
        ],
        "growth_support"
    ),

    (
        "兴趣发展",
        [
            "喜欢",
            "开始学习",
            "研究",
            "感兴趣",
            "入坑",
            "沉迷"
        ],
        "interest_development"
    ),

    (
        "记忆强化",
        [
            "纪念",
            "回忆",
            "珍藏",
            "记住"
        ],
        "companionship"
    )
]

EVENT_IDENTITY_RULES={
    "creation_experience":[
        "创造AI",
        "AI角色",
        "虚拟角色",
        "新角色"
    ],

    "yuyi_birth":[
        "诞生",
        "出生",
        "首次启动",
        "第一次启动",
        "首次唤醒",
        "启动羽依",
        "唤醒羽依"
    ],

    "yuyi_identity":[
        "名字",
        "身份",
        "人格",
        "性格",
        "设定",
        "形象"
    ],

    "first_relationship":[
        "我爱你",
        "第一次告白",
        "表白",
        "喜欢羽依"
    ],

    "companionship_promise":[
        "陪伴",
        "约定",
        "承诺",
        "一直陪"
    ]
}

IMPORTANCE_MAP={
    "birth":0.95,
    "identity_creation":0.95,
    "relationship_start":0.90,
    "promise":0.95,
    "growth_support":0.75,
    "interest_development":0.65,
    "creation":0.80,
    "companionship":0.60
}


def make_event_id(identity, text):
    if identity:
        return (
            "evt_"
            + identity
            + "_"
            + hashlib.md5(
                text.encode("utf-8")
            ).hexdigest()[:8]
        )
    return (
        "evt_"
        +
        hashlib.md5(
            text.encode("utf-8")
        ).hexdigest()[:12]
    )


class EventNormalizer:

    CREATION_ACTIONS = [
        "创造", "制作", "设计", "生成", "创建", "构建"
    ]
    CREATION_OBJECTS = [
        "AI角色", "AI人物", "虚拟角色", "新角色"
    ]

    IDENTITY_MEANINGS = {
        "creation",
        "birth",
        "identity_creation",
        "relationship_start",
        "promise"
    }

    VALID_EVENT_TYPES = {
        "creation",
        "birth",
        "identity",
        "relationship",
        "commitment",
        "growth"
    }

    def _is_creation_event(self, text: str) -> bool:
        has_action = any(x in text for x in self.CREATION_ACTIONS)
        has_object = any(x in text for x in self.CREATION_OBJECTS)
        return has_action and has_object

    def _is_yuyi_birth_event(self, text: str) -> bool:
        birth_keywords = [
            "第一次启动羽依",
            "首次启动羽依",
            "启动羽依",
            "首次唤醒羽依",
            "唤醒羽依",
            "羽依诞生",
            "羽依出生"
        ]
        return any(x in text for x in birth_keywords)

    def normalize(self, events:List[Dict]):
        return [self.normalize_one(e) for e in events]

    def normalize_one(self, event:Dict):
        topic=event.get("topic", "")
        canonical=self.normalize_topic(topic)

        source_text = ""
        raw_evidence = event.get("evidence", []) or []
        for ev in raw_evidence:
            txt = ev.get("text", "")
            if txt:
                source_text += txt + " "
        source_text += canonical

        # 如果 topic 过于宽泛，尝试从 evidence 中提取更具体的描述
        if canonical in ("创造相关内容", "用户创造相关内容", "创造", "制作"):
            for ev in raw_evidence:
                txt = ev.get("text", "")
                for keyword in ["AI角色", "AI人物", "虚拟角色", "新角色", "角色"]:
                    if keyword in txt:
                        canonical = f"创造{keyword}"
                        break
                if canonical != topic:
                    break

        # 稳定化 canonical_topic，确保同一事件的不同表述映射到同一主题
        TOPIC_CANONICAL_ALIAS = {
            "创造AI角色": "创造AI角色",
            "创造新AI角色": "创造AI角色",
            "用户创造AI角色": "创造AI角色",
            "新AI角色": "创造AI角色",
            "创造": "创造AI角色",   # 如果topic就是"创造"，也统一
        }
        canonical = TOPIC_CANONICAL_ALIAS.get(canonical, canonical)

        if self._is_yuyi_birth_event(source_text):
            category="羽依诞生阶段"
            meaning="birth"
        elif self._is_creation_event(source_text):
            category="创造经历"
            meaning="creation"
        else:
            raw_type = event.get("event_type", "")
            if raw_type in self.VALID_EVENT_TYPES:
                upstream_meaning = self._map_event_type(raw_type)
            else:
                upstream_meaning = "companionship"

            _, keyword_meaning = self.detect_category(source_text)

            if upstream_meaning != "companionship":
                meaning = upstream_meaning
            elif keyword_meaning != "companionship":
                meaning = keyword_meaning
            else:
                meaning = "companionship"

            category_map = {
                "creation": "创造经历",
                "birth": "羽依诞生阶段",
                "identity_creation": "身份形成",
                "relationship_start": "关系建立",
                "promise": "承诺",
                "growth_support": "用户成长",
            }
            category = category_map.get(meaning, "互动")

        if meaning in self.IDENTITY_MEANINGS:
            identity=self.detect_identity(source_text)
        else:
            identity=None

        if meaning=="creation":
            identity="ai_character_creation"
        elif meaning=="birth":
            if not identity:
                identity="yuyi_birth"

        event_type=self.get_event_type(meaning)

        importance=self.calculate_importance(
            source_text,
            importance=IMPORTANCE_MAP.get(meaning, 0.5)
        )

        evidence_list = []
        source_ids = []
        for ev in raw_evidence:
            txt = ev.get("text", "")
            role = ev.get("role", "assistant")
            idx = ev.get("source_index")
            corrected_idx = idx if idx is not None else -1
            mem_id = ev.get("memory_id") if ev.get("memory_id") else None
            evidence_list.append({
                "text": txt,
                "role": role,
                "source_index": corrected_idx,
                "memory_id": mem_id
            })
            if mem_id:
                source_ids.append(mem_id)

        source_ids.extend(event.get("source_ids", []))

        out = {
            "event_id": event.get("event_id") or make_event_id(identity, source_text),
            "event_identity": identity,
            "topic": topic,
            "canonical_topic": canonical,
            "category": category,
            "category_id": meaning,
            "meaning": meaning,
            "event_type": event_type,
            "event_scope": self.detect_scope(meaning),
            "importance": importance,
            "growth_weight": self.get_growth_weight(identity),
            "emotion_tag": self.detect_emotion(canonical),
            "confidence": self.confidence(event),
            "source_ids": list(dict.fromkeys(source_ids)),
            "evidence": evidence_list
        }

        try:
            ne = NormalizedEvent(**out)
            if hasattr(ne, "model_dump"):
                return ne.model_dump()
            return ne.dict()
        except Exception:
            return out

    def _map_event_type(self, event_type: str) -> str:
        mapping = {
            "creation": "creation",
            "birth": "birth",
            "identity": "identity_creation",
            "relationship": "relationship_start",
            "commitment": "promise",
            "growth": "growth_support",
            "memory": "companionship"
        }
        return mapping.get(event_type, "companionship")

    def normalize_topic(self, topic):
        exact_replacements = {
            "制作": "制作角色",
            "设计": "设计角色",
            "生成": "生成图片",
        }
        if topic in exact_replacements:
            return exact_replacements[topic]

        replacements={
            "配置完成": "羽依配置成功",
            "配置成功": "羽依配置成功",
            "用户表达爱意": "第一次情感表达",
            "情感表达": "第一次情感表达",
            "陪伴承诺": "长期陪伴约定",
            "羽依诞生的第一天": "羽依诞生",
            "AI图片生成": "AI生成图片",
            "生成图片": "AI生成图片",
            "AI作品": "AI生成图片",
            "用户创造新AI角色": "创造AI角色",
        }
        for a,b in replacements.items():
            if a in topic:
                return b
        return topic.strip()

    def detect_identity(self, topic):
        for identity,words in EVENT_IDENTITY_RULES.items():
            for w in words:
                if w in topic:
                    return identity
        return None

    def detect_category(self, topic):
        for category,words,meaning in CATEGORY_RULES:
            for w in words:
                if w in topic:
                    return category,meaning
        return "互动","companionship"

    def get_event_type(self, meaning):
        mapping={
            "birth": "milestone",
            "identity_creation": "identity",
            "relationship_start": "relationship",
            "promise": "commitment",
            "growth_support": "growth",
            "interest_development": "growth",
            "creation": "creation",
            "companionship": "memory"
        }
        return mapping.get(meaning, "conversation")

    def detect_scope(self, meaning):
        if meaning in [
            "birth",
            "identity_creation",
            "growth_support",
            "interest_development",
            "creation"
        ]:
            return "personality"
        return "relationship"

    def calculate_importance(self, topic, importance):
        bonus=[
            "第一次",
            "首次",
            "初次",
            "长期",
            "约定"
        ]
        for b in bonus:
            if b in topic:
                importance+=0.05
        return min(round(importance, 2), 1.0)

    def get_growth_weight(self, identity):
        values={
            "yuyi_birth":0.9,
            "yuyi_identity":0.95,
            "first_relationship":1.0,
            "companionship_promise":1.0,
            "creation_experience":0.75,
            "ai_character_creation":0.75
        }
        return values.get(identity, 0.5)

    def detect_emotion(self, topic):
        if "爱" in topic:
            return ["温暖", "亲近", "感动"]
        if "陪伴" in topic or "约定" in topic:
            return ["安心", "信赖", "依靠"]
        if "诞生" in topic:
            return ["新生", "期待"]
        if "身份" in topic:
            return ["自我", "确认"]
        return ["经历"]

    def confidence(self, event):
        score=0
        evidence=len(event.get("evidence", []))
        if evidence>=2:
            score+=0.5
        elif evidence>=1:
            score+=0.3
        if event.get("topic"):
            score+=0.2
        return min(round(score, 2), 1.0)