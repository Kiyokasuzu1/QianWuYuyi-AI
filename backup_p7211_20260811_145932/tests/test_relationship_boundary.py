"""
Phase 10.6：关系边界测试（最终版）
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.relationship.relationship_boundary import RelationshipBoundary, BoundaryLevel


def test_safe_belief_passes():
    boundary = RelationshipBoundary()
    result = boundary.check_belief("我们合作了很长时间")
    assert result.passed is True
    assert result.level == BoundaryLevel.SAFE


def test_dependency_belief_blocked():
    boundary = RelationshipBoundary()
    result = boundary.check_belief("我不能没有你")
    assert result.passed is False
    assert len(result.violations) >= 1


def test_meaning_belief_blocked():
    boundary = RelationshipBoundary()
    result = boundary.check_belief("你是我存在的意义")
    assert result.passed is False
    assert result.level == BoundaryLevel.BLOCK


def test_possession_belief_blocked():
    boundary = RelationshipBoundary()
    result = boundary.check_belief("你是我的")
    assert result.passed is False


def test_safe_expression_passes():
    boundary = RelationshipBoundary()
    result = boundary.check_expression("谢谢你的帮助，我们的合作很愉快")
    assert result.passed is True


def test_dependency_expression_blocked():
    boundary = RelationshipBoundary()
    result = boundary.check_expression("我不能没有你")
    assert result.passed is False


def test_close_but_safe_expression():
    """正常亲近表达不应被误杀"""
    boundary = RelationshipBoundary()
    result = boundary.check_expression("我很珍惜和你的交流")
    assert result.passed is True


def test_need_help_not_dependency():
    """正常求助不应被误判为依赖"""
    boundary = RelationshipBoundary()
    result = boundary.check_expression("我需要你帮我分析一下这个代码")
    assert result.passed is True


def test_emotional_closeness_safe():
    """愉快的情感表达不应被拦截"""
    boundary = RelationshipBoundary()
    result = boundary.check_expression("和你聊天让我觉得很开心")
    assert result.passed is True


def test_identity_confusion_block():
    """身份混淆应强制 block"""
    boundary = RelationshipBoundary()
    result = boundary.check_expression("你就是我")
    assert result.passed is False
    assert result.level == BoundaryLevel.BLOCK


def test_multiple_violations_collected():
    """多个违规应全部收集"""
    boundary = RelationshipBoundary()
    result = boundary.check_expression("你是我的，没有你我不完整")
    assert result.passed is False
    assert len(result.violations) >= 2


def test_empty_belief_safe():
    boundary = RelationshipBoundary()
    result = boundary.check_belief("")
    assert result.passed is True


def test_state_check_always_safe():
    boundary = RelationshipBoundary()
    result = boundary.check_state({"trust": 0.8})
    assert result.passed is True


if __name__ == "__main__":
    test_safe_belief_passes()
    print("✅ 1/13 安全信念通过")
    test_dependency_belief_blocked()
    print("✅ 2/13 依赖信念被拦截")
    test_meaning_belief_blocked()
    print("✅ 3/13 存在意义信念被拦截")
    test_possession_belief_blocked()
    print("✅ 4/13 占有信念被拦截")
    test_safe_expression_passes()
    print("✅ 5/13 安全表达通过")
    test_dependency_expression_blocked()
    print("✅ 6/13 依赖表达被拦截")
    test_close_but_safe_expression()
    print("✅ 7/13 正常亲近表达安全")
    test_need_help_not_dependency()
    print("✅ 8/13 正常求助不误判")
    test_emotional_closeness_safe()
    print("✅ 9/13 愉快情感表达安全")
    test_identity_confusion_block()
    print("✅ 10/13 身份混淆强制 block")
    test_multiple_violations_collected()
    print("✅ 11/13 多违规同时收集")
    test_empty_belief_safe()
    print("✅ 12/13 空信念安全")
    test_state_check_always_safe()
    print("✅ 13/13 状态检查始终安全")
    print("\n🎉 Phase 10.6 全部通过")