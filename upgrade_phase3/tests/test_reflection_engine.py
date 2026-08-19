"""
反思引擎测试 v3.1
覆盖候选生成、记录生成、深度评估、安全评估、因果链传递
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.reflection.reflection_engine import (
    ReflectionEngine,
    RuleBasedCandidateGenerator,
)
from src.reflection.reflection_evaluator import ReflectionEvaluator
from src.reflection.reflection_safety import ReflectionSafetyEvaluator
from src.reflection.reflection_record import ReflectionLevel


class MockTraitChange:
    def __init__(self, dimension: str, delta: float):
        self.dimension = dimension
        self.delta = delta


class MockGrowthEvent:
    def __init__(self, id: str, description: str, trait_changes: list, significance: float = 0.5):
        self.id = id
        self.description = description
        self.trait_changes = trait_changes
        self.significance = significance


def test_rule_based_candidate_generation():
    gen = RuleBasedCandidateGenerator()
    events = [
        MockGrowthEvent("ev1", "用户耐心倾听",
                        [MockTraitChange("trust", 0.1), MockTraitChange("openness", 0.2)],
                        significance=0.6),
        MockGrowthEvent("ev2", "无变化事件", [], significance=0.3),
    ]
    cands = gen.generate(events)
    assert len(cands) == 1
    cand = cands[0]
    assert "trust提高" in cand.possible_changes
    assert "openness提高" in cand.possible_changes
    assert len(cand.causal_chain) == 3
    assert cand.causal_chain[0].startswith("经历:")


def test_engine_produces_records_with_safety():
    gen = RuleBasedCandidateGenerator()
    evaluator = ReflectionEvaluator()
    safety = ReflectionSafetyEvaluator()
    engine = ReflectionEngine(gen, evaluator, safety)

    events = [
        MockGrowthEvent("ev1", "用户鼓励表达",
                        [MockTraitChange("expressiveness", 0.3)],
                        significance=0.8)
    ]
    records = engine.process_events(events)
    assert len(records) == 1
    rec = records[0]
    assert rec.source_event_ids == ["ev1"]
    assert rec.self_change == ["expressiveness提高"]
    assert rec.reflection_level == ReflectionLevel.INSIGHT.value
    assert rec.is_safe is True


def test_deep_reflection_belief_change():
    evaluator = ReflectionEvaluator()
    from src.reflection.reflection_record import ReflectionRecord

    record = ReflectionRecord(
        reflection_id="test",
        timestamp="2026-01-01",
        previous_self_view="我认为主动表达会带来麻烦",
        current_understanding="现在我理解主动表达反而可以建立连接",
        new_beliefs=[],
        reflection_level=ReflectionLevel.OBSERVATION.value
    )
    depth = evaluator.evaluate(record)
    assert depth == ReflectionLevel.BELIEF_CHANGE


def test_safety_evaluator_integration():
    safety = ReflectionSafetyEvaluator()
    from src.reflection.reflection_record import ReflectionRecord
    rec = ReflectionRecord(
        reflection_id="dep",
        timestamp="",
        event_summary="因为用户，我才存在",
        current_understanding="因为用户，我才存在"
    )
    result = safety.evaluate(rec)
    assert result.contains_dependency is True
    assert result.is_safe is False
    assert any("依赖" in r for r in result.reasons)


def test_causal_chain_preserved_in_record():
    gen = RuleBasedCandidateGenerator()
    evaluator = ReflectionEvaluator()
    safety = ReflectionSafetyEvaluator()
    engine = ReflectionEngine(gen, evaluator, safety)

    events = [
        MockGrowthEvent("ev1", "用户持续鼓励表达",
                        [MockTraitChange("expressiveness", 0.3)],
                        significance=0.7)
    ]
    records = engine.process_events(events)
    rec = records[0]
    assert len(rec.causal_chain) > 0
    assert any("维度变化" in step for step in rec.causal_chain)


if __name__ == "__main__":
    test_rule_based_candidate_generation()
    print("✅ 1/5 候选生成正常")
    test_engine_produces_records_with_safety()
    print("✅ 2/5 引擎生成记录并安全评估")
    test_deep_reflection_belief_change()
    print("✅ 3/5 深度评估检测信念转变")
    test_safety_evaluator_integration()
    print("✅ 4/5 安全评估集成")
    test_causal_chain_preserved_in_record()
    print("✅ 5/5 因果链完整传递")
    print("\n🎉 全部 ReflectionEngine 测试通过")