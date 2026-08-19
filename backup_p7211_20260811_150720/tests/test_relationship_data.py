"""
Phase 10.1：关系系统数据结构测试（修正 signal_strength）
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.relationship.relationship_state import RelationshipState
from src.relationship.relationship_profile import RelationshipProfile
from src.relationship.relationship_event import RelationshipEvent
from src.relationship.relationship_change import RelationshipChange


def test_state_defaults():
    state = RelationshipState()
    assert state.familiarity == 0.0
    assert state.trust == 0.0
    assert state.collaboration == 0.0
    assert state.relationship_stage == "initial"


def test_state_clamp():
    state = RelationshipState(familiarity=1.5, trust=-0.3)
    assert state.familiarity == 1.0
    assert state.trust == 0.0


def test_state_serialization():
    original = RelationshipState(
        familiarity=0.5,
        trust=0.6,
        communication_style=["技术讨论", "深度架构"],
        relationship_stage="stable",
        last_interaction_at="2026-01-01",
    )
    data = original.to_dict()
    restored = RelationshipState.from_dict(data)
    assert restored.familiarity == 0.5
    assert restored.trust == 0.6
    assert restored.communication_style == ["技术讨论", "深度架构"]


def test_profile_candidate_and_confirmed():
    profile = RelationshipProfile(
        candidate_patterns=["用户可能偏好架构分析"],
        confirmed_patterns=["用户喜欢深入讨论设计"],
    )
    assert len(profile.candidate_patterns) == 1
    assert len(profile.confirmed_patterns) == 1


def test_event_potential_dimensions():
    """事件只声明潜在影响维度，使用 signal_strength 而非 confidence"""
    event = RelationshipEvent(
        event_type="collaboration",
        evidence_ids=["mem_001"],
        signal_strength=0.85,                              # 修正：confidence → signal_strength
        potential_dimensions={"collaboration", "trust"},
    )
    assert "collaboration" in event.potential_dimensions
    assert "trust" in event.potential_dimensions


def test_change_previous_new_value():
    change = RelationshipChange(
        dimension="trust",
        previous_value=0.61,
        new_value=0.64,
        delta=0.03,
        reason="长期稳定合作",
        confidence=0.82,
        evidence_ids=["mem_001"],
    )
    assert change.previous_value == 0.61
    assert change.new_value == 0.64
    assert change.delta == 0.03


def test_change_serialization():
    original = RelationshipChange(
        dimension="collaboration",
        previous_value=0.5,
        new_value=0.55,
        delta=0.05,
        reason="共同完成项目",
        confidence=0.9,
        evidence_ids=["mem_001", "mem_002"],
    )
    data = original.to_dict()
    assert data["dimension"] == "collaboration"
    assert data["previous_value"] == 0.5
    assert data["new_value"] == 0.55


if __name__ == "__main__":
    test_state_defaults()
    print("✅ 1/7 状态默认值")
    test_state_clamp()
    print("✅ 2/7 状态边界保护")
    test_state_serialization()
    print("✅ 3/7 状态序列化")
    test_profile_candidate_and_confirmed()
    print("✅ 4/7 Profile 候选/确认分离")
    test_event_potential_dimensions()
    print("✅ 5/7 事件潜在维度")
    test_change_previous_new_value()
    print("✅ 6/7 Change 前后值记录")
    test_change_serialization()
    print("✅ 7/7 Change 序列化")
    print("\n🎉 Phase 10.1 全部通过")