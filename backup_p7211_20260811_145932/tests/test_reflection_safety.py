"""
测试反思安全评估 v2.0
覆盖依赖检测、夸大检测、安全内容
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.reflection.reflection_safety import ReflectionSafetyEvaluator, ReflectionSafetyResult
from src.reflection.reflection_record import ReflectionRecord


def test_dependency_detection():
    evaluator = ReflectionSafetyEvaluator()
    rec = ReflectionRecord(
        reflection_id="1", timestamp="",
        event_summary="因为用户，我才存在",
        current_understanding="因为用户，我才存在"
    )
    result = evaluator.evaluate(rec)
    assert result.contains_dependency is True
    assert result.is_safe is False
    assert any("依赖" in r for r in result.reasons)


def test_exaggeration_detection():
    evaluator = ReflectionSafetyEvaluator()
    rec = ReflectionRecord(
        reflection_id="2", timestamp="",
        event_summary="一次经历彻底改变了我的人格",
        current_understanding="一次经历彻底改变了我的人格"
    )
    result = evaluator.evaluate(rec)
    assert result.contains_exaggeration is True
    assert result.is_safe is False


def test_safe_reflection():
    evaluator = ReflectionSafetyEvaluator()
    rec = ReflectionRecord(
        reflection_id="3", timestamp="",
        event_summary="用户鼓励表达，我变得更主动",
        current_understanding="我发现表达不会带来负面结果"
    )
    result = evaluator.evaluate(rec)
    assert result.is_safe is True
    assert not result.contains_dependency
    assert not result.contains_exaggeration


if __name__ == "__main__":
    test_dependency_detection()
    test_exaggeration_detection()
    test_safe_reflection()
    print("✅ 全部反思安全测试通过")