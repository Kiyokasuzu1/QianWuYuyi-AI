"""
Phase 8.5A：SelfModel 上下文影响验证
验证不同 SelfModel 生成的 Prompt 片段携带正确认知信息，
且不会泄露内部数值。
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.personality.self_model_v3 import SelfModelV3, NarrativeItem
from src.personality.self_model_context_provider import SelfModelContextProvider


class FakeStore:
    def __init__(self, model=None):
        self._current_model = model

    def get_active_self_model(self):
        return self._current_model


def make_store(model):
    return FakeStore(model)


def test_exploration_model_context():
    """探索型：上下文包含探索信号，且不泄露数值"""
    model = SelfModelV3(
        identity="浅雾羽依",
        traits={"curiosity": 0.8, "openness": 0.7},
        beliefs=["尝试新事物是值得的"],
        narrative_items=[
            NarrativeItem("过去的探索经历让我相信，迈出第一步往往会有意外收获", ["r1"])
        ]
    )
    ctx = SelfModelContextProvider(make_store(model)).get_context()

    assert "curiosity" in ctx
    assert "openness" in ctx
    assert "尝试新事物是值得的" in ctx
    assert "意外收获" in ctx
    assert "稳定比冒险更重要" not in ctx
    # 数值不得泄露
    assert "0.8" not in ctx
    assert "0.7" not in ctx


def test_cautious_model_context():
    """谨慎型：上下文包含谨慎信号，且不泄露数值"""
    model = SelfModelV3(
        identity="浅雾羽依",
        traits={"shyness": 0.8, "caution": 0.7},
        beliefs=["稳定比冒险更重要"],
        narrative_items=[
            NarrativeItem("过去的一些经历让我明白，保护好自己也很重要", ["r2"])
        ]
    )
    ctx = SelfModelContextProvider(make_store(model)).get_context()

    assert "shyness" in ctx
    assert "caution" in ctx
    assert "稳定比冒险更重要" in ctx
    assert "保护好自己" in ctx
    assert "尝试新事物是值得的" not in ctx
    # 数值不得泄露
    assert "0.8" not in ctx
    assert "0.7" not in ctx


def test_contexts_are_different():
    """两个极端模型产生的上下文应明显不同"""
    model_a = SelfModelV3(traits={"curiosity": 0.8}, beliefs=["探索是好的"])
    model_b = SelfModelV3(traits={"caution": 0.8}, beliefs=["安全第一"])
    ctx_a = SelfModelContextProvider(make_store(model_a)).get_context()
    ctx_b = SelfModelContextProvider(make_store(model_b)).get_context()
    assert ctx_a != ctx_b


if __name__ == "__main__":
    test_exploration_model_context()
    print("✅ 1/3 探索型上下文")
    test_cautious_model_context()
    print("✅ 2/3 谨慎型上下文")
    test_contexts_are_different()
    print("✅ 3/3 上下文差异")
    print("\n🎉 Phase 8.5A 通过")