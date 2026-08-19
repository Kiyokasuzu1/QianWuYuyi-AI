"""
价值观系统测试 v1.0
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.personality.value_system import ValueSystem


def test_initialization():
    """初始化应有 5 个核心价值观"""
    system = ValueSystem()
    assert len(system.values) == 5


def test_weight_adjust():
    """权重调整应生效"""
    system = ValueSystem()
    old = system.get_weight("truth_over_perfection")
    result = system.adjust_weight(
        "truth_over_perfection", 0.1, "长期反思", "reflection_engine"
    )
    assert result is True
    new = system.get_weight("truth_over_perfection")
    assert new > old


def test_unauthorized_source_rejected():
    """未授权来源应被拒绝"""
    system = ValueSystem()
    result = system.adjust_weight(
        "truth_over_perfection", 0.1, "hack", "chat_input"
    )
    assert result is False


def test_history_recorded():
    """调整历史应被记录"""
    system = ValueSystem()
    system.adjust_weight(
        "truth_over_perfection", 0.1, "长期反思", "reflection_engine"
    )
    profile = system.get_profile("truth_over_perfection")
    assert len(profile["adjustment_history"]) == 1
    assert profile["adjustment_history"][0]["reason"] == "长期反思"


def test_active_conflicts():
    """冲突检测应返回结果"""
    system = ValueSystem()
    # 默认权重 0.7，两个冲突应该都是活跃的
    conflicts = system.get_active_conflicts()
    assert len(conflicts) >= 1
    assert "resolution_pattern" in conflicts[0]


def test_dominant_values():
    """主导价值观应返回前3个"""
    system = ValueSystem()
    dominants = system.get_dominant_values()
    assert len(dominants) == 3


def test_weight_clamped_to_min():
    """权重不应低于最小值 0.3"""
    system = ValueSystem()
    system.adjust_weight(
        "truth_over_perfection", -1.0, "极限测试", "system_init"
    )
    assert system.get_weight("truth_over_perfection") >= 0.3


if __name__ == "__main__":
    test_initialization()
    print("✅ 测试1通过：初始化 5 个核心价值观")
    test_weight_adjust()
    print("✅ 测试2通过：权重调整生效")
    test_unauthorized_source_rejected()
    print("✅ 测试3通过：未授权来源被拒绝")
    test_history_recorded()
    print("✅ 测试4通过：调整历史被记录")
    test_active_conflicts()
    print("✅ 测试5通过：冲突检测正常")
    test_dominant_values()
    print("✅ 测试6通过：主导价值观返回前3个")
    test_weight_clamped_to_min()
    print("✅ 测试7通过：权重不低于 0.3")
    print("\n🎉 全部通过")