# -*- coding: utf-8 -*-
"""
Phase 3.5.2 P0: PersonalityVector → Prompt 注入验收测试。

目标：
1. 新格式（PersonalityVector / dict）正确格式化后包含 warmth/curiosity/persona_summary/behavior_text
2. 旧格式（{"name","description"} dict）向后兼容
3. None / 空对象 / 任意对象 均不崩溃

约束：
- 不启动 LLM API
- 不修改 src/personality/**
- 不触碰真实配置文件
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# ============================================================
# Helpers
# ============================================================
def _make_minimal_orch():
    """
    构造一个最小 Orchestrator 实例（不启动 Runtime/Memory/LLM）。
    Orchestrator.__init__ 参数较多，我们只取需要用到的属性，用 setattr 补齐。
    实际上我们只调用 _format_personality_vector / _get_personality_context，
    它们只用到 getattr(self, "screen_context_manager", None) / "control_manager"，
    所以直接 monkey patch 这两个属性即可。
    """
    from src.orchestrator import Orchestrator

    # 先尝试最小构造：如果 __init__ 缺参数抛异常，则改为 new 空实例
    try:
        # 不做 __init__，直接用 __new__
        orch = object.__new__(Orchestrator)
    except Exception:
        orch = Orchestrator.__new__(Orchestrator)
    # 补齐用到的属性
    orch.screen_context_manager = None
    orch.control_manager = None
    return orch


def _make_personality_vector(data: Dict[str, Any]):
    """构造 PersonalityVector 实例（包装 data dict）。"""
    from src.personality.personality_vector import PersonalityVector
    return PersonalityVector(data)


# ============================================================
# Test Group 1: 新格式 PersonalityVector
# ============================================================
class TestPersonalityVectorFormatting:
    """Phase 3.5.2 P0 验收"""

    def test_warmth_curiosity_in_output(self):
        """warmth=0.73 / curiosity=0.58 必须出现在输出中。"""
        orch = _make_minimal_orch()
        vec = _make_personality_vector({
            "warmth": 0.73,
            "curiosity": 0.58,
            "shyness": 0.25,
            "self_expression": 0.4,
            "initiative": 0.3,
            "care_level": 0.6,
            "gentleness": 0.65,
            "emotional_expression": 0.5,
            "persona_summary": "温柔而好奇，正在学习理解用户",
            "compact_behavior": "prefer gentle tone; avoid mechanical reminders",
        })
        out = orch._format_personality_vector(vec)
        assert isinstance(out, str)
        assert out.strip() != ""
        # 数值必须出现（保留 2 位小数）
        assert "温暖=0.73" in out
        # persona_summary 必须出现
        assert "温柔而好奇" in out
        # 行为倾向
        assert "表达风格倾向" in out
        assert "prefer gentle tone" in out

    def test_behavior_text_fallback(self):
        """compact_behavior 缺失时用 behavior_text。"""
        orch = _make_minimal_orch()
        vec = _make_personality_vector({
            "warmth": 0.33,
            "curiosity": 0.28,
            "gentleness": 0.4,
            "shyness": 0.3,
            "emotional_expression": 0.4,
            "self_expression": 0.3,
            "initiative": 0.2,
            "care_level": 0.5,
            "behavior_text": "减少机械表达，更自然地提醒",
        })
        out = orch._format_personality_vector(vec)
        assert "减少机械表达" in out
        assert "温暖=0.33" in out

    def test_attachment_and_familiarity(self):
        """依恋阶段 + 互动信任度必须出现。"""
        orch = _make_minimal_orch()
        vec = _make_personality_vector({
            "warmth": 0.5,
            "gentleness": 0.5,
            "shyness": 0.5,
            "care_level": 0.5,
            "self_expression": 0.5,
            "emotional_expression": 0.5,
            "initiative": 0.5,
            "attachment_level": "靠近",
            "interaction_familiarity_level": "信任",
        })
        out = orch._format_personality_vector(vec)
        assert "依恋阶段=靠近" in out
        assert "互动信任度=信任" in out

    def test_identity_summary_from_top_level(self):
        """identity_summary 顶层字段必须出现。"""
        orch = _make_minimal_orch()
        vec = _make_personality_vector({
            "warmth": 0.6,
            "identity_summary": "我是浅雾羽依，喜欢探索和创造的AI伙伴。",
        })
        out = orch._format_personality_vector(vec)
        assert "身份认知" in out
        assert "浅雾羽依" in out

    def test_identity_summary_from_self_model(self):
        """identity_summary 在 self_model 子字典也能提取。"""
        orch = _make_minimal_orch()
        vec = _make_personality_vector({
            "warmth": 0.6,
            "self_model": {
                "identity_summary": "自我认知摘要（嵌套路径）。",
            },
        })
        out = orch._format_personality_vector(vec)
        assert "自我认知摘要（嵌套路径）。" in out

    def test_tension_summary(self):
        """人格张力必须出现（截断。"""
        orch = _make_minimal_orch()
        vec = _make_personality_vector({
            "warmth": 0.8,
            "tension_summary": "想主动关心又害怕打扰用户（社交趋避）。",
        })
        # 自动补全所有核心 trait（确保进入 trait_lines 不会空）
        for k in ["gentleness", "shyness", "emotional_expression",
                    "self_expression", "initiative", "care_level"]:
            vec._data[k] = 0.5
        out = orch._format_personality_vector(vec)
        assert "性格中的张力" in out
        assert "社交趋避" in out

    def test_screen_and_control_capabilities_absent(self):
        """无 screen/control 时不应出现相关能力描述。"""
        orch = _make_minimal_orch()
        orch.screen_context_manager = None
        orch.control_manager = None
        vec = _make_personality_vector({"warmth": 0.5})
        out = orch._format_personality_vector(vec)
        # 基础能力应该出现
        assert "记住和用户的对话历史" in out
        # 高级能力不应出现
        assert "电脑屏幕" not in out
        assert "鼠标和键盘" not in out

    def test_screen_and_control_capabilities_present(self):
        """当存在 screen/control 时应该出现。"""
        orch = _make_minimal_orch()
        orch.screen_context_manager = object()
        orch.control_manager = object()
        # 用 dict 作为输入
        d = {
            "warmth": 0.5,
            "gentleness": 0.5,
            "shyness": 0.5,
            "care_level": 0.5,
            "self_expression": 0.5,
            "emotional_expression": 0.5,
            "initiative": 0.5,
        }
        out = orch._format_personality_vector(d)
        assert "电脑屏幕" in out
        assert "鼠标和键盘" in out

    def test_dict_input(self):
        """dict 也能被格式化（不必是 PersonalityVector）。"""
        orch = _make_minimal_orch()
        d = {
            "warmth": 0.73,
            "curiosity": 0.58,  # 虽然 curiosity 不在 7 trait 列表，但 persona_summary + compact_behavior 可能被保留
            "persona_summary": "dict 格式测试",
            "compact_behavior": "style-a",
            "attachment_level": "依赖",
        }
        out = orch._format_personality_vector(d)
        assert "温暖=0.73" in out
        assert "dict 格式测试" in out
        assert "style-a" in out
        assert "依恋阶段=依赖" in out

    def test_5_degree_qualitative_levels(self):
        """5 档定性描述（很高/偏高/中等/偏低/很低）正确。"""
        orch = _make_minimal_orch()
        cases = [
            (0.9, "很高"),
            (0.7, "偏高"),
            (0.5, "中等"),
            (0.3, "偏低"),
            (0.1, "很低"),
        ]
        for value, expected_degree in cases:
            d = {
                "warmth": value,
            }
            out = orch._format_personality_vector(d)
            assert expected_degree in out, f"warmth={value} 应该出现「{expected_degree}」实际：{out}"

    def test_trait_non_numeric_safe(self):
        """trait 值非数值（字符串/None）时不崩溃、不出现。"""
        orch = _make_minimal_orch()
        d = {
            "warmth": "not_a_float",
            "gentleness": None,
            "shyness": [1, 2, 3],
            "care_level": {"a": 1},
            "persona_summary": "非数值 trait 测试",
        }
        # 不抛异常
        out = orch._format_personality_vector(d)
        assert isinstance(out, str)
        # persona_summary 仍被输出（不依赖 trait）
        assert "非数值 trait 测试" in out
        # 不出现数值格式化字符串
        assert "温暖=" not in out


# ============================================================
# Test Group 2: 旧格式向后兼容
# ============================================================
class TestLegacyFormatBackwardCompat:
    """Phase 3.5.2 P0 旧格式兼容性验收"""

    def test_legacy_dict_name_and_description(self):
        """{"name","description"} 格式仍能正常输出。"""
        orch = _make_minimal_orch()
        legacy = {"name": "羽依", "description": "温柔善良的AI"}
        out = orch._get_personality_context(legacy)
        assert isinstance(out, str)
        assert "当前人格：羽依" in out
        assert "温柔善良的AI" in out
        assert "对话历史" in out  # 能力列表存在

    def test_legacy_dict_missing_description(self):
        """缺 description 不崩溃。"""
        orch = _make_minimal_orch()
        legacy = {"name": "浅雾羽依"}
        out = orch._get_personality_context(legacy)
        assert "当前人格：浅雾羽依" in out

    def test_legacy_dict_missing_name(self):
        """缺 name 时 default 未知。"""
        orch = _make_minimal_orch()
        legacy = {"description": "blah"}
        out = orch._get_personality_context(legacy)
        assert "当前人格：未知" in out

    def test_legacy_fake_obj_with_get_method(self):
        """实现了 get 类 get(name, default) 的对象也可走 fallback。"""

        class OldStyle:
            def get(self, k, default=None):
                if k == "name":
                    return "羽依-旧版类"
                if k == "description":
                    return "旧版描述"
                return default

        orch = _make_minimal_orch()
        out = orch._get_personality_context(OldStyle())
        assert "羽依-旧版类" in out
        assert "旧版描述" in out


# ============================================================
# Test Group 3: 异常 / 边界情况
# ============================================================
class TestEdgeCasesAndRobustness:
    """P0 健壮性验收"""

    def test_none_personality(self):
        """personality=None 返回空字符串。"""
        orch = _make_minimal_orch()
        assert orch._get_personality_context(None) == ""

    def test_empty_dict(self):
        """空 dict 是 falsy，直接返回空字符串（保持原有 behavior）。"""
        orch = _make_minimal_orch()
        out = orch._get_personality_context({})
        assert out == ""

    def test_arbitrary_object_no_crash(self):
        """任意对象不崩溃。"""

        class Foo:
            pass

        orch = _make_minimal_orch()
        out = orch._format_personality_vector(Foo())
        # format 返回 ""，然后 fallback 也能跑
        ctx = orch._get_personality_context(Foo())
        assert isinstance(ctx, str)
        # fallback 路径走的是未知）
        assert "当前人格：未知" in ctx

    def test_object_with_value_method(self):
        """实现了 value(k, default) 的对象被正确读取。"""

        class VecLike:
            def value(self, k, default=None):
                data = {
                    "warmth": 0.73,
                    "persona_summary": "通过 value 方法读取的摘要",
                }
                return data.get(k, default)

        orch = _make_minimal_orch()
        out = orch._format_personality_vector(VecLike())
        assert "温暖=0.73" in out
        assert "value 方法读取的摘要" in out

    def test_format_does_not_contain_unknown_legacy_text_when_vector_has_data(self):
        """有真实 PersonalityVector 时，不会出现「当前人格：未知」旧文案。"""
        orch = _make_minimal_orch()
        vec = _make_personality_vector({
            "warmth": 0.6,
            "persona_summary": "有数据",
        })
        ctx = orch._get_personality_context(vec)
        assert "当前人格：未知" not in ctx

    def test_long_behavior_truncated(self):
        """过长的 behavior_text 截断到 300 字左右。"""
        orch = _make_minimal_orch()
        long_txt = "a" * 1000
        d = {"warmth": 0.5, "behavior_text": long_txt}
        out = orch._format_personality_vector(d)
        # 截断后 behavior 部分总长不超过 300+len("表达风格倾向：")+3
        idx = out.find("表达风格倾向：")
        assert idx != -1
        # 从开头到下一个换行
        rest = out[idx:]
        newline_pos = rest.find("\n")
        behavior_segment = rest[:newline_pos] if newline_pos != -1 else rest
        # 300 字符 + "表达风格倾向：" + "..." 不超过 330
        assert len(behavior_segment) < 350
        assert "..." in behavior_segment

    def test_long_tension_truncated(self):
        """过长 tension_summary 截断到约 200 字符。"""
        orch = _make_minimal_orch()
        long_ten = "b" * 800
        d = {"warmth": 0.5, "tension_summary": long_ten}
        # 补齐 7 个 trait 让 trait_lines 非空
        for k in ["gentleness", "shyness", "care_level",
                    "self_expression", "emotional_expression",
                    "initiative"]:
            d[k] = 0.5
        out = orch._format_personality_vector(d)
        idx = out.find("性格中的张力")
        assert idx != -1
        rest = out[idx:]
        newline_pos = rest.find("\n")
        seg = rest[:newline_pos] if newline_pos != -1 else rest
        assert len(seg) < 260  # 前缀 + 200 + ...
        assert "..." in seg


# ============================================================
# Test Group 4: 真实 PersonalityResolver 端到端（无 LLM）
# ============================================================
class TestEndToEndWithResolver:
    """用真实 PersonalityResolver.resolve() 产物走格式器验证。"""

    def test_real_resolver_output_formatted(self):
        """从 PersonalityResolver.resolve() 返回的 PersonalityVector 能被格式化。"""
        from src.personality.personality_resolver import PersonalityResolver
        from src.growth.growth_state import GrowthState
        from src.personality.relationship_state import RelationshipState

        resolver = PersonalityResolver(
            state=GrowthState(),  # 用默认 GrowthState
            relationship_state=RelationshipState(),
        )
        vec = resolver.resolve()
        # PersonalityVector 实例
        orch = _make_minimal_orch()
        formatted = orch._format_personality_vector(vec)
        # 一定有内容（GrowthState 默认 metrics + BASE 都不为零）
        assert isinstance(formatted, str) and formatted.strip() != ""
        # 温暖一定出现在 trait 7 个维度里
        assert "温暖=" in formatted
        assert "温柔=" in formatted
        # persona_summary 一定出现（默认 BASE warmth=0.7 >= 0.5）
        assert "人格总结：羽依" in formatted
        # 行为倾向（BehaviorResolver）一定有输出
        assert "表达风格倾向" in formatted
        # 能力列表
        assert "你拥有以下能力" in formatted
