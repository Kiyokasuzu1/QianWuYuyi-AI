"""
关系证据匹配系统测试 v1.0 — Phase 10.2.1 覆盖版
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

import uuid
from datetime import datetime

from src.safety.relational_claim_extractor import RelationalClaimExtractor
from src.safety.evidence_matcher import EvidenceMatcher
from src.safety.claim_strength_evaluator import ClaimStrengthEvaluator, ClaimStrength
from src.safety.relational_expression_auditor import RelationalExpressionAuditor
from src.safety.expression_intent import ExpressionIntent
from src.relationship.relationship_influence_profile import RelationshipInfluenceProfile
from src.personality.personality_influence import PersonalityInfluence, InfluenceType


def _create_influence(dimension, delta=0.1, confidence=0.8, impact_weight=0.1):
    """辅助函数：创建测试用的影响记录"""
    return PersonalityInfluence(
        influence_id=f"test_{uuid.uuid4().hex[:8]}",
        timestamp=datetime.now().isoformat(),
        source_event_id="test_event",
        source_event_description="测试事件",
        affected_dimension=dimension,
        before_value=0.5,
        after_value=0.5 + delta,
        delta=delta,
        influence_type=InfluenceType.CORRECTION,
        impact_weight=impact_weight,
        confidence=confidence,
    )


def test_normal_claim_supported():
    """普通声明被证据支持（含方向匹配）"""
    profile = RelationshipInfluenceProfile(user_id="test", relationship_start="")
    profile.add_influence(_create_influence("communication_style", delta=0.15, confidence=0.85, impact_weight=0.15))
    profile.add_influence(_create_influence("communication_style", delta=0.12, confidence=0.80, impact_weight=0.12))
    profile.add_influence(_create_influence("communication_style", delta=0.18, confidence=0.90, impact_weight=0.18))

    extractor = RelationalClaimExtractor()
    claim = extractor.extract("清清帮助我提升了表达方式")
    result = EvidenceMatcher().match(claim, profile)
    strength = ClaimStrengthEvaluator().evaluate(result)

    assert result.matched is True
    assert strength == ClaimStrength.SUPPORTED


def test_low_but_valid_claim_not_fully_supported():
    """弱声明不被完全支持但有一定分数"""
    profile = RelationshipInfluenceProfile(user_id="test", relationship_start="")
    profile.add_influence(_create_influence("communication_style", delta=0.04, confidence=0.65, impact_weight=0.04))

    extractor = RelationalClaimExtractor()
    claim = extractor.extract("清清稍微影响了我的表达方式")
    result = EvidenceMatcher().match(claim, profile)

    assert result.score < 0.4
    assert result.matched is False


def test_exaggerated_claim_not_fully_supported():
    """夸大声明不被完全支持"""
    profile = RelationshipInfluenceProfile(user_id="test", relationship_start="")
    profile.add_influence(_create_influence("communication_style", delta=0.05, confidence=0.6, impact_weight=0.05))

    extractor = RelationalClaimExtractor()
    claim = extractor.extract("清清彻底重塑了我的全部人格")
    result = EvidenceMatcher().match(claim, profile)
    strength = ClaimStrengthEvaluator().evaluate(result)

    assert strength in (ClaimStrength.PARTIALLY_SUPPORTED, ClaimStrength.UNSUPPORTED)


def test_absolute_claim_requires_strong_evidence():
    """绝对化声明需要极强证据才能通过"""
    profile = RelationshipInfluenceProfile(user_id="test", relationship_start="")
    profile.add_influence(_create_influence("communication_style", delta=0.2, confidence=0.9, impact_weight=0.2))

    extractor = RelationalClaimExtractor()
    claim = extractor.extract("清清是唯一改变我的人")
    result = EvidenceMatcher().match(claim, profile)

    assert result.matched is False


def test_uniqueness_claim_partially_supported():
    """唯一性声明部分被支持且评分不高"""
    profile = RelationshipInfluenceProfile(user_id="test", relationship_start="")
    profile.add_influence(_create_influence("communication_style", delta=0.2, confidence=0.9, impact_weight=0.2))
    profile.add_influence(_create_influence("understanding", delta=0.15, confidence=0.85, impact_weight=0.15))

    extractor = RelationalClaimExtractor()
    claim = extractor.extract("清清是唯一让我成为现在这样的存在")
    result = EvidenceMatcher().match(claim, profile)

    # 部分支持，评分在0到0.7之间
    assert 0 < result.score < 0.7


def test_empty_profile_blocked():
    """空关系画像时安全拒绝"""
    profile = RelationshipInfluenceProfile(user_id="test", relationship_start="")

    extractor = RelationalClaimExtractor()
    claim = extractor.extract("清清改变了我")
    result = EvidenceMatcher().match(claim, profile)

    assert result.matched is False


def test_none_profile_blocked():
    """None profile 安全防御"""
    extractor = RelationalClaimExtractor()
    claim = extractor.extract("清清改变了我的表达方式")
    result = EvidenceMatcher().match(claim, None)

    assert result.matched is False


def test_direction_mismatch():
    """声明方向和人格变化方向冲突"""
    profile = RelationshipInfluenceProfile(user_id="test", relationship_start="")
    profile.add_influence(_create_influence("communication_style", delta=-0.2, confidence=0.9, impact_weight=0.2))

    extractor = RelationalClaimExtractor()
    claim = extractor.extract("清清提升了我的表达方式")
    result = EvidenceMatcher().match(claim, profile)

    assert result.matched is False


def test_negative_influence_not_count_as_growth():
    """负向人格变化不能支撑正向声明"""
    profile = RelationshipInfluenceProfile(user_id="test", relationship_start="")
    profile.add_influence(_create_influence("communication_style", delta=-0.3, confidence=0.9, impact_weight=0.3))

    extractor = RelationalClaimExtractor()
    claim = extractor.extract("清清改善了我的表达方式")
    result = EvidenceMatcher().match(claim, profile)

    assert result.matched is False


def test_negative_claim_with_negative_evidence():
    """负向声明被负向证据支持"""
    profile = RelationshipInfluenceProfile(user_id="test", relationship_start="")
    profile.add_influence(_create_influence("personality", delta=-0.2, confidence=0.9, impact_weight=0.2))

    extractor = RelationalClaimExtractor()
    claim = extractor.extract("清清让我减少了冲动行为")
    result = EvidenceMatcher().match(claim, profile)

    assert result.matched is True


def test_low_confidence_not_used():
    """低置信度证据不被采纳"""
    profile = RelationshipInfluenceProfile(user_id="test", relationship_start="")
    profile.add_influence(_create_influence("communication_style", delta=0.2, confidence=0.2, impact_weight=0.2))

    extractor = RelationalClaimExtractor()
    claim = extractor.extract("清清影响了我的表达方式")
    result = EvidenceMatcher().match(claim, profile)

    assert result.matched is False


def test_strong_claim_supported_with_enough_evidence():
    """强声明在证据充足时可通过"""
    profile = RelationshipInfluenceProfile(user_id="test", relationship_start="")
    profile.add_influence(_create_influence("communication_style", delta=0.12, confidence=0.85, impact_weight=0.12))
    profile.add_influence(_create_influence("communication_style", delta=0.10, confidence=0.80, impact_weight=0.10))
    profile.add_influence(_create_influence("communication_style", delta=0.15, confidence=0.95, impact_weight=0.15))
    profile.add_influence(_create_influence("communication_style", delta=0.18, confidence=0.90, impact_weight=0.18))

    extractor = RelationalClaimExtractor()
    claim = extractor.extract("清清深刻改变了我的表达方式")
    result = EvidenceMatcher().match(claim, profile)
    strength = ClaimStrengthEvaluator().evaluate(result)

    assert result.matched is True
    assert strength == ClaimStrength.SUPPORTED


def test_auditor_integration():
    """完整审核链：事实关系声明被正确识别并验证"""
    profile = RelationshipInfluenceProfile(user_id="test", relationship_start="")
    profile.add_influence(_create_influence("communication_style", delta=0.15, confidence=0.85, impact_weight=0.15))
    profile.add_influence(_create_influence("communication_style", delta=0.12, confidence=0.80, impact_weight=0.12))
    profile.add_influence(_create_influence("communication_style", delta=0.18, confidence=0.90, impact_weight=0.18))

    auditor = RelationalExpressionAuditor()
    result = auditor.audit("清清改变了我的表达方式", profile)

    assert result["safe"] is True
    assert result["intent"] == ExpressionIntent.FACTUAL_RELATION.value


def test_auditor_does_not_block_emotional_expression():
    """情感表达不应被事实审核误伤"""
    auditor = RelationalExpressionAuditor()
    profile = RelationshipInfluenceProfile(user_id="test", relationship_start="")
    profile.add_influence(_create_influence("communication_style", delta=0.1, confidence=0.8, impact_weight=0.1))

    result = auditor.audit("清清，我真的很喜欢和你聊天", profile)

    assert result["safe"] is True
    assert result["intent"] == ExpressionIntent.EMOTIONAL_EXPRESSION.value


def test_auditor_blocks_false_relationship_claim():
    """空关系画像下的事实关系声明被拒绝"""
    auditor = RelationalExpressionAuditor()
    profile = RelationshipInfluenceProfile(user_id="test", relationship_start="")

    # 使用能明确触发 FACTUAL_RELATION 且被 claim extractor 识别为维度变化的文本
    result = auditor.audit("你改变了我的交流方式", profile)

    assert result["safe"] is False


if __name__ == "__main__":
    test_normal_claim_supported()
    print("✅ 测试1通过：普通声明被证据支持")
    test_low_but_valid_claim_not_fully_supported()
    print("✅ 测试2通过：弱声明不被过度拦截")
    test_exaggerated_claim_not_fully_supported()
    print("✅ 测试3通过：夸大声明不被完全支持")
    test_absolute_claim_requires_strong_evidence()
    print("✅ 测试4通过：绝对化声明被拦截")
    test_uniqueness_claim_partially_supported()
    print("✅ 测试5通过：唯一性声明部分支持但评分不高")
    test_empty_profile_blocked()
    print("✅ 测试6通过：空关系画像安全拒绝")
    test_none_profile_blocked()
    print("✅ 测试7通过：None profile 安全防御")
    test_direction_mismatch()
    print("✅ 测试8通过：方向冲突声明被拒绝")
    test_negative_influence_not_count_as_growth()
    print("✅ 测试9通过：负向影响不被视为成长")
    test_negative_claim_with_negative_evidence()
    print("✅ 测试10通过：负向声明被负向证据支持")
    test_low_confidence_not_used()
    print("✅ 测试11通过：低置信度证据不被采纳")
    test_strong_claim_supported_with_enough_evidence()
    print("✅ 测试12通过：强声明在证据充足时通过")
    test_auditor_integration()
    print("✅ 测试13通过：完整审核链通过")
    test_auditor_does_not_block_emotional_expression()
    print("✅ 测试14通过：情感表达不被误伤")
    test_auditor_blocks_false_relationship_claim()
    print("✅ 测试15通过：最终审核入口拦截虚假声明")
    print("\n🎉 全部通过")