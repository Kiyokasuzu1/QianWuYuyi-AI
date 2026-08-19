"""
特质状态更新器测试 v1.2
新增：重复执行防护测试
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.personality.trait_state_updater import TraitStateUpdater
from src.personality.trait_state import create_trait_state


def make_record(record_id="evo_test", approved=True, changes=None, confidence=0.9):
    """辅助创建 EvolutionRecord"""
    return {
        "record_id": record_id,
        "timestamp": "2026-07-25T00:00:00+00:00",
        "approved": approved,
        "trait_changes": changes or {},
        "confidence": confidence,
        "decision_reason": "test",
        "trigger_candidates": [],
        "source_growth_records": [],
        "rejection_reasons": {},
        "rejected_dimensions": [],
        "evolution_level": "trait_adjust",
        "requires_validation": False,
    }


def test_apply_approved_record():
    updater = TraitStateUpdater()
    states = {"creativity": create_trait_state("creativity", 0.5)}
    record = make_record("evo_001", True, {"creativity": {"before": 0.5, "after": 0.62, "delta": 0.12}})
    result = updater.apply(record, states)
    assert result["creativity"]["current_value"] == 0.62


def test_reject_unapproved_record():
    updater = TraitStateUpdater()
    states = {"creativity": create_trait_state("creativity", 0.5)}
    record = make_record("evo_002", False, {"creativity": {"before": 0.5, "after": 0.62, "delta": 0.12}})
    result = updater.apply(record, states)
    assert result["creativity"]["current_value"] == 0.5


def test_clamp_large_change():
    updater = TraitStateUpdater()
    states = {"creativity": create_trait_state("creativity", 0.5)}
    record = make_record("evo_003", True, {"creativity": {"before": 0.5, "after": 0.8, "delta": 0.3}})
    result = updater.apply(record, states)
    assert result["creativity"]["current_value"] == 0.65


def test_clamp_boundary():
    updater = TraitStateUpdater()
    states = {"creativity": create_trait_state("creativity", 0.95)}
    record = make_record("evo_004", True, {"creativity": {"before": 0.95, "after": 1.1, "delta": 0.15}})
    result = updater.apply(record, states)
    assert result["creativity"]["current_value"] <= 1.0


def test_update_stability_and_confidence():
    updater = TraitStateUpdater()
    states = {"creativity": create_trait_state("creativity", 0.5)}
    record = make_record("evo_005", True, {"creativity": {"before": 0.5, "after": 0.6, "delta": 0.1}})
    result = updater.apply(record, states)
    assert result["creativity"]["stability"] > 0.3
    assert result["creativity"]["confidence"] > 0.1


def test_evolution_history_recorded():
    updater = TraitStateUpdater()
    states = {"creativity": create_trait_state("creativity", 0.5)}
    record = make_record("evo_006", True, {"creativity": {"before": 0.5, "after": 0.62, "delta": 0.12}})
    result = updater.apply(record, states)
    history = result["creativity"].get("evolution_history", [])
    assert len(history) == 1
    assert history[0]["before"] == 0.5
    assert history[0]["after"] == 0.62
    assert history[0]["delta"] == 0.12


def test_duplicate_record_rejected():
    """重复执行同一记录应被拒绝"""
    updater = TraitStateUpdater()
    states = {"creativity": create_trait_state("creativity", 0.5)}
    record = make_record("evo_dup", True, {"creativity": {"before": 0.5, "after": 0.62, "delta": 0.12}})

    # 第一次执行
    updater.apply(record, states)
    assert states["creativity"]["current_value"] == 0.62

    # 第二次执行同一 record
    updater.apply(record, states)
    # 值不应再次变化
    assert states["creativity"]["current_value"] == 0.62


if __name__ == "__main__":
    test_apply_approved_record()
    print("✅ 测试1通过：审批通过的变化被写入")
    test_reject_unapproved_record()
    print("✅ 测试2通过：未通过审批的变化被拒绝")
    test_clamp_large_change()
    print("✅ 测试3通过：超过上限的变化被限制")
    test_clamp_boundary()
    print("✅ 测试4通过：最终值不超出 0~1 范围")
    test_update_stability_and_confidence()
    print("✅ 测试5通过：stability 和 confidence 提升")
    test_evolution_history_recorded()
    print("✅ 测试6通过：演化历史被记录")
    test_duplicate_record_rejected()
    print("✅ 测试7通过：重复执行同一记录被拒绝")
    print("\n🎉 全部通过")