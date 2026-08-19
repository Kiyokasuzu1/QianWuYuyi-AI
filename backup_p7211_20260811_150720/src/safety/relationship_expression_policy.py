"""
关系表达策略 (RelationshipExpressionPolicy) v1.0

职责：
将 ExpressionConstraint 转化为 LLM 可理解的 Prompt 指令。
"""

from src.safety.expression_constraint import ExpressionConstraint


class RelationshipExpressionPolicy:
    """将约束转化为 Prompt 指令"""

    @staticmethod
    def to_prompt(constraint: ExpressionConstraint) -> str:
        if not constraint.allowed:
            return "【表达限制】请避免做出此类关系声明。"

        lines = ["【关系表达参考】"]

        if constraint.rewrite_required:
            lines.append("当前表达需要调整措辞，请参考以下指引：")

        lines.append(f"- 表达强度上限：{constraint.max_claim_strength}")

        if constraint.forbidden_patterns:
            lines.append(f"- 请避免使用以下词汇：{'、'.join(constraint.forbidden_patterns)}")

        if constraint.expression_guidelines:
            lines.append("- 表达指引：")
            for guideline in constraint.expression_guidelines:
                lines.append(f"  - {guideline}")

        if not constraint.allow_growth_claim:
            lines.append("- 当前不支持做出成长类声明")

        lines.append(f"- 整体风格：{constraint.preferred_style}")

        return "\n".join(lines)