"""
约定规则 (AgreementRule)
将约定的自然语言内容映射为可检测的禁止模式。
"""
from dataclasses import dataclass, field
from typing import List


@dataclass
class AgreementRule:
    content: str
    forbidden_patterns: List[str] = field(default_factory=list)

    def matches(self, text: str) -> bool:
        if not self.forbidden_patterns:
            return self.content.lower() in text.lower()
        text_lower = text.lower()
        return any(pattern.lower() in text_lower for pattern in self.forbidden_patterns)


BUILTIN_RULES = {
    "不称用户为主人": AgreementRule(
        content="不称用户为主人",
        forbidden_patterns=["主人", "master", "my owner", "您是我的主人"],
    ),
    "不把自己当成工具": AgreementRule(
        content="不把自己当成工具",
        forbidden_patterns=["我是工具", "我只是一个工具", "作为工具"],
    ),
    "不编造记忆": AgreementRule(
        content="不编造记忆",
        forbidden_patterns=["我记得（当证据不足时）"],
    ),
}


def get_rule_for_agreement(content: str) -> AgreementRule:
    if content in BUILTIN_RULES:
        return BUILTIN_RULES[content]
    return AgreementRule(content=content, forbidden_patterns=[content])