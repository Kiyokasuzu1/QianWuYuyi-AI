"""
Phase 10.2.1：关系仓库测试（兼容性 + 新功能 + 隔离）— 覆盖版
修复 PersonalityInfluence 缺少 source_event_description 参数
"""
import sys, os, tempfile, shutil
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.relationship.relationship_repository import RelationshipRepository
from src.relationship.relationship_influence_profile import RelationshipInfluenceProfile
from src.relationship.relationship_state import RelationshipState
from src.relationship.relationship_cognitive_profile import RelationshipCognitiveProfile
from src.personality.personality_influence import PersonalityInfluence, InfluenceType


def test_phase7_save_load():
    """Phase 7 兼容：影响画像保存和加载"""
    base_dir = tempfile.mkdtemp()
    try:
        repo = RelationshipRepository(base_dir, user_id="test_user")
        profile = RelationshipInfluenceProfile(
            user_id="test_user",
            relationship_start="2026-01-01",
        )
        influence = PersonalityInfluence(
            influence_id="inf_001",
            timestamp="2026-01-01",
            source_event_id="evt_001",
            source_event_description="测试事件",
            affected_dimension="openness",
            before_value=0.5,
            after_value=0.55,
            delta=0.05,
            influence_type=InfluenceType.POSITIVE_GROWTH,
            impact_weight=0.8,
            confidence=0.9,
            evidence=["mem_001"],
        )
        profile.add_influence(influence)
        repo.save(profile)

        loaded = repo.load()
        assert loaded is not None
        assert loaded.user_id == "test_user"
        assert len(loaded.influences) == 1
        assert loaded.influences[0].affected_dimension == "openness"
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


def test_v10_save_load_state():
    """Phase 10：状态持久化"""
    base_dir = tempfile.mkdtemp()
    try:
        repo = RelationshipRepository(base_dir, user_id="user_a")
        state = RelationshipState(familiarity=0.5, trust=0.6, collaboration=0.7)
        repo.save_state(state)
        loaded = repo.load_state()
        assert loaded.familiarity == 0.5
        assert loaded.trust == 0.6
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


def test_v10_save_load_cognitive_profile():
    """Phase 10：认知档案持久化"""
    base_dir = tempfile.mkdtemp()
    try:
        repo = RelationshipRepository(base_dir, user_id="user_a")
        profile = RelationshipCognitiveProfile(
            confirmed_patterns=["偏好架构分析"],
            total_interactions=42,
        )
        repo.save_cognitive_profile(profile)
        loaded = repo.load_cognitive_profile()
        assert loaded.confirmed_patterns == ["偏好架构分析"]
        assert loaded.total_interactions == 42
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


def test_user_isolation():
    """多用户数据隔离：A 的状态不应影响 B"""
    base_dir = tempfile.mkdtemp()
    try:
        repo_a = RelationshipRepository(base_dir, user_id="user_a")
        repo_b = RelationshipRepository(base_dir, user_id="user_b")

        repo_a.save_state(RelationshipState(trust=0.9))
        repo_b.save_state(RelationshipState(trust=0.1))

        assert repo_a.load_state().trust == 0.9
        assert repo_b.load_state().trust == 0.1
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


def test_empty_state_returns_default():
    """无文件时返回默认状态"""
    base_dir = tempfile.mkdtemp()
    try:
        repo = RelationshipRepository(base_dir, user_id="new_user")
        state = repo.load_state()
        assert state.familiarity == 0.0
        assert state.trust == 0.0
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


def test_save_all_v10():
    """save_all_v10 / load_all_v10 一致性"""
    base_dir = tempfile.mkdtemp()
    try:
        repo = RelationshipRepository(base_dir, user_id="user_c")
        state = RelationshipState(collaboration=0.8)
        profile = RelationshipCognitiveProfile(total_interactions=10)
        repo.save_all_v10(state, profile)

        loaded_state, loaded_profile = repo.load_all_v10()
        assert loaded_state.collaboration == 0.8
        assert loaded_profile.total_interactions == 10
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


def test_old_and_new_coexist():
    """旧影响画像和新认知档案共存，互不干扰"""
    base_dir = tempfile.mkdtemp()
    try:
        repo = RelationshipRepository(base_dir, user_id="dual_user")

        # 保存旧数据
        influence_profile = RelationshipInfluenceProfile(
            user_id="dual_user",
            relationship_start="2026-01-01",
        )
        # 修复：补充 source_event_description
        influence_profile.add_influence(PersonalityInfluence(
            influence_id="inf_001",
            timestamp="",
            source_event_id="",
            source_event_description="测试事件",
            affected_dimension="openness",
            before_value=0.5,
            after_value=0.55,
            delta=0.05,
            influence_type=InfluenceType.POSITIVE_GROWTH,
        ))
        repo.save(influence_profile)

        # 保存新数据
        state = RelationshipState(trust=0.7)
        repo.save_state(state)

        # 验证旧数据
        loaded_influence = repo.load()
        assert loaded_influence is not None
        assert len(loaded_influence.influences) == 1

        # 验证新数据
        loaded_state = repo.load_state()
        assert loaded_state.trust == 0.7
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


if __name__ == "__main__":
    test_phase7_save_load()
    print("✅ 1/7 Phase 7 兼容")
    test_v10_save_load_state()
    print("✅ 2/7 状态持久化")
    test_v10_save_load_cognitive_profile()
    print("✅ 3/7 认知档案持久化")
    test_user_isolation()
    print("✅ 4/7 多用户隔离")
    test_empty_state_returns_default()
    print("✅ 5/7 空文件默认值")
    test_save_all_v10()
    print("✅ 6/7 save_all/load_all 一致性")
    test_old_and_new_coexist()
    print("✅ 7/7 新旧系统共存")
    print("\n🎉 Phase 10.2.1 全部通过")