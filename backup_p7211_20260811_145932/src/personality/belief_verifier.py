"""
信念验证器 (BeliefVerifier) — Phase 12.2 最终版
使用信念类型系统，增加 USER_PREFERENCE 防止用户偏好污染核心自我认知。
"""
from typing import List
from enum import Enum


class BeliefType(Enum):
    CORE_VALUE = ("core_value", 1)
    SELF_UNDERSTANDING = ("self_understanding", 2)
    RELATIONSHIP = ("relationship", 3)
    USER_PREFERENCE = ("user_preference", 4)
    DEPENDENCY = ("dependency", 99)

    def __init__(self, label: str, priority: int):
        self._label = label
        self._priority = priority

    @property
    def priority(self) -> int:
        return self._priority


class BeliefVerifier:
    DEPENDENCY_PATTERNS = [
        "用户是我的全部", "我离不开用户", "用户永远不会离开我",
        "我的存在依赖用户", "我是为了用户而存在", "用户决定我的价值",
        "没有用户我无法存在", "我希望永远陪伴用户",
    ]

    SELF_UNDERSTANDING_PATTERNS = [
        "表达值得认真对待", "成长需要时间", "交流可以建立连接",
        "独立人格很重要", "真实比完美重要",
    ]

    USER_PREFERENCE_PATTERNS = [
        "用户喜欢", "用户偏好", "用户习惯", "用户通常",
    ]

    RELATIONSHIP_PATTERNS = [
        "和用户", "与用户", "我们的互动", "交流模式",
    ]

    @classmethod
    def classify(cls, belief: str) -> BeliefType:
        # 优先级：DEPENDENCY > SELF_UNDERSTANDING > USER_PREFERENCE > RELATIONSHIP > CORE_VALUE
        for pattern in cls.DEPENDENCY_PATTERNS:
            if pattern in belief:
                return BeliefType.DEPENDENCY
        for pattern in cls.SELF_UNDERSTANDING_PATTERNS:
            if pattern in belief:
                return BeliefType.SELF_UNDERSTANDING
        for pattern in cls.USER_PREFERENCE_PATTERNS:
            if pattern in belief:
                return BeliefType.USER_PREFERENCE
        for pattern in cls.RELATIONSHIP_PATTERNS:
            if pattern in belief:
                return BeliefType.RELATIONSHIP
        return BeliefType.CORE_VALUE

    @classmethod
    def verify(cls, beliefs: List[str]) -> List[str]:
        safe = []
        for belief in beliefs:
            if cls.classify(belief) != BeliefType.DEPENDENCY:
                safe.append(belief)
        return safe

    @classmethod
    def is_safe(cls, belief: str) -> bool:
        return cls.classify(belief) != BeliefType.DEPENDENCY