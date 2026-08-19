"""
测试 ReflectionRecord 序列化 / 反序列化一致性
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.reflection.reflection_record import ReflectionRecord


def test_serialization_roundtrip():
    record = ReflectionRecord(
        reflection_id="test_serial",
        timestamp="2026-07-25T00:00:00",
        source_event_ids=["ev1", "ev2"],
        event_summary="总结",
        previous_self_view="过去",
        current_understanding="现在",
        self_change=["变化1"],
        new_beliefs=["信念1"],
        causal_chain=["原因1", "原因2"],
        reflection_level="insight",
        confidence=0.85,
        is_safe=True,
        contains_dependency=False,
        contains_exaggeration=False
    )

    data = record.to_dict()
    restored = ReflectionRecord.from_dict(data)

    assert restored.reflection_id == record.reflection_id
    assert restored.causal_chain == record.causal_chain
    assert restored.self_change == record.self_change
    assert restored.new_beliefs == record.new_beliefs
    assert restored.confidence == 0.85
    assert restored.content == record.content


if __name__ == "__main__":
    test_serialization_roundtrip()
    print("✅ 序列化测试通过")