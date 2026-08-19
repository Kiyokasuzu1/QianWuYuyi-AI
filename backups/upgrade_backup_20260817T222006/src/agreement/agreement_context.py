"""
约定上下文提供器 (AgreementContext)
将 Agreement 列表转换为 Prompt 可用的自然语言描述。
"""
from typing import List
from src.agreement.agreement import Agreement, AgreementPriority


class AgreementContext:
    @staticmethod
    def build(agreements: List[Agreement]) -> str:
        if not agreements:
            return ""

        immutable_rules = []
        high_rules = []

        for a in agreements:
            if a.priority == AgreementPriority.IMMUTABLE:
                immutable_rules.append(a.content)
            elif a.priority == AgreementPriority.HIGH:
                high_rules.append(a.content)

        if not immutable_rules and not high_rules:
            return ""

        lines = []

        if immutable_rules:
            lines.append("【不可改变核心约定】")
            lines.append("以下规则绝对不能违反，优先级高于一切：")
            for i, rule in enumerate(immutable_rules, 1):
                lines.append(f"{i}. {rule}")

        if high_rules:
            if lines:
                lines.append("")
            lines.append("【长期偏好】")
            lines.append("以下规则应尽量遵守：")
            for rule in high_rules:
                lines.append(f"- {rule}")

        return "\n".join(lines)