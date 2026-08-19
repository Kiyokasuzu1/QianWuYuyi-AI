"""
SelfModelV3 测试套件
覆盖构建、叙事来源、置信度过滤、安全过滤、继承、序列化
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.personality.self_model_v3 import SelfModelV3, NarrativeItem
from src.personality.self_model_builder_v3 import SelfModelBuilderV3
from src.reflection.reflection_record import ReflectionRecord


def make_reflection(ref_id, is_safe, confidence=0.8, causal_chain=None, new_beliefs=None,
                    level="observation", understanding=""):
    return ReflectionRecord(
        reflection_id=ref_id,
        timestamp="2026-01-01",
        is_safe=is_safe,
        confidence=confidence,
        causal_chain=causal_chain or [],
        new_beliefs=new_beliefs or [],
        reflection_level=level,
        current_understanding=understanding
    )


def test_builder_creates_model():
    builder = SelfModelBuilderV3()
    reflections = [
        make_reflection("r1", True, causal_chain=["经历: 用户鼓励表达", "维度变化: expressiveness提高"],
                        new_beliefs=["表达是安全的"], level="belief_change", understanding="我理解了表达不会带来冲突")
    ]
    model = builder.build("浅雾羽依", {"shyness": 0.6}, {"honesty": 0.9}, reflections)
    assert model.identity == "浅雾羽依"
    assert "表达是安全的" in model.beliefs
    assert len(model.narrative_items) == 1
    assert "expressiveness提高" in model.narrative_items[0].text
    assert model.narrative_items[0].source_ids == ["r1"]


def test_unsafe_reflections_excluded():
    builder = SelfModelBuilderV3()
    reflections = [
        make_reflection("r_bad", False, causal_chain=["依赖用户"], new_beliefs=["我没有用户不行"], level="belief_change")
    ]
    model = builder.build("羽依", {}, {}, reflections)
    assert model.beliefs == []
    assert model.narrative_items == []


def test_low_confidence_excluded():
    builder = SelfModelBuilderV3(min_confidence=0.5)
    reflections = [
        make_reflection("r_low", True, confidence=0.1, causal_chain=["经历: 某件事"],
                        new_beliefs=["可能有点用"], level="insight", understanding="好像有变化")
    ]
    model = builder.build("羽依", {}, {}, reflections)
    assert model.beliefs == []
    assert model.narrative_items == []


def test_narrative_accumulates_from_previous():
    builder = SelfModelBuilderV3(max_narratives=3)
    previous = SelfModelV3(
        identity="羽依",
        beliefs=["旧信念"],
        narrative_items=[NarrativeItem(text="以前的故事", source_ids=["r_old"])]
    )
    new_ref = make_reflection("r_new", True, causal_chain=["经历: 新事件"], level="insight", understanding="新的理解")
    model = builder.build("羽依", {}, {}, [new_ref], previous_model=previous)
    assert "旧信念" in model.beliefs
    assert len(model.narrative_items) == 2
    assert model.narrative_items[0].source_ids == ["r_old"]
    assert model.narrative_items[1].source_ids == ["r_new"]


def test_belief_limit():
    builder = SelfModelBuilderV3(max_beliefs=2)
    reflections = [
        make_reflection("r1", True, new_beliefs=["信念A"]),
        make_reflection("r2", True, new_beliefs=["信念B"]),
        make_reflection("r3", True, new_beliefs=["信念C"]),
    ]
    model = builder.build("羽依", {}, {}, reflections)
    assert len(model.beliefs) == 2
    assert "信念A" in model.beliefs
    assert "信念B" in model.beliefs


def test_prompt_context_includes_all():
    model = SelfModelV3(
        identity="浅雾羽依",
        traits={"openness": 0.7},
        beliefs=["表达是安全的"],
        narrative_items=[NarrativeItem(text="因为经历鼓励，我逐渐变得愿意表达", source_ids=["r1"])]
    )
    ctx = model.to_prompt_context()
    assert "浅雾羽依" in ctx
    assert "openness" in ctx
    assert "表达是安全的" in ctx
    assert "因为经历鼓励" in ctx


def test_narrative_source_tracking():
    builder = SelfModelBuilderV3()
    reflections = [
        make_reflection("ref_001", True, causal_chain=["经历: A"], level="insight"),
        make_reflection("ref_002", True, causal_chain=["经历: B"], level="belief_change", understanding="新的理解")
    ]
    model = builder.build("羽依", {}, {}, reflections)
    assert len(model.narrative_items) == 2
    assert model.narrative_items[0].source_ids == ["ref_001"]
    assert model.narrative_items[1].source_ids == ["ref_002"]


def test_serialization_roundtrip():
    original = SelfModelV3(
        identity="浅雾羽依",
        traits={"openness": 0.7},
        beliefs=["表达是安全的"],
        narrative_items=[NarrativeItem(text="叙事一", source_ids=["r1"])],
        last_updated="2026-01-01"
    )
    data = original.to_dict()
    restored = SelfModelV3.from_dict(data)
    assert restored.identity == original.identity
    assert restored.traits == original.traits
    assert restored.beliefs == original.beliefs
    assert len(restored.narrative_items) == 1
    assert restored.narrative_items[0].text == "叙事一"
    assert restored.narrative_items[0].source_ids == ["r1"]
    assert restored.last_updated == original.last_updated


if __name__ == "__main__":
    test_builder_creates_model()
    print("✅ 1/8 模型构建正确")
    test_unsafe_reflections_excluded()
    print("✅ 2/8 不安全反思被排除")
    test_low_confidence_excluded()
    print("✅ 3/8 低置信度反思被排除")
    test_narrative_accumulates_from_previous()
    print("✅ 4/8 旧模型叙事继承")
    test_belief_limit()
    print("✅ 5/8 信念数量限制")
    test_prompt_context_includes_all()
    print("✅ 6/8 Prompt 上下文生成")
    test_narrative_source_tracking()
    print("✅ 7/8 叙事来源追踪")
    test_serialization_roundtrip()
    print("✅ 8/8 序列化/反序列化正确")
    print("\n🎉 全部 SelfModelV3 测试通过")