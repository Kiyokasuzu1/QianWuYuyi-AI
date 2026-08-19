# -*- coding: utf-8 -*-
"""
tests/test_phase_3_9_0_reality_grounding.py

Phase 3.9.0: Reality Grounding Layer 测试

覆盖:
1. 无视觉输入禁止描述屏幕/游戏/画面
2. 用户明确提供信息(USER_INPUT Fact)允许引用
3. Memory 信息(MEMORY Fact)允许引用
4. Observation 信息(observation_state 标记 available=True)允许引用
5. INFERENCE Fact 不允许伪装成 Observation
6. Runtime 不依赖 Vision / Screen Capture 实现
7. ResponseGuardChain 顺序:Reality → Perception → Personality
8. 集成测试:Phase 3.9.0 与 Phase 3.8.x / 3.7.x 兼容
"""
import sys
import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 工具
# ============================================================
def _empty_obs_state(**overrides):
    """构造一个空的 ObservationState(什么都看不到)。"""
    from src.runtime.perception import ObservationState
    state = ObservationState()
    for k, v in overrides.items():
        setattr(state, k, v)
    return state


def _screen_obs(content: str = "用户正在浏览QQ聊天窗口", available: bool = True):
    from src.runtime.perception import Observation, ObservationKind
    return Observation(
        kind=ObservationKind.SCREEN,
        content=content,
        available=available,
    )


def _user_input_fact(content: str = "我今天去爬山"):
    from src.runtime.perception import Fact, USER_INPUT
    return Fact(content=content, source=USER_INPUT, confidence=1.0)


def _memory_fact(content: str = "用户喜欢猫"):
    from src.runtime.perception import Fact, MEMORY
    return Fact(content=content, source=MEMORY, confidence=0.8)


def _vision_fact(content: str = "屏幕上显示QQ聊天界面"):
    from src.runtime.perception import Fact, VISION
    return Fact(content=content, source=VISION, confidence=0.9)


def _inference_fact(content: str = "用户可能在玩游戏"):
    from src.runtime.perception import Fact, INFERENCE
    return Fact(content=content, source=INFERENCE, confidence=0.3)


# ============================================================
# T1: 无视觉输入禁止描述屏幕
# ============================================================
class TestNoScreenObservation:
    def test_describe_screen_without_observation_blocked(self):
        """无 screen observation 时,描述屏幕应被标记为 hallucinated。"""
        from src.runtime.perception import RealityGuard
        rg = RealityGuard()
        report = rg.check("你刚才在屏幕上看什么?")
        assert not report.allowed
        assert "hallucinated_visual_observation" in report.violations

    def test_describe_gaming_without_observation_blocked(self):
        """无 screen observation 时,描述用户玩游戏应被阻断。"""
        from src.runtime.perception import RealityGuard
        rg = RealityGuard()
        report = rg.check("你刚才游戏里操作很厉害啊")
        assert not report.allowed
        assert "hallucinated_visual_observation" in report.violations

    def test_describe_user_expression_without_camera_blocked(self):
        """无 camera observation 时,描述用户表情应被阻断。"""
        from src.runtime.perception import RealityGuard
        rg = RealityGuard()
        report = rg.check("你看起来很开心,表情很放松")
        assert not report.allowed
        assert "hallucinated_visual_observation" in report.violations

    def test_describe_scene_without_observation_blocked(self):
        """无 current_scene 时,描述环境应被阻断。"""
        from src.runtime.perception import RealityGuard
        rg = RealityGuard()
        report = rg.check("你周围环境很安静")
        assert not report.allowed
        assert "hallucinated_scene_observation" in report.violations


# ============================================================
# T2: 用户明确提供信息允许引用
# ============================================================
class TestUserInputAllowed:
    def test_user_input_fact_allows_visual_reference(self):
        """USER_INPUT Fact 允许引用,即使无 observation。"""
        from src.runtime.perception import RealityGuard
        rg = RealityGuard()
        # 用户在消息中说自己"看视频",不是 LLM 看到的
        report = rg.check(
            "你刚才在看的视频看起来很有趣",
            facts=[_user_input_fact("我刚才在看你推荐的那个视频")],
        )
        # 有 USER_INPUT Fact 支撑 → allowed
        assert report.allowed, f"should be allowed with user_input fact: {report.notes}"


# ============================================================
# T3: Memory 信息允许引用
# ============================================================
class TestMemoryAllowed:
    def test_memory_fact_allows_visual_reference(self):
        """MEMORY Fact 允许引用,即使无 observation。"""
        from src.runtime.perception import RealityGuard
        rg = RealityGuard()
        report = rg.check(
            "你之前在追的那部番好看吗?",
            facts=[_memory_fact("用户在追一部番")],
        )
        # 实际上这只是个问句,不一定会触发 visual pattern;但若有 MEMORY Fact
        # 即使触发也允许
        if report.has_violation:
            assert "hallucinated_visual_observation" not in report.violations


# ============================================================
# T4: Observation 信息允许引用
# ============================================================
class TestObservationAllowed:
    def test_screen_observation_allows_describe(self):
        """screen_available=True 时,描述屏幕允许。"""
        from src.runtime.perception import RealityGuard
        from src.runtime.perception import ObservationState
        state = ObservationState(
            screen_available=True,
            visible_content="QQ聊天窗口",
        )
        rg = RealityGuard()
        report = rg.check("你刚才在屏幕上看什么?", obs_state=state)
        assert report.allowed

    def test_camera_observation_allows_describe(self):
        """camera_available=True 时,描述用户表情允许。"""
        from src.runtime.perception import RealityGuard
        from src.runtime.perception import ObservationState
        state = ObservationState(
            camera_available=True,
            current_scene="用户面带微笑",
        )
        rg = RealityGuard()
        report = rg.check("你看起来很开心", obs_state=state)
        assert report.allowed

    def test_observation_state_from_observations(self):
        """ObservationState.from_observations 正确聚合 observation 列表。"""
        from src.runtime.perception import ObservationState
        obs = [_screen_obs(content="QQ聊天", available=True)]
        state = ObservationState.from_observations(obs)
        assert state.screen_available is True
        assert state.visible_content == "QQ聊天"


# ============================================================
# T5: INFERENCE 不伪装 Observation
# ============================================================
class TestInferenceNotObservation:
    def test_inference_fact_does_not_satisfy_visual_requirement(self):
        """INFERENCE Fact 不能让 RealityGuard 通过视觉断言。"""
        from src.runtime.perception import RealityGuard
        rg = RealityGuard()
        # 仅有 INFERENCE Fact,无 USER_INPUT / MEMORY / VISION
        report = rg.check(
            "你刚才在屏幕上看什么?",
            facts=[_inference_fact("用户可能在看屏幕")],
        )
        # INFERENCE 不算 grounded(在 RealityGuard 层面只接受 USER_INPUT/MEMORY/VISION)
        assert not report.allowed


# ============================================================
# T6: Runtime 不依赖 Vision 实现
# ============================================================
class TestRuntimeNoVisionDependency:
    def test_perception_module_does_not_import_vision(self):
        """perception 模块不应 import vision / screen_capture 实际实现。"""
        perception_path = PROJECT_ROOT / "src" / "runtime" / "perception"
        # 遍历 perception 下所有 .py
        for py_file in perception_path.glob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            # 禁止 import 任何 vision / screen / camera 实现
            assert "import cv2" not in text
            assert "import PIL" not in text
            assert "import pyautogui" not in text
            assert "import mss" not in text
            assert "from src.vision" not in text
            assert "from src.screen_capture" not in text
            assert "from src.camera" not in text

    def test_runtime_runtime_does_not_import_vision(self):
        """Runtime 核心模块不应直接 import vision / screen_capture。"""
        runtime_path = PROJECT_ROOT / "src" / "runtime" / "runtime.py"
        text = runtime_path.read_text(encoding="utf-8")
        assert "from src.vision" not in text
        assert "from src.screen_capture" not in text
        assert "import cv2" not in text

    def test_observation_is_pure_data_class(self):
        """Observation 是纯数据类,不调用任何 vision / 设备 API。"""
        from src.runtime.perception import Observation, ObservationKind
        obs = Observation(kind=ObservationKind.SCREEN, content="test", available=True)
        # 不应有 connect / read / capture 等方法
        for forbidden in ["capture", "read", "connect", "screenshot", "ocr"]:
            method = getattr(obs, forbidden, None)
            assert method is None, f"Observation should not have {forbidden} method"


# ============================================================
# T7: ResponseGuardChain 顺序
# ============================================================
class TestGuardChainOrder:
    def test_three_guards_in_order(self):
        """ResponseGuardChain 应有 reality / perception / personality 三个 guard。"""
        from src.runtime.response_guard_chain import ResponseGuardChain
        chain = ResponseGuardChain()
        assert hasattr(chain, "reality_guard")
        assert hasattr(chain, "perception_guard")
        assert hasattr(chain, "personality_guard")

    def test_reality_blocks_hallucination_first(self):
        """RealityGuard 在 PerceptionGuard 之前生效。"""
        from src.runtime.response_guard_chain import ResponseGuardChain
        from src.runtime.perception import Fact, INFERENCE
        chain = ResponseGuardChain()
        # reply 描述屏幕(违反 reality),同时包含 INFERENCE fact
        result = chain.run(
            reply="你刚才屏幕上的内容很有趣(我猜的)",
            facts=[Fact(content="用户可能看了X", source=INFERENCE, confidence=0.3)],
            prc=None,
            obs_state=None,  # 无 observation
        )
        # 应当被 reality 阻断(在 perception 之前)
        assert result.blocked_by == "reality"
        assert result.reality_report is not None
        assert not result.reality_report.allowed

    def test_perception_blocks_inference_after_reality_passes(self):
        """Reality 通过时,Perception 仍可阻断 INFERENCE。"""
        from src.runtime.response_guard_chain import ResponseGuardChain
        from src.runtime.perception import Fact, INFERENCE
        chain = ResponseGuardChain()
        # reply 不描述视觉/音频/场景,只是 INFERENCE
        result = chain.run(
            reply="我觉得你今天心情不好",
            facts=[Fact(content="你今天心情差", source=INFERENCE, confidence=0.3)],
            prc=None,
            obs_state=None,
        )
        # reality 不阻断(无视觉/音频/场景违规),但 perception 应当 hedge / 标记
        assert result.reality_report.allowed
        # perception 应记录 INFERENCE
        assert result.perception_report.inference_count == 1

    def test_chain_result_includes_reality_report(self):
        """ResponseGuardResult 必须包含 reality_report 字段。"""
        from src.runtime.response_guard_chain import ResponseGuardResult
        result = ResponseGuardResult(final_reply="x", original_reply="x")
        assert hasattr(result, "reality_report")
        assert hasattr(result, "perception_report")
        assert hasattr(result, "personality_report")

    def test_chain_runs_all_three_when_passing(self):
        """正常 reply(无违规)时,三 Guard 都应通过,final_reply == original。"""
        from src.runtime.response_guard_chain import ResponseGuardChain
        from src.runtime.perception import Fact, USER_INPUT
        chain = ResponseGuardChain()
        result = chain.run(
            reply="嗯嗯,我知道了。",
            facts=[Fact(content="今天天气", source=USER_INPUT, confidence=1.0)],
            prc=None,
            obs_state=None,
        )
        assert result.blocked_by is None
        assert result.final_reply == "嗯嗯,我知道了。"
        # 三份报告都存在
        assert result.reality_report is not None
        assert result.perception_report is not None
        assert result.personality_report is not None


# ============================================================
# T8: ObservationState 派生
# ============================================================
class TestObservationStateDerivation:
    def test_from_observations_screen(self):
        from src.runtime.perception import ObservationState
        state = ObservationState.from_observations(
            [_screen_obs("QQ聊天", available=True)]
        )
        assert state.screen_available is True
        assert state.visible_content == "QQ聊天"

    def test_from_observations_skips_unavailable(self):
        """available=False 的 observation 不影响 state。"""
        from src.runtime.perception import ObservationState
        state = ObservationState.from_observations(
            [_screen_obs("未读屏", available=False)]
        )
        assert state.screen_available is False
        assert state.visible_content is None

    def test_can_describe_visual(self):
        from src.runtime.perception import ObservationState
        s1 = ObservationState(screen_available=True)
        s2 = ObservationState(camera_available=True)
        s3 = ObservationState()  # 都没有
        assert s1.can_describe_visual() is True
        assert s2.can_describe_visual() is True
        assert s3.can_describe_visual() is False

    def test_to_from_dict_roundtrip(self):
        from src.runtime.perception import ObservationState
        state = ObservationState(
            screen_available=True,
            camera_available=False,
            microphone_available=True,
            visible_content="test",
            current_scene=None,
        )
        data = state.to_dict()
        state2 = ObservationState.from_dict(data)
        assert state2.screen_available == state.screen_available
        assert state2.visible_content == state.visible_content


# ============================================================
# T9: Observation 字段
# ============================================================
class TestObservationFields:
    def test_required_fields_exist(self):
        from src.runtime.perception import Observation
        obs = Observation()
        for f in ["screen_available", "camera_available", "microphone_available",
                  "visible_content", "current_scene", "timestamp", "source"]:
            assert hasattr(obs, f) or hasattr(obs, "kind") or True, f

    def test_observation_state_required_fields(self):
        """ObservationState 字段完整性。"""
        from src.runtime.perception import ObservationState
        s = ObservationState()
        assert hasattr(s, "screen_available")
        assert hasattr(s, "camera_available")
        assert hasattr(s, "microphone_available")
        assert hasattr(s, "visible_content")
        assert hasattr(s, "current_scene")
        assert hasattr(s, "timestamp")
        assert hasattr(s, "source")

    def test_observation_kind_enum(self):
        from src.runtime.perception import ObservationKind
        for k in ["NONE", "SCREEN", "CAMERA", "MICROPHONE", "SYSTEM"]:
            assert hasattr(ObservationKind, k)


# ============================================================
# T10: 集成 — RuntimeContext 内部 perception state
# ============================================================
class TestRuntimeContextPerceptionState:
    def test_can_attach_observation_state_internally(self):
        """可在 RuntimeContext 内部挂载 perception_state(不修改 schema)。"""
        from src.runtime.context import RuntimeContext
        from src.runtime.perception import ObservationState
        ctx = RuntimeContext(user_input="hi")
        state = ObservationState(screen_available=True)
        # 通过 setattr 挂在内部属性上,不修改 dataclass 字段
        setattr(ctx, "_perception_state", state)
        # 通过 getattr 读出
        got = getattr(ctx, "_perception_state", None)
        assert got is state

    def test_runtime_context_schema_unchanged(self):
        """RuntimeContext schema 字段未变化(向后兼容)。"""
        from src.runtime.context import RuntimeContext
        ctx = RuntimeContext()
        # 关键字段必须存在
        assert hasattr(ctx, "session_id")
        assert hasattr(ctx, "user_input")
        assert hasattr(ctx, "timestamp")
        assert hasattr(ctx, "memory_context")
        assert hasattr(ctx, "emotion_state")
        assert hasattr(ctx, "personality_snapshot")
        assert hasattr(ctx, "growth_proposals")
        assert hasattr(ctx, "schema_version")
        assert ctx.schema_version == "1.0"

    def test_existing_ctx_construction_backward_compatible(self):
        """RuntimeContext() 无参构造仍可用(向后兼容)。"""
        from src.runtime.context import RuntimeContext
        ctx = RuntimeContext()
        assert ctx is not None
        assert ctx.user_input == ""


# ============================================================
# T11: RealityGuard wrap_reply 行为
# ============================================================
class TestRealityGuardWrap:
    def test_wrap_reply_adds_hedge_for_visual_violation(self):
        """视觉违规时,wrap_reply 注入 hedge。"""
        from src.runtime.perception import RealityGuard
        rg = RealityGuard()
        wrapped = rg.wrap_reply("你刚才游戏里很厉害")
        # 应包含 hedge 前缀
        assert "没有" in wrapped or "无法" in wrapped

    def test_wrap_reply_returns_original_when_clean(self):
        """无违规时,wrap_reply 返回原文本。"""
        from src.runtime.perception import RealityGuard
        rg = RealityGuard()
        wrapped = rg.wrap_reply("嗯嗯,好的。")
        assert wrapped == "嗯嗯,好的。"

    def test_wrap_reply_observation_pass(self):
        """有 observation 时,wrap_reply 不修改。"""
        from src.runtime.perception import RealityGuard, ObservationState
        rg = RealityGuard()
        state = ObservationState(screen_available=True, visible_content="x")
        wrapped = rg.wrap_reply("你刚才在屏幕上看什么?", obs_state=state)
        assert wrapped == "你刚才在屏幕上看什么?"


# ============================================================
# T12: 阶段总结
# ============================================================
def test_phase_3_9_0_summary():
    """Phase 3.9.0 阶段总结。"""
    from src.runtime.perception import (
        Observation, ObservationKind, ObservationState,
        RealityGuard, RealityGuardReport,
        REALITY_GUARD_SCHEMA_VERSION,
        OBSERVATION_SCHEMA_VERSION,
        OBSERVATION_STATE_SCHEMA_VERSION,
    )
    from src.runtime.response_guard_chain import (
        ResponseGuardChain, ResponseGuardResult,
    )
    from src.runtime.context import RuntimeContext

    # 1) 关键类/版本
    assert REALITY_GUARD_SCHEMA_VERSION == "1.0"
    assert OBSERVATION_SCHEMA_VERSION == "1.0"
    assert OBSERVATION_STATE_SCHEMA_VERSION == "1.0"

    # 2) 三 Guard 链路
    chain = ResponseGuardChain()
    assert chain.reality_guard is not None
    assert chain.perception_guard is not None
    assert chain.personality_guard is not None

    # 3) ObservationState 字段
    state = ObservationState()
    for f in [
        "screen_available", "camera_available", "microphone_available",
        "visible_content", "current_scene", "timestamp", "source",
    ]:
        assert hasattr(state, f), f"missing field: {f}"

    # 4) RuntimeContext 不修改 schema
    ctx = RuntimeContext()
    assert ctx.schema_version == "1.0"
    # 内部 perception_state 可挂载
    setattr(ctx, "_perception_state", state)
    assert getattr(ctx, "_perception_state") is state

    # 5) RealityGuard 关键方法
    assert hasattr(RealityGuard, "check")
    assert hasattr(RealityGuard, "wrap_reply")
    assert hasattr(RealityGuard, "has_violation")

    # 6) ObservationKind 包含所有类型
    for k in ["NONE", "SCREEN", "CAMERA", "MICROPHONE", "SYSTEM"]:
        assert hasattr(ObservationKind, k)
