"""
E-Emotion-6 测试 1/5：情绪状态保存与恢复（EmotionState 2.0）
覆盖任务书验收项：情绪状态保存恢复
- 新增 5 维度默认值/边界保护
- 版本信封（schema_version=2）
- 旧数据 from_dict 自动补默认值（版本兼容）
- snapshot/restore（含 JSON 序列化往返 = 持久化保存恢复）
"""
import sys, os, json
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest

from src.emotion.emotion_state import EmotionState, SCHEMA_VERSION
from src.emotion.emotion_delta import EmotionDelta


def test_new_dimension_defaults():
    """11 维默认值：旧 6 维不变，新 5 维有合理基线"""
    state = EmotionState()
    assert state.valence == 0.0
    assert state.arousal == 0.5
    assert state.stability == 0.7
    assert state.happiness == 0.5
    assert state.sadness == 0.0
    assert state.trust == 0.5
    assert state.attachment == 0.5


def test_new_dimension_clamp():
    """新维度创建时边界保护"""
    state = EmotionState(stability=5.0, happiness=-1.0, sadness=2.0, trust=-0.5, attachment=3.0)
    assert state.stability == 1.0
    assert state.happiness == 0.0
    assert state.sadness == 1.0
    assert state.trust == 0.0
    assert state.attachment == 1.0


def test_apply_delta_covers_new_dimensions():
    """apply_delta 应用 11 维变化且原状态不变"""
    state = EmotionState(happiness=0.4, trust=0.5, sadness=0.1)
    delta = EmotionDelta(happiness=0.3, trust=0.05, sadness=-0.1, stability=-0.2)
    new_state = state.apply_delta(delta)

    assert new_state.happiness == 0.7
    assert new_state.trust == 0.55
    assert new_state.sadness == 0.0
    assert new_state.stability == pytest.approx(0.5, abs=1e-9)
    assert state.happiness == 0.4
    assert state.trust == 0.5


def test_to_dict_has_version_envelope():
    """to_dict 携带 schema_version=2 与全部 11 维"""
    data = EmotionState().to_dict()
    assert data["schema_version"] == SCHEMA_VERSION
    for dim in (
        "valence", "arousal", "curiosity", "anxiety", "confidence", "energy",
        "stability", "happiness", "sadness", "trust", "attachment",
    ):
        assert dim in data


def test_from_dict_old_data_gets_defaults():
    """旧版本数据（缺新维度）from_dict 自动补默认值（版本兼容）"""
    old_data = {"valence": 0.4, "arousal": 0.6, "updated_at": "2026-01-01T00:00:00"}
    state = EmotionState.from_dict(old_data)
    assert state.valence == 0.4
    assert state.stability == 0.7
    assert state.happiness == 0.5
    assert state.sadness == 0.0
    assert state.trust == 0.5
    assert state.attachment == 0.5


def test_json_save_restore_roundtrip():
    """JSON 序列化往返（保存→恢复）状态一致——验收项「情绪状态保存恢复」"""
    original = EmotionState(
        valence=0.6, arousal=0.7, happiness=0.8, sadness=0.1,
        trust=0.9, attachment=0.8, stability=0.6, updated_at="2026-08-21T10:00:00",
    )
    payload = json.dumps(original.to_dict(), ensure_ascii=False)
    restored = EmotionState.from_dict(json.loads(payload))

    assert restored.to_dict() == original.to_dict()


def test_snapshot_envelope():
    """snapshot 携带版本 + 时间戳 + 完整状态"""
    snap = EmotionState(trust=0.8).snapshot()
    assert snap["schema_version"] == SCHEMA_VERSION
    assert "snapshot_at" in snap
    assert snap["state"]["trust"] == 0.8


def test_restore_from_snapshot():
    """restore 从快照恢复（包信封格式）"""
    snap = EmotionState(happiness=0.9).snapshot()
    restored = EmotionState.restore(snap)
    assert restored.happiness == 0.9
    assert restored.to_dict() == snap["state"]


def test_restore_accepts_flat_legacy_dict():
    """restore 兼容直接传入的旧式扁平状态 dict"""
    restored = EmotionState.restore({"valence": -0.5, "updated_at": ""})
    assert restored.valence == -0.5
    assert restored.trust == 0.5


if __name__ == "__main__":
    test_new_dimension_defaults(); print("✅ 1/9 新维度默认值")
    test_new_dimension_clamp(); print("✅ 2/9 新维度边界")
    test_apply_delta_covers_new_dimensions(); print("✅ 3/9 新维度 delta")
    test_to_dict_has_version_envelope(); print("✅ 4/9 版本信封")
    test_from_dict_old_data_gets_defaults(); print("✅ 5/9 旧数据兼容")
    test_json_save_restore_roundtrip(); print("✅ 6/9 JSON 保存恢复")
    test_snapshot_envelope(); print("✅ 7/9 快照信封")
    test_restore_from_snapshot(); print("✅ 8/9 快照恢复")
    test_restore_accepts_flat_legacy_dict(); print("✅ 9/9 扁平旧数据恢复")
    print("\n🎉 E-Emotion-6-1 状态保存恢复全部通过")
