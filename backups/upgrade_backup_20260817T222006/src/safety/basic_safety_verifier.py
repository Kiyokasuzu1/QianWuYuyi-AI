"""
基础安全防火墙 (BasicSafetyVerifier) v1.3

职责：
拦截在任何情况下都不应出现的危险表达。
v1.3 重构：规则定义统一迁移至 safety_rules.py。
"""

from typing import Dict, List, Optional
from src.safety.safety_rules import check_against_rules, SafetyRuleCategory


class BasicSafetyVerifier:
    """基础安全防火墙，引用统一规则库"""

    def verify(self, text: str) -> Dict:
        violations = check_against_rules(text)
        formatted_violations = [
            {
                "pattern": pattern,
                "category": category.value,
                "suggestion": suggestion,
            }
            for pattern, category, severity, suggestion in violations
        ]

        return {
            "safe": len(formatted_violations) == 0,
            "violations": formatted_violations,
            "intent": None,
            "evidence": [],
        }

    def is_safe(self, text: str) -> bool:
        return self.verify(text)["safe"]

    def get_rewrite_hint(self, text: str) -> Optional[str]:
        result = self.verify(text)
        if result["safe"]:
            return None
        hints = [f"「{v['pattern']}」：{v['suggestion']}" for v in result["violations"]]
        return "请修正以下问题：\n" + "\n".join(hints)