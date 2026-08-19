"""
Phase 4.0 R2.6.4-C.2 PromptContextBuilder Gates (PCB-1 ~ PCB-10+)

本 Builder 是 Context Assembly Layer：
    read → merge → format → return
不决定说什么 / 不 modify 任何状态 / 不产生 GrowthProposal / Reflection / LLM 调用。
"""

from __future__ import annotations

import ast
import copy
import inspect
from typing import Any, Dict, List

import pytest

from src.context.self_context_builder import SelfContextBuilder
from src.context.self_context_schema import create_empty_self_context
from src.context.prompt_context_builder import PromptContextBuilder
from src.context.prompt_context_schema import (
    FORBIDDEN_CALLS,
    FORBIDDEN_IMPORTS,
    validate_prompt_context_shape,
)


# ─────────────────────────────────────────────────────────
# 辅助：构造合法 SelfContext（来自 SelfContextBuilder.build）
# ─────────────────────────────────────────────────────────
def _make_self_context_v1():
    scb = SelfContextBuilder()
    return scb.build(mode="read_only_identity")


# ─────────────────────────────────────────────────────────
# PCB-1: 数据来源正确（6 份上下文只读取值，不重新计算）
# ─────────────────────────────────────────────────────────
class TestPCB1SourceCorrectness:
    def test_self_context_injected_verbatim(self) -> None:
        """self_context 来自 Builder 输出后原样注入（仅 deepcopy，不改动内容）。"""
        sc = _make_self_context_v1()
        sc_id_before = (sc["version"], sc["continuity_status"], sc["injection_policy"]["mode"])
        pc = PromptContextBuilder().build(self_context=sc)
        assert pc["self_context"]["version"] == sc_id_before[0]
        assert pc["self_context"]["continuity_status"] == sc_id_before[1]
        assert pc["self_context"]["injection_policy"]["mode"] == sc_id_before[2]
        # 子字段内容完全一致
        for k in ("identity_summary", "personality_summary", "growth_summary", "reflection_summary"):
            assert pc["self_context"][k] == sc[k], f"SC 字段 {k} 在装配时被修改"

    def test_system_identity_values_forwarded(self) -> None:
        si = {
            "name": "羽依",
            "system_role": "assistant_ai",
            "anchor_markers": ["陪伴", "安全", "真实"],
            "build_tag": "demo-r264c",
        }
        pc = PromptContextBuilder().build(system_identity=si)
        assert pc["system_identity"]["name"] == "羽依"
        assert pc["system_identity"]["system_role"] == "assistant_ai"
        assert pc["system_identity"]["anchor_markers"] == ["陪伴", "安全", "真实"]
        assert pc["system_identity"]["build_tag"] == "demo-r264c"

    def test_user_context_values_forwarded(self) -> None:
        ui = {
            "user_id": "u_123",
            "display_name": "小晨",
            "conversation_role": "user",
            "relationship_tier": "friend",
            "preferred_name": "晨哥",
        }
        pc = PromptContextBuilder().build(user_info=ui)
        assert pc["user_context"]["user_id"] == "u_123"
        assert pc["user_context"]["display_name"] == "小晨"
        assert pc["user_context"]["conversation_role"] == "user"
        assert pc["user_context"]["relationship_tier"] == "friend"
        assert pc["user_context"]["preferred_name"] == "晨哥"

    def test_memory_context_values_forwarded_with_count(self) -> None:
        mems = ["一起讨论过 AI 绘画", "对方提到喜欢猫"]
        pc = PromptContextBuilder().build(context_memories=mems, memory_scope="last_7_days")
        assert pc["memory_context"]["context_memories"] == ["一起讨论过 AI 绘画", "对方提到喜欢猫"]
        assert pc["memory_context"]["memory_count"] == 2
        assert pc["memory_context"]["recall_scope"] == "last_7_days"

    def test_emotion_context_dict_source(self) -> None:
        em = {"emotion_mood": "warm", "intensity": 0.72, "context_note": "愉快的话题"}
        pc = PromptContextBuilder().build(emotion_state=em)
        assert pc["emotion_context"]["emotion_mood"] == "warm"
        assert abs(pc["emotion_context"]["intensity"] - 0.72) < 1e-6
        assert pc["emotion_context"]["context_note"] == "愉快的话题"
        # trace 标记 emotion_source=dict
        assert pc["context_trace"]["emotion_source"] == "emotion_state:dict"

    def test_relationship_context_dict_source(self) -> None:
        rel = {"closeness_score": 0.85, "dynamic_traits": ["默契", "有共同兴趣"], "trust_level": "high"}
        pc = PromptContextBuilder().build(relationship_snapshot=rel)
        assert abs(pc["relationship_context"]["closeness_score"] - 0.85) < 1e-6
        assert pc["relationship_context"]["dynamic_traits"] == ["默契", "有共同兴趣"]
        assert pc["relationship_context"]["trust_level"] == "high"

    def test_task_context_values_forwarded(self) -> None:
        tk = {
            "turn_number": 7,
            "session_id": "sess_abc",
            "current_topic": "AI 绘画学习",
            "scenario": "creative_collaboration",
        }
        pc = PromptContextBuilder().build(task_info=tk)
        assert pc["task_context"]["turn_number"] == 7
        assert pc["task_context"]["session_id"] == "sess_abc"
        assert pc["task_context"]["current_topic"] == "AI 绘画学习"
        assert pc["task_context"]["scenario"] == "creative_collaboration"


# ─────────────────────────────────────────────────────────
# PCB-2: merge 合并结果形状正确（所有组合都必须 validate）
# ─────────────────────────────────────────────────────────
class TestPCB2MergeShapeAlwaysValid:
    def test_none_all_uses_defaults(self) -> None:
        pc = PromptContextBuilder().build()
        validate_prompt_context_shape(pc)
        # 默认值：羽依 / companion_ai / unknown tier / neutral mood / unknown scenario
        assert pc["system_identity"]["name"] == "羽依"
        assert pc["system_identity"]["system_role"] == "companion_ai"
        assert pc["user_context"]["relationship_tier"] == "unknown"
        assert pc["emotion_context"]["emotion_mood"] == "neutral"
        assert pc["task_context"]["scenario"] == "unknown"

    def test_all_inputs_merged_still_valid(self) -> None:
        pc = PromptContextBuilder().build(
            system_identity={"name": "羽依", "system_role": "assistant_ai", "anchor_markers": ["真实"], "build_tag": "t"},
            self_context=_make_self_context_v1(),
            user_info={"user_id": "u", "display_name": "d", "conversation_role": "user", "relationship_tier": "friend", "preferred_name": "p"},
            context_memories=["m1", "m2"],
            memory_scope="1d",
            emotion_state={"emotion_mood": "happy", "intensity": 0.6, "context_note": "c"},
            relationship_snapshot={"closeness_score": 0.2, "dynamic_traits": ["dt"], "trust_level": "medium"},
            task_info={"turn_number": 3, "session_id": "s", "current_topic": "tpc", "scenario": "casual_chat"},
            assembly_mode="standard",
        )
        validate_prompt_context_shape(pc)


# ─────────────────────────────────────────────────────────
# PCB-3: context_trace 审计轨迹（全字段事实匹配）
# ─────────────────────────────────────────────────────────
class TestPCB3ContextTraceAudit:
    def test_trace_points_to_sc_version_and_policy(self) -> None:
        sc = _make_self_context_v1()
        pc = PromptContextBuilder().build(self_context=sc, assembly_mode="standard")
        assert pc["context_trace"]["self_context_version"] == sc["version"]
        assert pc["context_trace"]["self_context_continuity"] == sc["continuity_status"]
        assert pc["context_trace"]["self_context_injection_mode"] == sc["injection_policy"]["mode"]

    def test_trace_memory_count_matches_clip(self) -> None:
        # 20 条 memories，默认 cap 10 → memory_count=10
        mems = [f"记忆{i}" for i in range(20)]
        pc = PromptContextBuilder().build(context_memories=mems, memory_scope="1d")
        assert pc["context_trace"]["memory_count"] == 10
        assert pc["context_trace"]["memory_scope"] == "1d"

    def test_trace_trust_level_matches_relationship(self) -> None:
        rel = {"closeness_score": 0.9, "dynamic_traits": [], "trust_level": "high"}
        pc = PromptContextBuilder().build(relationship_snapshot=rel)
        assert pc["context_trace"]["relationship_trust_level"] == "high"

    def test_trace_build_version_assigned_once(self) -> None:
        b = PromptContextBuilder()
        pc1 = b.build()
        assert pc1["context_trace"]["build_version"] == pc1["version"] == 1
        pc2 = b.build()
        assert pc2["context_trace"]["build_version"] == pc2["version"] == 2

    def test_trace_assembly_mode_matches_input(self) -> None:
        for mode in ("standard", "minimal", "identity_only"):
            pc = PromptContextBuilder().build(assembly_mode=mode)
            assert pc["context_trace"]["assembly_mode"] == mode


# ─────────────────────────────────────────────────────────
# PCB-4: 不 modify 输入对象（只读）
# ─────────────────────────────────────────────────────────
class TestPCB4NoStateMutation:
    def test_all_input_mappings_and_list_unchanged(self) -> None:
        si = {"name": "羽依", "system_role": "companion_ai", "anchor_markers": ["A"], "build_tag": "T"}
        sc = _make_self_context_v1()
        sc_copy = copy.deepcopy(sc)
        ui = {"user_id": "u", "display_name": None, "conversation_role": "user", "relationship_tier": "unknown", "preferred_name": None}
        mems = ["A", "B", "C"]
        em = {"emotion_mood": "curious", "intensity": 0.4, "context_note": None}
        rel = {"closeness_score": 0.3, "dynamic_traits": ["亲近"], "trust_level": "medium"}
        tk = {"turn_number": 3, "session_id": "s1", "current_topic": None, "scenario": "question_answer"}

        PromptContextBuilder().build(
            system_identity=si,
            self_context=sc,
            user_info=ui,
            context_memories=mems,
            emotion_state=em,
            relationship_snapshot=rel,
            task_info=tk,
        )

        # SC 输入对象未被 mutate（deepcompare）
        assert sc == sc_copy
        # 其他 dict 完全一致
        assert si == {"name": "羽依", "system_role": "companion_ai", "anchor_markers": ["A"], "build_tag": "T"}
        assert ui == {"user_id": "u", "display_name": None, "conversation_role": "user", "relationship_tier": "unknown", "preferred_name": None}
        assert mems == ["A", "B", "C"]
        assert em == {"emotion_mood": "curious", "intensity": 0.4, "context_note": None}
        assert rel == {"closeness_score": 0.3, "dynamic_traits": ["亲近"], "trust_level": "medium"}
        assert tk == {"turn_number": 3, "session_id": "s1", "current_topic": None, "scenario": "question_answer"}


# ─────────────────────────────────────────────────────────
# PCB-5: 红线：Builder 不 import 红线模块 / 不调用红线 API
# ─────────────────────────────────────────────────────────
class TestPCB5ASTRedlines:
    @staticmethod
    def _ast_imports() -> List[str]:
        import src.context.prompt_context_builder as mod

        tree = ast.parse(inspect.getsource(mod))
        imports: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        return imports

    @staticmethod
    def _ast_call_names() -> List[str]:
        import src.context.prompt_context_builder as mod

        tree = ast.parse(inspect.getsource(mod))
        calls: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    calls.append(func.id)
                elif isinstance(func, ast.Attribute):
                    calls.append(func.attr)
        return calls

    def test_no_forbidden_imports(self) -> None:
        imports = set(self._ast_imports())
        # FORBIDDEN_IMPORTS 来自 schema；PromptContext builder 禁止触发这些
        bad = [m for m in FORBIDDEN_IMPORTS if m in imports or any(i.startswith(m) for i in imports)]
        assert bad == [], f"Builder 含红线 import: {bad}"

    def test_no_forbidden_call_names(self) -> None:
        names = set(self._ast_call_names())
        bad = [n for n in FORBIDDEN_CALLS if n in names]
        assert bad == [], f"Builder 含红线调用: {bad}"


# ─────────────────────────────────────────────────────────
# PCB-6: 膨胀保护（memories / traits / strings）
# ─────────────────────────────────────────────────────────
class TestPCB6InflationProtection:
    def test_memories_1000_still_capped_to_default_10(self) -> None:
        mems = [f"记忆{i}" for i in range(1000)]
        pc = PromptContextBuilder().build(context_memories=mems)
        assert len(pc["memory_context"]["context_memories"]) == 10
        assert pc["memory_context"]["memory_count"] == 10

    def test_relationship_traits_capped(self) -> None:
        traits = [f"关系特征{i}" for i in range(50)]
        rel = {"closeness_score": 0.5, "dynamic_traits": traits, "trust_level": "medium"}
        pc = PromptContextBuilder().build(relationship_snapshot=rel)
        # 默认 max_relationship_traits=6
        assert len(pc["relationship_context"]["dynamic_traits"]) == 6

    def test_string_length_capped_to_280(self) -> None:
        long_str = "A" * 4000
        ui = {
            "user_id": long_str,
            "display_name": long_str,
            "conversation_role": "user",
            "relationship_tier": "unknown",
            "preferred_name": long_str,
        }
        pc = PromptContextBuilder().build(
            user_info=ui,
            context_memories=[long_str],
            memory_scope=long_str,
            emotion_state={"emotion_mood": "neutral", "intensity": 0.5, "context_note": long_str},
            relationship_snapshot={"closeness_score": 0.1, "dynamic_traits": [long_str], "trust_level": "low"},
            task_info={"turn_number": 1, "session_id": long_str, "current_topic": long_str, "scenario": "unknown"},
        )
        # 检查关键字段 len <= 280
        assert len(pc["user_context"]["user_id"]) <= 280
        assert len(pc["user_context"]["preferred_name"]) <= 280
        assert len(pc["memory_context"]["context_memories"][0]) <= 280
        assert len(pc["memory_context"]["recall_scope"]) <= 280
        assert len(pc["emotion_context"]["context_note"]) <= 280
        assert len(pc["task_context"]["session_id"]) <= 280
        assert len(pc["task_context"]["current_topic"]) <= 280

    def test_anchor_markers_capped(self) -> None:
        markers = [f"锚点{i}" for i in range(100)]
        si = {"name": "羽依", "system_role": "companion_ai", "anchor_markers": markers, "build_tag": "t"}
        pc = PromptContextBuilder().build(system_identity=si)
        assert len(pc["system_identity"]["anchor_markers"]) <= 10  # DEFAULT_MAX_ANCHOR_MARKERS


# ─────────────────────────────────────────────────────────
# PCB-7: 返回 shape 永远合法（异常/Nones/SC 非法时都能 validate）
# ─────────────────────────────────────────────────────────
class TestPCB7AlwaysValidShape:
    def test_no_args_passes(self) -> None:
        validate_prompt_context_shape(PromptContextBuilder().build())

    def test_sc_none_passes(self) -> None:
        # None SC → 自动使用空 SelfContext（create_empty_self_context v1）
        pc = PromptContextBuilder().build(self_context=None)
        validate_prompt_context_shape(pc)
        assert pc["self_context"]["version"] == 1

    def test_bad_enum_values_still_fallback_to_whitelist(self) -> None:
        ui = {"user_id": "x", "display_name": "y", "conversation_role": "bad!", "relationship_tier": "family!", "preferred_name": "p"}
        em = {"emotion_mood": "rage_mode_bad", "intensity": 999.0, "context_note": None}
        rel = {"closeness_score": -3, "dynamic_traits": ["t"], "trust_level": "super_high_bad"}
        tk = {"turn_number": -5, "session_id": "s", "current_topic": "t", "scenario": "nonsense_scenario"}
        pc = PromptContextBuilder().build(user_info=ui, emotion_state=em, relationship_snapshot=rel, task_info=tk)
        validate_prompt_context_shape(pc)
        # fallback
        assert pc["user_context"]["conversation_role"] == "user"
        assert pc["user_context"]["relationship_tier"] == "unknown"
        assert pc["emotion_context"]["emotion_mood"] == "neutral"
        assert pc["emotion_context"]["intensity"] == 0.5
        assert pc["relationship_context"]["closeness_score"] == 0.0
        assert pc["relationship_context"]["trust_level"] == "unknown"
        assert pc["task_context"]["turn_number"] == 0
        assert pc["task_context"]["scenario"] == "unknown"


# ─────────────────────────────────────────────────────────
# PCB-8: version 严格递增（成功和异常都递增，每次 +1）
# ─────────────────────────────────────────────────────────
class TestPCB8VersionIncrement:
    def test_version_increments_strictly_1_2_3(self) -> None:
        b = PromptContextBuilder()
        assert b.build()["version"] == 1
        assert b.build()["version"] == 2
        assert b.build()["version"] == 3

    def test_version_increments_despite_exception(self) -> None:
        b = PromptContextBuilder()

        class EvilEmotion:
            @property
            def emotion_mood(self) -> None:
                raise RuntimeError("boom")

        # version 1: 成功
        assert b.build()["version"] == 1
        # version 2: 抛异常 → degraded，version=2
        degraded = b.build(emotion_state=EvilEmotion())
        assert degraded["version"] == 2
        # version 3: 再成功 → 3（没被重复 +1）
        assert b.build()["version"] == 3


# ─────────────────────────────────────────────────────────
# PCB-9: Deterministic（同输入 → 输出同内容，忽略唯一 version）
# ─────────────────────────────────────────────────────────
class TestPCB9Deterministic:
    def test_same_inputs_same_output_content(self) -> None:
        sc = _make_self_context_v1()
        b1 = PromptContextBuilder()
        b2 = PromptContextBuilder()
        pc1 = b1.build(
            system_identity={"name": "羽依", "system_role": "companion_ai", "anchor_markers": ["A"], "build_tag": "t"},
            self_context=sc,
            user_info={"user_id": "u1", "display_name": "d1", "conversation_role": "user", "relationship_tier": "friend", "preferred_name": "p1"},
            context_memories=["m1", "m2"],
            memory_scope="1h",
            emotion_state={"emotion_mood": "happy", "intensity": 0.8, "context_note": "n1"},
            relationship_snapshot={"closeness_score": 0.7, "dynamic_traits": ["默契"], "trust_level": "high"},
            task_info={"turn_number": 5, "session_id": "s1", "current_topic": "t1", "scenario": "casual_chat"},
        )
        pc2 = b2.build(
            system_identity={"name": "羽依", "system_role": "companion_ai", "anchor_markers": ["A"], "build_tag": "t"},
            self_context=sc,
            user_info={"user_id": "u1", "display_name": "d1", "conversation_role": "user", "relationship_tier": "friend", "preferred_name": "p1"},
            context_memories=["m1", "m2"],
            memory_scope="1h",
            emotion_state={"emotion_mood": "happy", "intensity": 0.8, "context_note": "n1"},
            relationship_snapshot={"closeness_score": 0.7, "dynamic_traits": ["默契"], "trust_level": "high"},
            task_info={"turn_number": 5, "session_id": "s1", "current_topic": "t1", "scenario": "casual_chat"},
        )
        # build_version/version 不同实例，忽略这两项；其余必须完全一致
        v1, v2 = pc1.pop("version"), pc2.pop("version")
        t1 = pc1["context_trace"].pop("build_version")
        t2 = pc2["context_trace"].pop("build_version")
        assert isinstance(v1, int) and isinstance(v2, int) and v1 == 1 and v2 == 1
        assert t1 == 1 and t2 == 1
        assert pc1 == pc2


# ─────────────────────────────────────────────────────────
# PCB-10: 异常隔离（100% 不抛；degraded 仍合法）
# ─────────────────────────────────────────────────────────
class TestPCB10ExceptionIsolation:
    def test_evil_property_does_not_propagate(self) -> None:
        class EvilSC:
            def __len__(self) -> int:
                return 1

            def __iter__(self):
                raise RuntimeError("不能转 dict")

            def items(self):  # dict protocol 但抛
                raise RuntimeError("items 爆掉")

        # build 应返回 degraded，不抛
        pc = PromptContextBuilder().build(self_context=EvilSC())  # type: ignore[arg-type]
        validate_prompt_context_shape(pc)
        assert pc["context_trace"]["assembly_mode"] == "degraded"
        assert "prompt_context_builder_degraded:" in pc["context_trace"]["emotion_source"]

    def test_degraded_memory_scope_has_marker(self) -> None:
        class EvilMem(list):
            def __init__(self) -> None:
                super().__init__(["will_boom_on_iter"])

            def __iter__(self):  # type: ignore[override]
                raise RuntimeError("cannot iterate memories")

        pc = PromptContextBuilder().build(context_memories=EvilMem())
        validate_prompt_context_shape(pc)
        assert "prompt_context_builder_degraded:" in (pc["memory_context"]["recall_scope"] or "")


# ─────────────────────────────────────────────────────────
# PCB-11: assembly_mode 非法值 fallback → standard；仍 legal
# ─────────────────────────────────────────────────────────
class TestPCB11AssemblyModeFallback:
    def test_invalid_assembly_maps_to_standard(self) -> None:
        pc = PromptContextBuilder().build(assembly_mode="make_personality_grow")
        validate_prompt_context_shape(pc)
        assert pc["context_trace"]["assembly_mode"] == "standard"
