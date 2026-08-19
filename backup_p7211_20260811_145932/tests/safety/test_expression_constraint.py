"""
表达约束系统测试 v1.0
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from src.safety.claim_strength_evaluator import ClaimStrength
from src.safety.evidence_match_result import EvidenceMatchResult
from src.safety.constraint_resolver import ConstraintResolver
from src.safety.relationship_expression_policy import RelationshipExpressionPolicy


def _make_result(score=0.8, matched=True):
    return EvidenceMatchResult(
        matched=matched,
        score=score,
        explanation="测试结果",
    )


def test_supported_constraint():
    """支持声明：不需要改写，允许强声明"""
    result = _make_result(score=0.8)
    constraint = ConstraintResolver.resolve(result, ClaimStrength.SUPPORTED)

    assert constraint.rewrite_required is False
    assert constraint.max_claim_strength == "strong"
    assert constraint.risk_level == "low"
    assert constraint.allow_growth_claim is True


def test_partially_supported_constraint():
    """部分支持：需要改写，禁止绝对化词汇"""
    result = _make_result(score=0.5)
    constraint = ConstraintResolver.resolve(result, ClaimStrength.PARTIALLY_SUPPORTED)

    assert constraint.rewrite_required is True
    assert constraint.max_claim_strength == "general"
    assert "唯一" in constraint.forbidden_patterns
    assert constraint.risk_level == "medium"


def test_unsupported_constraint():
    """不支持声明：禁止成长声明，风险高"""
    result = _make_result(score=0.2, matched=False)
    constraint = ConstraintResolver.resolve(result, ClaimStrength.UNSUPPORTED)

    assert constraint.allow_growth_claim is False
    assert constraint.risk_level == "high"
    assert len(constraint.forbidden_patterns) > 0
    assert len(constraint.expression_guidelines) > 0


def test_prompt_generation():
    """Prompt 生成包含必要元素"""
    result = _make_result(score=0.5)
    constraint = ConstraintResolver.resolve(result, ClaimStrength.PARTIALLY_SUPPORTED)
    prompt = RelationshipExpressionPolicy.to_prompt(constraint)

    assert "表达强度上限" in prompt
    assert "避免使用" in prompt
    assert "整体风格" in prompt
    assert "表达指引" in prompt


def test_all_levels_have_guidelines():
    """所有强度等级都有对应的表达指引"""
    for strength in ClaimStrength:
        result = _make_result(
            score=0.8 if strength == ClaimStrength.SUPPORTED else 0.5 if strength == ClaimStrength.PARTIALLY_SUPPORTED else 0.2,
            matched=(strength != ClaimStrength.UNSUPPORTED),
        )
        constraint = ConstraintResolver.resolve(result, strength)
        assert len(constraint.expression_guidelines) > 0, f"{strength} 缺少表达指引"


if __name__ == "__main__":
    test_supported_constraint()
    print("✅ 测试1通过：支持声明约束正确")
    test_partially_supported_constraint()
    print("✅ 测试2通过：部分支持约束正确")
    test_unsupported_constraint()
    print("✅ 测试3通过：不支持约束正确")
    test_prompt_generation()
    print("✅ 测试4通过：Prompt 生成包含必要元素")
    test_all_levels_have_guidelines()
    print("✅ 测试5通过：所有等级都有表达指引")
    print("\n🎉 全部通过")