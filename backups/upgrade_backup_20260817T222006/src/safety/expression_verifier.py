"""
表达审核器 (ExpressionVerifier) v3.2

职责：
作为安全审核的总入口，串联基础安全防火墙和意图驱动的关系表达审核器。
v3.2 更新：修复接口兼容性，支持 relationship_profile 参数传递。
为 Phase 7.2 预留 personality_state 接口。
"""

from typing import Dict, Optional
from src.safety.basic_safety_verifier import BasicSafetyVerifier
from src.safety.relational_expression_auditor import RelationalExpressionAuditor


class ExpressionVerifier:
    """安全审核管道"""

    def __init__(self):
        self._basic_safety = BasicSafetyVerifier()
        self._relational_auditor = RelationalExpressionAuditor()

    def verify(self, text: str, profile=None, personality_state=None) -> Dict:
        """
        执行完整的审核管道。

        Args:
            text: 待审核的回复文本
            profile: 关系画像（RelationshipProfile），可选
            personality_state: 当前人格状态，为 Phase 7.2 预留

        Returns:
            {"safe": bool, "violations": [...], "intent": ..., "evidence": [...]}
        """
        # 1. 基础安全检查
        basic_result = self._basic_safety.verify(text)
        if not basic_result["safe"]:
            return basic_result

        # 2. 意图驱动的关系表达审核（传递 profile）
        return self._relational_auditor.audit(text, profile)

    def is_safe(self, text: str, profile=None) -> bool:
        return self.verify(text, profile)["safe"]

    def get_rewrite_hint(self, text: str, profile=None) -> Optional[str]:
        result = self.verify(text, profile)
        if result["safe"]:
            return None
        hints = [f"「{v['pattern']}」：{v['suggestion']}" for v in result["violations"]]
        return "请修正以下问题：\n" + "\n".join(hints)