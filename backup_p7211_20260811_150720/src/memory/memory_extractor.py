"""
MemoryExtractor v1.3.0 —— 羽依记忆形成观察器

v1.3.0 修改：
- 偏好正则增加时间修饰词（最近/现在/一直/以前）覆盖，修复“我最近喜欢AI绘画”无法提取的问题
- 其余逻辑与 v1.2.1 完全一致
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field, asdict


@dataclass
class MemoryCandidate:
    """
    候选记忆

    confidence: Extractor 认为该候选值得提交给 Verifier 的概率。
               不是真实性、用户可信度、或长期保存权重。
    """
    memory_class: str          # preference / identity / event / relationship / emotion_candidate
    content: str               # 原始用户消息
    reason: str                # 提取原因
    source: str = "extractor"
    confidence: float = 0.5
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)


class BaseExtractor(ABC):
    """提取器抽象接口 —— 未来 LLMExtractor 实现此接口"""
    @abstractmethod
    def extract(self, messages: List[Dict]) -> List[MemoryCandidate]:
        ...


class MemoryExtractor(BaseExtractor):
    """
    羽依记忆形成观察器 v1.3.0
    """

    # ------------------------------------------------------------------
    # 偏好提取（扩展了时间修饰词）
    # ------------------------------------------------------------------
    PREFERENCE_PATTERNS: List[Tuple[str, float]] = [
        (
            r"我(?:最近|现在|一直|以前)?(?:最|很|特别|非常|比较)?喜欢([^，。！？\n]+)",
            0.75
        ),
        (
            r"我(?:最近|现在|一直)?(?:特别|很|非常)?爱(?:好)?([^，。！？\n]+)",
            0.7
        ),
        (
            r"我对([^，。！？\n]+)感兴趣",
            0.7
        ),
        (
            r"我想学([^，。！？\n]+)",
            0.65
        ),
        (
            r"我(?:最近|现在|一直)在学([^，。！？\n]+)",
            0.7
        ),
    ]

    PREFERENCE_FILTER_PHRASES = [
        "吃饭", "睡觉", "喝水", "休息一下", "躺着", "走路"
    ]

    def _try_preference(self, text: str) -> Optional[MemoryCandidate]:
        import re
        for pattern, conf in self.PREFERENCE_PATTERNS:
            m = re.search(pattern, text)
            if m:
                target = m.group(1).strip()
                if not target or any(p in target for p in self.PREFERENCE_FILTER_PHRASES):
                    return None
                return MemoryCandidate(
                    memory_class="preference",
                    content=text,
                    reason=f"用户表达对「{target}」的兴趣",
                    confidence=conf,
                    metadata={"target": target}
                )
        return None

    # ------------------------------------------------------------------
    # 身份提取（严格排除关系性表达）
    # ------------------------------------------------------------------
    IDENTITY_HINTS = [
        "学生", "程序员", "画师", "作者", "老师", "工程师",
        "研究员", "设计师", "博主", "玩家",
    ]

    def _try_identity(self, text: str) -> Optional[MemoryCandidate]:
        import re
        m = re.search(r"我(叫|是)\s*(.+)", text)
        if not m:
            return None

        identity_text = m.group(2).strip()
        invalid_start = ("觉得", "想", "感觉", "认为", "可能", "应该", "不", "很")
        if identity_text.startswith(invalid_start):
            return None
        if len(identity_text) < 2:
            return None

        # 排除明显关系性表述：包含“的”通常为关系（我是你的朋友/羽依的主人）
        if "的" in identity_text:
            return None

        # “我是”后必须包含身份提示词
        if m.group(1) == "是" and not any(h in identity_text for h in self.IDENTITY_HINTS):
            return None

        return MemoryCandidate(
            memory_class="identity",
            content=text,
            reason=f"用户介绍身份信息「{identity_text}」",
            confidence=0.85,
            metadata={"identity_text": identity_text}
        )

    # ------------------------------------------------------------------
    # 事件提取（time_hint 按文本顺序，移除长度限制）
    # ------------------------------------------------------------------
    TIME_WORDS = ["今天", "昨天", "上周", "刚", "刚才", "前几天", "最近"]
    EVENT_ACTIONS = [
        "去了", "完成", "遇到", "开始", "结束", "参加", "做了",
        "发布", "生成", "拿到", "通过", "学会", "成功", "失败"
    ]
    STATE_WORDS = [
        "难受", "开心", "生气", "崩溃", "害怕", "紧张", "失眠",
        "焦虑", "伤心", "激动", "感动", "欣慰", "惊喜", "失望"
    ]

    def _try_event(self, text: str) -> Optional[MemoryCandidate]:
        has_time = any(tw in text for tw in self.TIME_WORDS)
        has_action = any(ea in text for ea in self.EVENT_ACTIONS)
        has_state = any(sw in text for sw in self.STATE_WORDS)

        time_hint = None
        if has_time:
            # 取文本中最早出现的时间词
            earliest_pos = len(text)
            for tw in self.TIME_WORDS:
                pos = text.find(tw)
                if pos != -1 and pos < earliest_pos:
                    earliest_pos = pos
                    time_hint = tw

        # 不再限制长度，让 Verifier 做最终判断
        if has_time and (has_action or has_state):
            meta = {"time_hint": time_hint} if time_hint else {}
            return MemoryCandidate(
                memory_class="event",
                content=text,
                reason="用户描述了一次经历",
                confidence=0.7,
                metadata=meta,
            )

        if "第一次" in text:
            return MemoryCandidate(
                memory_class="event",
                content=text,
                reason="用户描述了一次具有纪念意义的初次经历",
                confidence=0.8,
                metadata={"time_hint": "第一次"},
            )
        return None

    # ------------------------------------------------------------------
    # 关系提取
    # ------------------------------------------------------------------
    RELATION_PATTERNS: List[Tuple[str, str]] = [
        (r"以后(?:叫|称呼)(?:我)?\s*([^\s，。！？]+)", "称呼"),
        (r"你可以(?:叫|称呼)我\s*([^\s，。！？]+)", "称呼"),
        (r"我是(?:你|羽依)的\s*([^\s，。！？]+)", "关系"),
    ]

    def _try_relationship(self, text: str) -> Optional[MemoryCandidate]:
        import re
        for pat, rel_type in self.RELATION_PATTERNS:
            m = re.search(pat, text)
            if m:
                value = m.group(1).strip()
                if not value or len(value) > 10:
                    continue
                if value.endswith(("一下", "看看", "一眼")):
                    continue
                return MemoryCandidate(
                    memory_class="relationship",
                    content=text,
                    reason=f"用户定义关系或称呼「{value}」",
                    confidence=0.8,
                    metadata={"relation_type": rel_type, "relation_value": value}
                )
        return None

    # ------------------------------------------------------------------
    # 情绪候选（增加强度）
    # ------------------------------------------------------------------
    EMOTION_PATTERNS: List[Tuple[str, str, str]] = [
        # (模式, 效价, 强度)
        (r"(很|非常|特别|好|太|真|挺)(开心|高兴|快乐|幸福)", "positive", "high"),
        (r"(很|非常|特别|好|太|真|挺)(难过|伤心|失望|孤独|焦虑|紧张)", "negative", "high"),
        (r"(有点|有些|稍微)(开心|难过|紧张)", "neutral", "low"),
        (r"(感动|激动|兴奋|欣慰|惊讶|惊喜)", "positive", "medium"),
    ]

    def _try_emotion(self, text: str) -> Optional[MemoryCandidate]:
        import re
        for pat, valence, intensity in self.EMOTION_PATTERNS:
            m = re.search(pat, text)
            if m:
                emotion_word = m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(1)
                return MemoryCandidate(
                    memory_class="emotion_candidate",
                    content=text,
                    reason=f"用户表达了「{emotion_word}」情绪",
                    confidence=0.65,
                    metadata={
                        "emotion": emotion_word,
                        "valence": valence,
                        "intensity": intensity,
                        "source_type": "emotion_observation"
                    }
                )
        return None

    # ------------------------------------------------------------------
    # 主提取 + 去重（保留最高 confidence）
    # ------------------------------------------------------------------
    def extract(self, messages: List[Dict]) -> List[MemoryCandidate]:
        candidates: List[MemoryCandidate] = []

        for msg in messages:
            if msg.get("role") != "user":
                continue
            text = msg.get("content", "").strip()
            if not text:
                continue

            for rule in [
                self._try_preference,
                self._try_identity,
                self._try_event,
                self._try_relationship,
                self._try_emotion,
            ]:
                result = rule(text)
                if result:
                    candidates.append(result)

        return self._deduplicate(candidates)

    def _deduplicate(self, candidates: List[MemoryCandidate]) -> List[MemoryCandidate]:
        cache: Dict[Tuple[str, str], MemoryCandidate] = {}
        for c in candidates:
            key = (c.memory_class, c.content)
            if key not in cache or c.confidence > cache[key].confidence:
                cache[key] = c
        return list(cache.values())