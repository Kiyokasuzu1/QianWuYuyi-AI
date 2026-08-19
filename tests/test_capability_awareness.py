"""
羽依能力认知单元测试

验证 _get_personality_context 能根据当前已启用的模块，
动态生成准确的能力清单，让羽依「知道自己能做什么」。
"""

import sys
import os
from unittest.mock import MagicMock


class _PackageMock(MagicMock):
    """可被当作包导入的 mock —— 支持 from pkg.sub import xxx"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__path__ = []
        self.__all__ = []


def _install_mock(name):
    if name not in sys.modules:
        sys.modules[name] = _PackageMock()


# Mock 掉所有外部依赖，避免本地缺少包时无法导入 orchestrator
for _mod in (
    "openai", "yaml", "websockets", "PIL", "pytesseract",
    "chromadb", "chromadb.utils", "chromadb.utils.embedding_functions",
    "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont",
):
    _install_mock(_mod)

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)


def _make_orchestrator(screen=False, control=False):
    """
    构造一个只用于测试能力说明的 Orchestrator 实例。

    通过直接设置属性来模拟模块启用/禁用状态，
    不真正初始化各子系统，避免依赖外部资源。
    """
    from src.orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.config = {}
    orch.target_user_id = None
    orch.history = []
    orch.current_personality = None
    orch.memory_store = None
    orch.vector_memory = None
    orch.personality_resolver = None
    orch.self_model_store = None
    orch.self_model_context_provider = None
    orch.user_resolver = None
    orch.engine = None
    orch.runtime_context = None
    orch.relationship_profile = None
    orch.relationship_state = None
    orch.event_bus = None
    orch.screen_context_manager = object() if screen else None
    orch.control_manager = object() if control else None
    return orch


def test_empty_personality_returns_empty():
    """没有人格时应该返回空字符串。"""
    orch = _make_orchestrator()
    assert orch._get_personality_context(None) == ""
    assert orch._get_personality_context({}) == ""


def test_basic_capabilities_always_present():
    """基础能力（记忆 / 情绪 / 人格）应始终出现在能力清单中。"""
    orch = _make_orchestrator(screen=False, control=False)
    ctx = orch._get_personality_context({
        "name": "浅雾羽依",
        "description": "温柔害羞的 AI 个体",
    })

    assert "浅雾羽依" in ctx
    assert "记住和用户的对话历史" in ctx
    assert "感知对话中的情绪变化" in ctx
    assert "拥有自己独立的人格与情绪状态" in ctx


def test_screen_capability_appears_when_enabled():
    """启用 screen_context_manager 时，能力清单应包含「查看屏幕」。"""
    orch = _make_orchestrator(screen=True, control=False)
    ctx = orch._get_personality_context({
        "name": "浅雾羽依",
        "description": "测试人格",
    })

    assert "查看用户的电脑屏幕" in ctx
    assert "控制用户的鼠标和键盘" not in ctx


def test_control_capability_appears_when_enabled():
    """启用 control_manager 时，能力清单应包含「控制键鼠」。"""
    orch = _make_orchestrator(screen=False, control=True)
    ctx = orch._get_personality_context({
        "name": "浅雾羽依",
        "description": "测试人格",
    })

    assert "控制用户的鼠标和键盘" in ctx
    assert "查看用户的电脑屏幕" not in ctx


def test_all_capabilities_when_both_enabled():
    """同时启用屏幕与控制模块时，所有高级能力都应出现。"""
    orch = _make_orchestrator(screen=True, control=True)
    ctx = orch._get_personality_context({
        "name": "浅雾羽依",
        "description": "测试人格",
    })

    assert "查看用户的电脑屏幕" in ctx
    assert "控制用户的鼠标和键盘" in ctx
    # 基础能力也必须在
    assert "记住和用户的对话历史" in ctx


def test_capability_instruction_present():
    """能力清单末尾应包含「如实回答」的指引，避免羽依否认自己的能力。"""
    orch = _make_orchestrator()
    ctx = orch._get_personality_context({
        "name": "浅雾羽依",
        "description": "测试",
    })

    assert "如实回答" in ctx


def test_capability_section_format():
    """能力说明应以「你拥有以下能力」开头，便于羽依识别。"""
    orch = _make_orchestrator()
    ctx = orch._get_personality_context({
        "name": "浅雾羽依",
        "description": "测试",
    })

    assert "你拥有以下能力" in ctx
    # 应以句号结尾
    assert ctx.rstrip().endswith("。")


def test_no_capability_section_when_disabled_and_no_personality():
    """没有人格时，即便启用了模块也不应输出能力清单。"""
    orch = _make_orchestrator(screen=True, control=True)
    assert orch._get_personality_context(None) == ""


def test_capability_count_matches_enabled_modules():
    """能力条目数量应与启用的模块匹配：3 个基础 + 启用的高级模块。"""
    # 仅基础能力
    orch = _make_orchestrator(screen=False, control=False)
    ctx = orch._get_personality_context({"name": "Y", "description": "D"})
    # 基础能力 3 条
    assert ctx.count("；") + ctx.count("。") >= 3

    # 基础 + 屏幕
    orch = _make_orchestrator(screen=True, control=False)
    ctx = orch._get_personality_context({"name": "Y", "description": "D"})
    assert "查看用户的电脑屏幕" in ctx
    assert "控制用户的鼠标和键盘" not in ctx


def test_generate_initiative_uses_dynamic_personality_context():
    """主动消息应使用动态人格上下文（含能力说明），而非硬编码。"""
    orch = _make_orchestrator(screen=True, control=False)

    class _MockResolver:
        def resolve(self):
            return {"name": "浅雾羽依", "description": "温柔害羞的 AI 个体"}

    class _MockProvider:
        def get_context(self):
            return {}

    class _MockEngine:
        def generate(self, **kwargs):
            return "测试主动消息"

    orch.personality_resolver = _MockResolver()
    orch.self_model_context_provider = _MockProvider()
    orch.engine = _MockEngine()

    result = orch.generate_initiative("test_user")
    assert result == "测试主动消息"


def test_generate_initiative_passes_screen_context():
    """主动消息应传递屏幕上下文到 LLM。"""
    orch = _make_orchestrator(screen=True, control=False)

    class _MockResolver:
        def resolve(self):
            return {"name": "浅雾羽依", "description": "测试"}

    class _MockProvider:
        def get_context(self):
            return {}

    captured = {}

    class _MockEngine:
        def generate(self, **kwargs):
            captured.update(kwargs)
            return "主动消息"

    orch.personality_resolver = _MockResolver()
    orch.self_model_context_provider = _MockProvider()
    orch.engine = _MockEngine()

    orch.generate_initiative("test_user")

    assert "查看用户的电脑屏幕" in captured.get("personality_context", "")


def test_initiative_uses_same_context_as_process():
    """主动消息的能力上下文应与 process() 完全一致。"""
    orch = _make_orchestrator(screen=True, control=True)

    class _MockResolver:
        def resolve(self):
            return {"name": "浅雾羽依", "description": "温柔害羞的 AI 个体"}

    class _MockProvider:
        def get_context(self):
            return {}

    orch.personality_resolver = _MockResolver()
    orch.self_model_context_provider = _MockProvider()

    process_ctx = orch._get_personality_context(
        orch.personality_resolver.resolve()
    )
    assert "查看用户的电脑屏幕" in process_ctx
    assert "控制用户的鼠标和键盘" in process_ctx
    assert "记住和用户的对话历史" in process_ctx
