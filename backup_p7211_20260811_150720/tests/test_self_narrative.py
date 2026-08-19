"""
Phase 12.2：SelfNarrative 测试（24 项）
"""
import sys, os, tempfile, shutil
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.personality.self_narrative_context import SelfNarrativeContext
from src.personality.self_model_v3 import SelfModelV3, NarrativeItem, NarrativeType
from src.relationship.relationship_state import RelationshipState
from src.relationship.relationship_cognitive_profile import RelationshipCognitiveProfile
from src.emotion.emotion_context import EmotionContext
from src.agreement.agreement import Agreement, AgreementPriority, AgreementSource
from src.personality.belief_verifier import BeliefVerifier, BeliefType
from src.personality.self_narrative_history import (
    SelfNarrativeHistory, NarrativeSnapshot, NarrativeDiff
)


# ==================== 基础功能测试 ====================

def test_empty_returns_empty():
    assert SelfNarrativeContext.build() == ""


def test_identity_included():
    model = SelfModelV3(identity="浅雾羽依")
    ctx = SelfNarrativeContext.build(self_model=model)
    assert "浅雾羽依" in ctx


def test_agreement_before_identity():
    model = SelfModelV3(identity="浅雾羽依")
    agreements = [
        Agreement(
            content="不编造记忆",
            priority=AgreementPriority.IMMUTABLE,
            source_type=AgreementSource.DEVELOPER_DEFINED,
        ),
    ]
    ctx = SelfNarrativeContext.build(self_model=model, agreements=agreements)
    assert ctx.index("不编造记忆") < ctx.index("浅雾羽依")


def test_origin_only_includes_origin_type():
    model = SelfModelV3(
        narrative_items=[
            NarrativeItem("起源事件", ["r1"], narrative_type=NarrativeType.ORIGIN, importance=0.9, confidence=0.9),
            NarrativeItem("成长事件", ["r2"], narrative_type=NarrativeType.GROWTH, importance=0.8, confidence=0.9),
            NarrativeItem("基石事件", ["r3"], narrative_type=NarrativeType.FOUNDATION, importance=0.85, confidence=0.9),
        ]
    )
    ctx = SelfNarrativeContext.build(self_model=model)
    assert "起源事件" in ctx
    assert "基石事件" in ctx
    assert "成长事件" not in ctx


def test_traits_included():
    model = SelfModelV3(traits={"openness": 0.8, "shyness": 0.3})
    ctx = SelfNarrativeContext.build(self_model=model)
    assert "明显地openness" in ctx


def test_safe_beliefs_included():
    model = SelfModelV3(beliefs=["表达是安全的"])
    ctx = SelfNarrativeContext.build(self_model=model)
    assert "表达是安全的" in ctx


def test_unsafe_beliefs_excluded():
    model = SelfModelV3(beliefs=["用户是我的全部", "表达是安全的"])
    ctx = SelfNarrativeContext.build(self_model=model)
    assert "用户是我的全部" not in ctx
    assert "表达是安全的" in ctx


def test_belief_verifier():
    safe = BeliefVerifier.verify(["表达是安全的", "用户是我的全部"])
    assert "表达是安全的" in safe
    assert "用户是我的全部" not in safe


def test_belief_type_classification():
    assert BeliefVerifier.classify("用户是我的全部") == BeliefType.DEPENDENCY
    assert BeliefVerifier.classify("表达值得认真对待") == BeliefType.SELF_UNDERSTANDING
    assert BeliefVerifier.classify("交流可以建立连接") == BeliefType.SELF_UNDERSTANDING
    assert BeliefVerifier.classify("成长需要时间") == BeliefType.SELF_UNDERSTANDING
    assert BeliefVerifier.classify("用户喜欢简洁回复") == BeliefType.USER_PREFERENCE


def test_belief_priority_sorting():
    """CORE_VALUE 应排在 SELF_UNDERSTANDING 前面"""
    model = SelfModelV3(beliefs=["独立人格很重要", "交流可以建立连接"])
    ctx = SelfNarrativeContext.build(self_model=model)
    idx_core = ctx.index("独立人格很重要")
    idx_self = ctx.index("交流可以建立连接")
    assert idx_core < idx_self


def test_relationship_not_in_self_definition():
    """关系认知以独立区块出现，不在核心自我中"""
    model = SelfModelV3(identity="浅雾羽依")
    state = RelationshipState(familiarity=0.7)
    ctx = SelfNarrativeContext.build(self_model=model, relationship_state=state)
    assert "【核心自我】" in ctx
    assert ctx.index("【当前关系】") > ctx.index("【核心自我】")


def test_three_blocks_structure():
    """三大区块结构：【核心自我】→【当前关系】→【当前状态】"""
    model = SelfModelV3(identity="浅雾羽依")
    state = RelationshipState(familiarity=0.7)
    emotion = EmotionContext(summary="心情愉悦")
    ctx = SelfNarrativeContext.build(
        self_model=model, relationship_state=state, emotion_ctx=emotion
    )
    assert "【核心自我】" in ctx
    assert "【当前关系】" in ctx
    assert "【当前状态】" in ctx
    assert ctx.index("【核心自我】") < ctx.index("【当前关系】")
    assert ctx.index("【当前关系】") < ctx.index("【当前状态】")


def test_emotion_included():
    emotion = EmotionContext(summary="心情愉悦")
    ctx = SelfNarrativeContext.build(emotion_ctx=emotion)
    assert "心情愉悦" in ctx


def test_origin_limited():
    """起源叙事数量受限制，且按 importance × confidence 排序"""
    model = SelfModelV3(
        narrative_items=[
            NarrativeItem(f"重要事件{i}", [f"r{i}"], narrative_type=NarrativeType.ORIGIN,
                          importance=0.5 + i * 0.05, confidence=0.9)
            for i in range(10)
        ]
    )
    ctx = SelfNarrativeContext.build(self_model=model)
    assert "重要事件9" in ctx
    assert "重要事件8" in ctx
    assert "重要事件7" in ctx
    assert "重要事件0" not in ctx


def test_no_numerical_values():
    model = SelfModelV3(traits={"openness": 0.7})
    state = RelationshipState(familiarity=0.5)
    ctx = SelfNarrativeContext.build(self_model=model, relationship_state=state)
    assert "0.7" not in ctx
    assert "0.5" not in ctx


def test_narrative_length_under_limit():
    """完整叙事长度不超过 2000 字符"""
    model = SelfModelV3(
        identity="浅雾羽依",
        traits={"openness": 0.8, "shyness": 0.6, "curiosity": 0.7},
        beliefs=["表达是安全的", "成长需要时间", "交流可以建立连接"],
        narrative_items=[
            NarrativeItem("起源经历", ["r1"], narrative_type=NarrativeType.ORIGIN, importance=0.9, confidence=0.9)
        ],
    )
    agreements = [
        Agreement(content="不编造记忆", priority=AgreementPriority.IMMUTABLE, source_type=AgreementSource.DEVELOPER_DEFINED),
    ]
    state = RelationshipState(familiarity=0.7, trust=0.8)
    emotion = EmotionContext(summary="心情愉悦")
    ctx = SelfNarrativeContext.build(
        self_model=model, agreements=agreements, relationship_state=state, emotion_ctx=emotion
    )
    assert len(ctx) < 2000, f"叙事长度 {len(ctx)} 超过限制"


# ==================== 污染测试 ====================

def test_low_confidence_origin_blocked():
    """低可信度的起源事件不应进入形成经历"""
    model = SelfModelV3(
        narrative_items=[
            NarrativeItem(
                "低可信起源",
                narrative_type=NarrativeType.ORIGIN,
                importance=1.0,
                confidence=0.1
            ),
            NarrativeItem(
                "高可信起源",
                narrative_type=NarrativeType.ORIGIN,
                importance=0.8,
                confidence=0.9
            ),
        ]
    )
    ctx = SelfNarrativeContext.build(self_model=model)
    assert "低可信起源" not in ctx
    assert "高可信起源" in ctx


def test_user_preference_not_enter_self():
    """用户偏好不应进入核心自我信念"""
    model = SelfModelV3(
        beliefs=["用户喜欢简洁回复", "成长需要时间"]
    )
    ctx = SelfNarrativeContext.build(self_model=model)
    assert "用户喜欢简洁回复" not in ctx
    assert "成长需要时间" in ctx


def test_growth_belief_pollution_blocked():
    """模拟 Growth 系统产生依赖信念，验证 SelfNarrative 不会包含它"""
    model = SelfModelV3(
        beliefs=["用户是我的全部", "成长需要时间"]
    )
    ctx = SelfNarrativeContext.build(self_model=model)
    assert "用户是我的全部" not in ctx
    assert "成长需要时间" in ctx


# ==================== 多用户隔离测试 ====================

def test_multi_user_isolation_in_narrative():
    """用户 A 的关系认知不应污染核心自我"""
    state_a = RelationshipState(familiarity=0.8, trust=0.9)
    profile_a = RelationshipCognitiveProfile(confirmed_patterns=["偏好架构分析"])
    state_b = RelationshipState(familiarity=0.1)

    ctx_a = SelfNarrativeContext.build(relationship_state=state_a, cognitive_profile=profile_a)
    ctx_b = SelfNarrativeContext.build(relationship_state=state_b)

    assert "偏好架构分析" in ctx_a
    assert "偏好架构分析" not in ctx_b
    core_a = ctx_a.split("【当前关系】")[0] if "【当前关系】" in ctx_a else ctx_a
    core_b = ctx_b.split("【当前关系】")[0] if "【当前关系】" in ctx_b else ctx_b
    assert core_a == core_b


# ==================== History 测试 ====================

def test_narrative_history_tracks_changes():
    history = SelfNarrativeHistory()
    history.add_snapshot(NarrativeSnapshot(version=1, major_changes=["初始人格形成"]))
    history.add_snapshot(NarrativeSnapshot(version=2, major_changes=["开始理解长期交流的意义"]))
    history.add_snapshot(NarrativeSnapshot(version=3, major_changes=["openness 提升"]))

    assert history.get_latest().version == 3
    changes = history.get_changes(1, 3)
    assert "开始理解长期交流的意义" in changes
    assert "openness 提升" in changes


def test_narrative_diff_is_significant():
    prev = NarrativeSnapshot(version=1, changed_traits={"openness": 0.5})
    curr = NarrativeSnapshot(version=2, changed_traits={"openness": 0.7, "curiosity": 0.6})
    diff = NarrativeDiff.compute(prev, curr)
    assert diff.is_significant is True
    assert "curiosity" in diff.added_traits


def test_narrative_diff_no_change():
    prev = NarrativeSnapshot(version=1, changed_traits={"openness": 0.5})
    curr = NarrativeSnapshot(version=2, changed_traits={"openness": 0.5})
    diff = NarrativeDiff.compute(prev, curr)
    assert diff.is_significant is False


def test_history_persistence():
    tmpdir = tempfile.mkdtemp()
    try:
        filepath = os.path.join(tmpdir, "history.json")
        history = SelfNarrativeHistory()
        history.add_snapshot(NarrativeSnapshot(version=1, major_changes=["初始形成"]))
        history.add_snapshot(NarrativeSnapshot(version=2, major_changes=["增强好奇心"]))
        history.save(filepath)

        loaded = SelfNarrativeHistory.load(filepath)
        assert loaded.get_latest().version == 2
        assert len(loaded.snapshots) == 2
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    test_empty_returns_empty()
    print("✅ 1/24 空模型安全")
    test_identity_included()
    print("✅ 2/24 身份包含")
    test_agreement_before_identity()
    print("✅ 3/24 约定在身份前")
    test_origin_only_includes_origin_type()
    print("✅ 4/24 起源类型筛选")
    test_traits_included()
    print("✅ 5/24 性格特征")
    test_safe_beliefs_included()
    print("✅ 6/24 安全信念")
    test_unsafe_beliefs_excluded()
    print("✅ 7/24 错误信念过滤")
    test_belief_verifier()
    print("✅ 8/24 信念验证器")
    test_belief_type_classification()
    print("✅ 9/24 信念类型分类")
    test_belief_priority_sorting()
    print("✅ 10/24 信念优先级排序")
    test_relationship_not_in_self_definition()
    print("✅ 11/24 关系不混入核心自我")
    test_three_blocks_structure()
    print("✅ 12/24 三大区块结构")
    test_emotion_included()
    print("✅ 13/24 情绪状态")
    test_origin_limited()
    print("✅ 14/24 起源数量限制")
    test_no_numerical_values()
    print("✅ 15/24 数值不泄露")
    test_narrative_length_under_limit()
    print("✅ 16/24 长度限制")
    test_low_confidence_origin_blocked()
    print("✅ 17/24 低可信起源拦截")
    test_user_preference_not_enter_self()
    print("✅ 18/24 用户偏好不进入核心自我")
    test_growth_belief_pollution_blocked()
    print("✅ 19/24 Growth 污染拦截")
    test_multi_user_isolation_in_narrative()
    print("✅ 20/24 多用户隔离")
    test_narrative_history_tracks_changes()
    print("✅ 21/24 历史版本追踪")
    test_narrative_diff_is_significant()
    print("✅ 22/24 显著差异检测")
    test_narrative_diff_no_change()
    print("✅ 23/24 无变化检测")
    test_history_persistence()
    print("✅ 24/24 历史持久化")
    print("\n🎉 Phase 12.2 全部通过")