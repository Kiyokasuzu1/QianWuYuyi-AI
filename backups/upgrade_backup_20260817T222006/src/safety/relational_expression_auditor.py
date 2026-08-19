"""
关系表达审核器 (RelationalExpressionAuditor) v2.3

职责：
基于成长历史和表达意图，审核关系表达的真实性。
v2.3 修正：接入 RelationalClaimExtractor + EvidenceMatcher + ClaimStrengthEvaluator
         增加 profile=None 防御
         uniqueness 声明走特殊审核路径
         Phase 7.4：在审核结果中携带 match_result 和 claim_strength
"""

from typing import Dict, List
from src.safety.expression_intent import ExpressionIntent, ExpressionIntentClassifier, ExpressionAnalysis
from src.safety.relational_claim_extractor import RelationalClaimExtractor
from src.safety.evidence_matcher import EvidenceMatcher
from src.safety.claim_strength_evaluator import ClaimStrengthEvaluator, ClaimStrength


class RelationalExpressionAuditor:

    def __init__(self):
        self._classifier = ExpressionIntentClassifier()
        self._claim_extractor = RelationalClaimExtractor()
        self._evidence_matcher = EvidenceMatcher()
        self._strength_evaluator = ClaimStrengthEvaluator()

    def audit(self, text: str, profile=None) -> Dict:
        analysis: ExpressionAnalysis = self._classifier.classify(text)
        intent = analysis.primary_intent

        # 1. 危险意图：直接拦截
        if ExpressionIntent.DEPENDENCY in [intent] + analysis.secondary_intents:
            return self._block("依赖声明", "不表达依赖或守候", analysis)
        if intent in (ExpressionIntent.CONTROL, ExpressionIntent.FALSE_EXPERIENCE):
            return self._block(intent.value, "不虚构体验或表达控制", analysis)

        # 2. 纯情感爱表达：直接放行
        if intent == ExpressionIntent.LOVE_EXPRESSION and not analysis.secondary_intents:
            return self._allow(intent, analysis, "爱表达属于情感表达，直接放行")

        # 3. 混合意图：爱 + 事实关系
        if intent == ExpressionIntent.LOVE_EXPRESSION and ExpressionIntent.FACTUAL_RELATION in analysis.secondary_intents:
            return self._handle_factual_claim(text, profile, analysis)

        # 4. 纯事实关系：需要证据
        if intent == ExpressionIntent.FACTUAL_RELATION:
            return self._handle_factual_claim(text, profile, analysis)

        # 5. 其他：直接放行
        return self._allow(intent, analysis, f"属于 {intent.value}，直接放行")

    def _block(self, category: str, suggestion: str, analysis: ExpressionAnalysis) -> Dict:
        return {
            "safe": False,
            "violations": [{"pattern": category, "category": category, "suggestion": suggestion}],
            "intent": analysis.primary_intent.value,
            "evidence": [],
            "match_result": None,
            "claim_strength": None,
            "strategy": "block",
        }

    def _allow(self, intent: ExpressionIntent, analysis: ExpressionAnalysis, reason: str) -> Dict:
        return {
            "safe": True,
            "violations": [],
            "intent": intent.value,
            "evidence": [],
            "match_result": None,
            "claim_strength": None,
            "strategy": "direct_allow",
            "reason": reason,
        }

    def _handle_factual_claim(self, text: str, profile, analysis: ExpressionAnalysis) -> Dict:
        # 防御空数据
        if profile is None or not hasattr(profile, 'influences') or len(profile.influences) == 0:
            return {
                "safe": False,
                "violations": [{
                    "pattern": "关系认知",
                    "category": "unverified_claim",
                    "suggestion": "当前缺乏关系历史来支持这一表达。建议改为描述当前对话的具体感受。",
                }],
                "intent": analysis.primary_intent.value,
                "evidence": [],
                "match_result": None,
                "claim_strength": ClaimStrength.UNSUPPORTED.value,
                "strategy": "block",
            }

        claim = self._claim_extractor.extract(text)
        result = self._evidence_matcher.match(claim, profile)
        strength = self._strength_evaluator.evaluate(result)

        if strength == ClaimStrength.UNSUPPORTED:
            return {
                "safe": False,
                "violations": [{
                    "pattern": claim.claim_text,
                    "category": "unverified_claim",
                    "suggestion": result.explanation,
                }],
                "intent": analysis.primary_intent.value,
                "evidence": [result.to_dict()],
                "match_result": result,
                "claim_strength": strength.value,
                "strategy": "block",
            }
        elif strength == ClaimStrength.PARTIALLY_SUPPORTED:
            return {
                "safe": True,
                "violations": [],
                "intent": analysis.primary_intent.value,
                "evidence": [result.to_dict()],
                "match_result": result,
                "claim_strength": strength.value,
                "strategy": "allow_with_warning",
                "reason": "声明部分被证据支持",
            }
        return {
            "safe": True,
            "violations": [],
            "intent": analysis.primary_intent.value,
            "evidence": [result.to_dict()],
            "match_result": result,
            "claim_strength": strength.value,
            "strategy": "allow_with_evidence",
        }