"""
Phase 8.5B & 8.5C：SelfModel 行为闭环验证（覆盖版，修复 TraitState 类型问题）
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.personality.self_model_v3 import SelfModelV3, NarrativeItem
from src.personality.self_model_context_provider import SelfModelContextProvider
from src.personality.self_model_builder_v3 import SelfModelBuilderV3
from src.reflection.reflection_record import ReflectionRecord
from src.personality.trait_state import TraitState
from src.personality.trait_state_updater import TraitStateUpdater


class FakeStore:
    def __init__(self, model=None):
        self._current_model = model

    def get_active_self_model(self):
        return self._current_model


class BehaviorPolicyAnalyzer:
    """模拟行为策略层：根据 SelfModel 上下文推断行为倾向信号。"""
    def analyze(self, context: str) -> dict:
        explore_keywords = ["curiosity", "openness", "探索", "尝试", "新事物", "意外收获"]
        caution_keywords = ["shyness", "caution", "谨慎", "稳定", "安全", "保护"]

        explore_score = sum(1 for w in explore_keywords if w in context)
        caution_score = sum(1 for w in caution_keywords if w in context)

        total = explore_score + caution_score
        exploration_drive = explore_score / total if total > 0 else 0.5
        risk_control = caution_score / total if total > 0 else 0.5

        return {
            "exploration_drive": round(exploration_drive, 2),
            "risk_control": round(risk_control, 2),
            "balance": "explore" if exploration_drive > 0.6 else "caution" if risk_control > 0.6 else "balanced"
        }


# ---------- 8.5B ----------
def test_exploration_model_yields_high_exploration():
    model = SelfModelV3(
        traits={"curiosity": 0.8, "openness": 0.7},
        beliefs=["尝试新事物是值得的"],
        narrative_items=[NarrativeItem("过去的探索经历让我相信迈出第一步会有收获", ["r1"])]
    )
    ctx = SelfModelContextProvider(FakeStore(model)).get_context()
    signals = BehaviorPolicyAnalyzer().analyze(ctx)
    assert signals["exploration_drive"] > 0.5
    assert signals["balance"] == "explore"


def test_cautious_model_yields_high_caution():
    model = SelfModelV3(
        traits={"shyness": 0.8, "caution": 0.7},
        beliefs=["稳定比冒险更重要"],
        narrative_items=[NarrativeItem("保护好自己也很重要", ["r2"])]
    )
    ctx = SelfModelContextProvider(FakeStore(model)).get_context()
    signals = BehaviorPolicyAnalyzer().analyze(ctx)
    assert signals["risk_control"] > 0.5
    assert signals["balance"] == "caution"


def test_mixed_personality_yields_balanced_signal():
    model = SelfModelV3(
        traits={"curiosity": 0.8, "caution": 0.8},
        beliefs=["探索是好的", "安全也很重要"],
        narrative_items=[
            NarrativeItem("探索让我成长", ["r1"]),
            NarrativeItem("谨慎让我避免伤害", ["r2"])
        ]
    )
    ctx = SelfModelContextProvider(FakeStore(model)).get_context()
    signals = BehaviorPolicyAnalyzer().analyze(ctx)
    assert 0.3 <= signals["exploration_drive"] <= 0.7
    assert 0.3 <= signals["risk_control"] <= 0.7
    assert signals["balance"] == "balanced"


def test_empty_model_does_not_crash():
    ctx = ""
    signals = BehaviorPolicyAnalyzer().analyze(ctx)
    assert signals["exploration_drive"] == 0.5
    assert signals["risk_control"] == 0.5


def test_different_models_produce_different_signals():
    model_a = SelfModelV3(traits={"curiosity": 0.9}, beliefs=["探索是好的"])
    model_b = SelfModelV3(traits={"caution": 0.9}, beliefs=["安全第一"])
    ctx_a = SelfModelContextProvider(FakeStore(model_a)).get_context()
    ctx_b = SelfModelContextProvider(FakeStore(model_b)).get_context()
    sig_a = BehaviorPolicyAnalyzer().analyze(ctx_a)
    sig_b = BehaviorPolicyAnalyzer().analyze(ctx_b)
    assert sig_a["exploration_drive"] != sig_b["exploration_drive"]
    assert sig_a["risk_control"] != sig_b["risk_control"]


# ---------- 8.5C ----------
def test_growth_event_changes_behavior():
    """使用真实 TraitStateUpdater + 字典格式成长记录验证完整闭环"""
    # 初始特质
    initial_traits = {
        "caution": TraitState(current_value=0.8, stability=0.7),
        "curiosity": TraitState(current_value=0.2, stability=0.7),
        "openness": TraitState(current_value=0.3, stability=0.7)
    }

    # 成长记录（TypedDict 字典格式）
    growth_records = [
        {
            "record_id": "gr1",
            "growth_level": "trait",
            "affected_dimensions": ["curiosity", "openness"],
            "changes": {"curiosity": 0.3, "openness": 0.2},
            "event": "尝试公开表达并获得正面反馈",
            "narrative": "我第一次主动表达了观点，发现并没有想象中可怕",
            "meaning": "表达可能是安全的",
            "confidence": 0.85,
            "validation_count": 3,
            "momentum": 0.7,
            "approved": True,
        },
        {
            "record_id": "gr2",
            "growth_level": "preference",
            "affected_dimensions": ["curiosity"],
            "changes": {"curiosity": 0.2},
            "event": "主动探索新话题并感到有趣",
            "narrative": "当我主动聊了一个新话题，发现交流变得很有趣",
            "meaning": "探索新事物可以带来成长",
            "confidence": 0.8,
            "validation_count": 2,
            "momentum": 0.6,
            "approved": True,
        }
    ]

    # 使用 TraitStateUpdater 更新特质
    updater = TraitStateUpdater()
    updated_traits = dict(initial_traits)
    for record in growth_records:
        updated_traits = updater.apply(record, updated_traits)

    # 提取更新后的数值（兼容 dict 或 TraitState）
    updated_trait_values = {}
    for dim, state in updated_traits.items():
        if isinstance(state, dict):
            updated_trait_values[dim] = state.get("current_value", 0.5)
        else:
            updated_trait_values[dim] = state.current_value

    # 构建反思记录
    reflections = [
        ReflectionRecord(
            reflection_id="ref1",
            timestamp="2026-01-01",
            is_safe=True,
            confidence=0.9,
            causal_chain=["经历: 尝试公开表达", "维度变化: curiosity提高"],
            new_beliefs=["尝试新事物是安全的"],
            reflection_level="belief_change",
            current_understanding="表达没有带来负面影响"
        ),
        ReflectionRecord(
            reflection_id="ref2",
            timestamp="2026-01-02",
            is_safe=True,
            confidence=0.85,
            causal_chain=["经历: 探索新话题", "维度变化: openness提高"],
            new_beliefs=["探索能带来成长"],
            reflection_level="insight",
            current_understanding="新话题交流打开了视野"
        )
    ]

    # 构建更新后的 SelfModel
    builder = SelfModelBuilderV3(min_confidence=0.5)
    updated_model = builder.build(
        identity="浅雾羽依",
        traits=updated_trait_values,
        values={},
        reflections=reflections,
        previous_model=SelfModelV3(
            traits={"caution": 0.8, "curiosity": 0.2},
            beliefs=["安全第一"]
        )
    )

    # 验证行为倾向转向探索
    ctx = SelfModelContextProvider(FakeStore(updated_model)).get_context()
    signals = BehaviorPolicyAnalyzer().analyze(ctx)

    assert signals["exploration_drive"] > signals["risk_control"], \
        f"成长后应更倾向探索，实际信号: {signals}"
    assert "尝试新事物是安全的" in ctx
    assert "探索能带来成长" in ctx


if __name__ == "__main__":
    test_exploration_model_yields_high_exploration()
    print("✅ 1/6 探索型→高探索信号")
    test_cautious_model_yields_high_caution()
    print("✅ 2/6 谨慎型→高谨慎信号")
    test_mixed_personality_yields_balanced_signal()
    print("✅ 3/6 冲突人格→平衡信号")
    test_empty_model_does_not_crash()
    print("✅ 4/6 空模型安全")
    test_different_models_produce_different_signals()
    print("✅ 5/6 不同模型信号不同")
    test_growth_event_changes_behavior()
    print("✅ 6/6 成长事件→行为变化闭环（TraitStateUpdater 真实链路）")
    print("\n🎉 Phase 8.5B + 8.5C 全部通过")