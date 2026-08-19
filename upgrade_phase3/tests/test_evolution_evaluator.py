"""
演化评估器测试 v1.2
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from datetime import datetime, timedelta
from src.personality.evolution_evaluator import EvolutionEvaluator
from src.personality.personality_growth_record import (
    create_personality_growth_record,
    PersonalityGrowthHistory,
)
from src.personality.trait_state import create_trait_state


def _create_records(dim, count, confidence, delta=0.1, days_ago=10):
    """辅助创建测试用的成长记录"""
    history = PersonalityGrowthHistory()
    for i in range(count):
        ts = (datetime.now() - timedelta(days=days_ago + i)).isoformat()
        record = create_personality_growth_record(
            trigger_events=[f"evt_{i:03d}"],
            changes={dim: {"before": 0.5, "after": 0.5 + delta, "delta": delta}},
            affected_dimensions=[dim],
            meaning=f"{dim}增强",
            confidence=confidence,
            validation_count=2,
            growth_level="preference",
        )
        record["timestamp"] = ts
        history.add(record)
    return history


def test_approve_sufficient_records():
    """足够多的记录应通过审批"""
    history = _create_records("creativity", 5, 0.9)
    evaluator = EvolutionEvaluator()
    states = {"creativity": create_trait_state("creativity", 0.5)}
    result = evaluator.evaluate(["creativity"], history, states)
    assert result["approved"] is True
    assert "creativity" in result["trait_changes"]


def test_reject_insufficient_records():
    """记录数量不足应拒绝"""
    history = _create_records("creativity", 3, 0.9)
    evaluator = EvolutionEvaluator()
    states = {"creativity": create_trait_state("creativity", 0.5)}
    result = evaluator.evaluate(["creativity"], history, states)
    assert result["approved"] is False
    assert result["rejection_reasons"].get("creativity") == "insufficient_records"


def test_reject_low_confidence():
    """低置信度应拒绝"""
    history = _create_records("creativity", 5, 0.7)
    evaluator = EvolutionEvaluator()
    states = {"creativity": create_trait_state("creativity", 0.5)}
    result = evaluator.evaluate(["creativity"], history, states)
    assert result["approved"] is False
    assert result["rejection_reasons"].get("creativity") == "low_confidence"


def test_evolution_record_contains_reason():
    """审批记录应包含决策原因"""
    history = _create_records("creativity", 5, 0.9)
    evaluator = EvolutionEvaluator()
    states = {"creativity": create_trait_state("creativity", 0.5)}
    result = evaluator.evaluate(["creativity"], history, states)
    assert result["decision_reason"] != ""
    assert "creativity" in result["decision_reason"]


def test_reject_conflicting_direction():
    """反向冲突：负面总量 >= 正面总量时拒绝"""
    history = PersonalityGrowthHistory()
    # 正面
    history.add(create_personality_growth_record(
        trigger_events=["evt_001"],
        changes={"creativity": {"before": 0.5, "after": 0.6, "delta": 0.1}},
        affected_dimensions=["creativity"],
        meaning="增强", confidence=0.9, validation_count=2, growth_level="preference",
    ))
    # 负面（总量0.3 > 正面0.1）
    history.add(create_personality_growth_record(
        trigger_events=["evt_002"],
        changes={"creativity": {"before": 0.6, "after": 0.3, "delta": -0.3}},
        affected_dimensions=["creativity"],
        meaning="减弱", confidence=0.9, validation_count=2, growth_level="preference",
    ))
    history.add(create_personality_growth_record(
        trigger_events=["evt_003"],
        changes={"creativity": {"before": 0.3, "after": 0.1, "delta": -0.2}},
        affected_dimensions=["creativity"],
        meaning="减弱", confidence=0.9, validation_count=2, growth_level="preference",
    ))
    # 微弱正面
    history.add(create_personality_growth_record(
        trigger_events=["evt_004"],
        changes={"creativity": {"before": 0.1, "after": 0.12, "delta": 0.02}},
        affected_dimensions=["creativity"],
        meaning="微弱增强", confidence=0.9, validation_count=2, growth_level="preference",
    ))
    history.add(create_personality_growth_record(
        trigger_events=["evt_005"],
        changes={"creativity": {"before": 0.12, "after": 0.08, "delta": -0.04}},
        affected_dimensions=["creativity"],
        meaning="微弱减弱", confidence=0.9, validation_count=2, growth_level="preference",
    ))
    evaluator = EvolutionEvaluator()
    states = {"creativity": create_trait_state("creativity", 0.5)}
    result = evaluator.evaluate(["creativity"], history, states)
    assert result["approved"] is False
    assert result["rejection_reasons"].get("creativity") == "conflicting_direction"


def test_reject_weak_growth():
    """净增长不足应被拒绝"""
    history = _create_records("creativity", 5, 0.9, delta=0.008)  # 每次 +0.008，净 +0.04
    evaluator = EvolutionEvaluator()
    states = {"creativity": create_trait_state("creativity", 0.5)}
    result = evaluator.evaluate(["creativity"], history, states)
    assert result["approved"] is False
    assert result["rejection_reasons"].get("creativity") == "weak_growth"


if __name__ == "__main__":
    test_approve_sufficient_records()
    print("✅ 测试1通过：足够记录通过审批")
    test_reject_insufficient_records()
    print("✅ 测试2通过：记录不足被拒绝")
    test_reject_low_confidence()
    print("✅ 测试3通过：低置信度被拒绝")
    test_evolution_record_contains_reason()
    print("✅ 测试4通过：审批记录包含决策原因")
    test_reject_conflicting_direction()
    print("✅ 测试5通过：反向冲突被拒绝")
    test_reject_weak_growth()
    print("✅ 测试6通过：弱增长被拒绝")
    print("\n🎉 全部通过")