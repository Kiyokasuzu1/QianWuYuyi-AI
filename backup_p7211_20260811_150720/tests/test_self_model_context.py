"""
SelfModelContextProvider 测试 v8.4.2
覆盖：空模型、身份、参考语、数值隐藏、元数据不泄露、长文本截断
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


def test_empty_model_returns_empty():
    store = FakeStore(None)
    provider = SelfModelContextProvider(store)
    assert provider.get_context() == ""


def test_identity_is_always_present():
    model = SelfModelV3(identity="浅雾羽依")
    store = FakeStore(model)
    ctx = SelfModelContextProvider(store).get_context()
    assert "浅雾羽依" in ctx


def test_context_is_reference_not_instruction():
    """Prompt 应使用'参考'语气，而非强制设定"""
    model = SelfModelV3(identity="浅雾羽依")
    store = FakeStore(model)
    ctx = SelfModelContextProvider(store).get_context()
    assert "自我认知参考" in ctx
    assert "不是绝对事实" in ctx
    assert "你必须" not in ctx
    assert "你的设定是" not in ctx


def test_traits_listed_without_numbers():
    """不泄露人格数值"""
    model = SelfModelV3(traits={"openness": 0.7, "shyness": 0.4})
    store = FakeStore(model)
    ctx = SelfModelContextProvider(store).get_context()
    assert "openness" in ctx
    assert "shyness" in ctx
    assert "0.7" not in ctx
    assert "0.4" not in ctx


def test_beliefs_included():
    model = SelfModelV3(beliefs=["表达是安全的", "成长需要时间"])
    store = FakeStore(model)
    ctx = SelfModelContextProvider(store).get_context()
    assert "表达是安全的" in ctx
    assert "成长需要时间" in ctx


def test_narratives_truncated():
    # 确保文本长度超过80字符，从而触发截断并添加“…”标识
    long_text = "这是一段非常长的成长叙事文本" * 8   # 长度约为 12*8 = 96 字，超过 80
    model = SelfModelV3(narrative_items=[NarrativeItem(text=long_text, source_ids=["r1"])])
    store = FakeStore(model)
    ctx = SelfModelContextProvider(store).get_context()
    assert "…" in ctx


def test_metadata_not_exposed():
    """source_ids 等内部 ID 不应出现在 Prompt 中"""
    model = SelfModelV3(
        narrative_items=[NarrativeItem(text="成长故事", source_ids=["ref_001"])]
    )
    store = FakeStore(model)
    ctx = SelfModelContextProvider(store).get_context()
    assert "ref_001" not in ctx
    assert "source_ids" not in ctx


if __name__ == "__main__":
    test_empty_model_returns_empty()
    print("✅ 1/7 空模型安全")
    test_identity_is_always_present()
    print("✅ 2/7 身份始终存在")
    test_context_is_reference_not_instruction()
    print("✅ 3/7 参考语而非指令语")
    test_traits_listed_without_numbers()
    print("✅ 4/7 性格无数值泄露")
    test_beliefs_included()
    print("✅ 5/7 信念正确输出")
    test_narratives_truncated()
    print("✅ 6/7 长文本截断")
    test_metadata_not_exposed()
    print("✅ 7/7 元数据不泄露")
    print("\n🎉 全部 SelfModelContext 测试通过")