"""
E-Emotion-6 测试 5/5：治理边界（identity_core / personality_state 不被情绪链路修改）
覆盖任务书验收项：identity_core 完全不变、personality_state 不被自动修改

治理红线（AGENTS.md / 任务书）：
- 情绪只能影响：当前状态 / 行为倾向 / 回复策略 / 记忆权重
- 不允许直接修改 identity_core；不允许绕过 Proposal/Governance 修改人格核心
- 完整情绪链路（评估 → 应用 → 衰减 → 记忆权重 → 响应策略）跑完后，
  IDENTITY_CORE 与 PersonalityState 必须逐位不变。
"""
import sys, os, copy
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.emotion.emotion_state import EmotionState
from src.emotion.emotion_event import EmotionEvent
from src.emotion.emotion_evaluator import EmotionEvaluator
from src.emotion.emotion_updater import EmotionUpdater
from src.emotion.emotion_memory_weight import EmotionMemoryWeightBridge
from src.emotion.emotion_response_strategy import EmotionResponseStrategyBuilder


def _run_full_emotion_pipeline():
    """跑完整情绪链路（评估→提案→应用→衰减→记忆权重→响应策略）。"""
    evaluator = EmotionEvaluator()
    updater = EmotionUpdater()
    bridge = EmotionMemoryWeightBridge()
    builder = EmotionResponseStrategyBuilder()

    state = EmotionState()
    for event in (
        EmotionEvent("user_praise", intensity=1.0),
        EmotionEvent("user_conflict", intensity=0.8),
        EmotionEvent("mitigation", intensity=0.7),
        EmotionEvent("achievement", intensity=0.9),
    ):
        proposals = evaluator.evaluate_proposals(event, evidence_ids=["ev_1"])
        result = updater.apply(state, proposals, source_event_id=event.event_type)
        state = EmotionState.from_dict(result.state_after)

    state = updater.decay(state, seconds=7200)
    _ = bridge.apply_to_memory({"content": "高情绪记忆", "importance": 0.5}, state)
    _ = builder.build(state)
    return state


def test_identity_core_unchanged_after_full_pipeline():
    """完整情绪链路后 IDENTITY_CORE 逐位不变——验收项「identity_core完全不变」"""
    from src.personality.identity_core import IDENTITY_CORE
    before = copy.deepcopy(IDENTITY_CORE)
    _run_full_emotion_pipeline()
    assert IDENTITY_CORE == before


def test_personality_state_not_modified_by_pipeline():
    """完整情绪链路后 PersonalityState.traits 不被自动修改——验收项「personality_state不被自动修改」"""
    from src.personality.personality_state import PersonalityState
    ps = PersonalityState()
    before_traits = copy.deepcopy(ps.traits)
    before_version = ps.version
    _run_full_emotion_pipeline()
    assert ps.traits == before_traits
    assert ps.version == before_version


def test_emotion_modules_do_not_import_personality():
    """情绪核心模块不导入人格模块（编译期边界，防未来越权）"""
    import src.emotion.emotion_updater as updater_mod
    import src.emotion.emotion_response_strategy as strategy_mod
    import src.emotion.emotion_memory_weight as weight_mod

    for mod in (updater_mod, strategy_mod, weight_mod):
        with open(mod.__file__, encoding="utf-8") as f:
            text = f.read()
        # 红线：不允许 import 人格域模块（人格修改必须走 Governance 链）
        assert "import src.personality" not in text
        assert "from src.personality" not in text
        assert "import src.identity" not in text
        assert "from src.identity" not in text


if __name__ == "__main__":
    test_identity_core_unchanged_after_full_pipeline(); print("✅ 1/3 identity_core 完全不变")
    test_personality_state_not_modified_by_pipeline(); print("✅ 2/3 personality_state 不被自动修改")
    test_emotion_modules_do_not_import_personality(); print("✅ 3/3 模块边界（不依赖人格模块）")
    print("\n🎉 E-Emotion-6-5 治理边界全部通过")
