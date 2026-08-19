# -*- coding: utf-8 -*-
"""
tests/test_runtime_stage_contract.py

Phase 4.0.1 风险点1专项：Stage → Method Name 冻结契约。

SPEC v0.2 风险点1：
  LifecycleExecutor.execute(core, event, ctx) 会按固定方法名调用：
    core._stage_00_control_check()
    core._stage_01_receive_event()
    ...
    core._stage_16_response()
  这些名字必须冻结，否则 LifecycleExecutor 找不到方法，Runtime 空跑。

本文件 = 权威冻结表（FROZEN CONTRACT），任何修改必须同步更新：
  - src/runtime/lifecycle.py: STAGE_TO_METHOD_NAME 列表
  - src/runtime/runtime_core.py: 17 个 _stage_XX 方法
  - 所有对 stage 枚举 / 方法名映射的假设

共 17+ 断言覆盖 17 个 stage。
"""
from __future__ import annotations

import unittest


# =====================================================================
# FROZEN: 17 个 Stage → 方法名映射（此表与 lifecycle.py STAGE_TO_METHOD_NAME
#         必须严格一致；任何一侧修改都会被断言卡死）
# =====================================================================
_FROZEN_CONTRACT: list = [
    # (stage_enum_name, expected_method_name)
    ("CONTROL_CHECK",            "_stage_00_control_check"),
    ("RECEIVE_EVENT",            "_stage_01_receive_event"),
    ("MEMORY_RETRIEVAL",         "_stage_02_memory_retrieval"),
    ("EMOTION_UPDATE",           "_stage_03_emotion_update"),
    ("GROWTH_EVALUATION",        "_stage_04_growth_evaluation"),
    ("PERSONALITY_UPDATE",       "_stage_05_personality_update"),
    ("PERSONALITY_CONTEXT_BUILD","_stage_06_personality_context_build"),
    ("PERCEPTION_OBSERVATION",   "_stage_07_perception_observation"),
    ("PERCEPTION_ANALYSIS",      "_stage_08_perception_analysis"),
    ("SELF_MODEL_BUILD",         "_stage_09_self_model_build"),
    ("SELF_MODEL_EVOLUTION",     "_stage_10_self_model_evolution"),
    ("SELF_MODEL_REFLECTION",    "_stage_11_self_model_reflection"),
    ("SELF_MODEL_VALIDATION",    "_stage_12_self_model_validation"),
    ("SELF_MODEL_PERSISTENCE",   "_stage_13_self_model_persistence"),
    ("RESPONSE_GENERATION",      "_stage_14_response_generation"),
    ("GUARD_CHAIN",              "_stage_15_guard_chain"),
    ("RESPONSE",                 "_stage_16_response"),
]


class TestStageContractFrozen(unittest.TestCase):
    """冻结 17 Stage → Method 映射。任何对 stage 方法名的修改都会让本套件爆炸。"""

    # -----------------------------------------------------------------
    # 1. 顺序 + 数量契约
    # -----------------------------------------------------------------
    def test_contract_count_is_17(self):
        """冻结契约表必须恰好 17 项（对应 process() 遍历的 17 个阶段）。"""
        self.assertEqual(len(_FROZEN_CONTRACT), 17)

    def test_lifecycle_order_count_is_17(self):
        """RUNTIME_LIFECYCLE_ORDER 必须恰好 17 项（冻结数量）。"""
        from src.runtime.stages import RUNTIME_LIFECYCLE_ORDER

        self.assertEqual(len(RUNTIME_LIFECYCLE_ORDER), 17)

    # -----------------------------------------------------------------
    # 2. lifecycle.py STAGE_TO_METHOD_NAME × 17 断言（与冻结表逐项对齐）
    # -----------------------------------------------------------------
    def test_lifecycle_matches_frozen_contract(self):
        """lifecycle.STAGE_TO_METHOD_NAME 与 FROZEN_CONTRACT 逐项相等。"""
        from src.runtime.lifecycle_executor import STAGE_TO_METHOD_NAME
        from src.runtime.stages import RuntimeStage

        self.assertEqual(
            len(STAGE_TO_METHOD_NAME), 17,
            "lifecycle.py STAGE_TO_METHOD_NAME 必须恰好 17 项",
        )
        # 逐项比对：17 断言
        for idx, (expected_enum_name, expected_method) in enumerate(_FROZEN_CONTRACT):
            stage_enum, actual_method = STAGE_TO_METHOD_NAME[idx]
            self.assertEqual(
                stage_enum.name, expected_enum_name,
                f"第 {idx} 项 stage enum 名称不一致（契约冻结={expected_enum_name}）",
            )
            self.assertEqual(
                actual_method, expected_method,
                f"第 {idx} 项 {expected_enum_name} → 方法名冻结为 {expected_method}，"
                f"实际为 {actual_method}",
            )

    # -----------------------------------------------------------------
    # 3. runtime_core.RuntimeCore 类 × 17 断言：必须存在所有 stage 方法
    # -----------------------------------------------------------------
    def test_runtimecore_has_all_17_stage_methods(self):
        """runtime_core.RuntimeCore 必须存在全部 17 个 _stage_XX 方法。"""
        from src.runtime.runtime_core import RuntimeCore

        missing: list = []
        for expected_enum_name, expected_method in _FROZEN_CONTRACT:
            fn = getattr(RuntimeCore, expected_method, None)
            if not callable(fn):
                missing.append((expected_enum_name, expected_method))
        # 逐条断言（共 17 断言）
        for expected_enum_name, expected_method in _FROZEN_CONTRACT:
            fn = getattr(RuntimeCore, expected_method, None)
            self.assertTrue(
                callable(fn),
                f"RuntimeCore 缺少冻结阶段方法：{expected_enum_name} → {expected_method}",
            )
        # 汇总兜底（确保确实是 17 个都存在）
        self.assertEqual(
            len(missing), 0,
            f"缺失 {len(missing)} 个冻结 stage 方法：{missing}",
        )

    # -----------------------------------------------------------------
    # 4. 顺序一致性契约：枚举顺序 × STAGE_TO_METHOD_NAME 顺序 ×
    #    RUNTIME_LIFECYCLE_ORDER 顺序三者完全对齐
    # -----------------------------------------------------------------
    def test_all_three_orderings_align(self):
        """枚举在 lifecycle 映射表中的顺序与 RUNTIME_LIFECYCLE_ORDER 对齐。"""
        from src.runtime.lifecycle_executor import STAGE_TO_METHOD_NAME
        from src.runtime.stages import RUNTIME_LIFECYCLE_ORDER

        stages_in_order = [s for s, _ in STAGE_TO_METHOD_NAME]
        self.assertEqual(
            [s.name for s in stages_in_order],
            [s.name for s in RUNTIME_LIFECYCLE_ORDER],
            "STAGE_TO_METHOD_NAME 中的 stage 顺序必须与 RUNTIME_LIFECYCLE_ORDER 完全一致",
        )

    # -----------------------------------------------------------------
    # 5. RUNTIME_LIFECYCLE_ORDER 首末项契约（安全层前置 + Response 收尾）
    # -----------------------------------------------------------------
    def test_first_stage_is_control_check(self):
        """第 0 阶段 = CONTROL_CHECK（安全层前置，不可调换位置）。"""
        from src.runtime.stages import RUNTIME_LIFECYCLE_ORDER, RuntimeStage

        self.assertEqual(RUNTIME_LIFECYCLE_ORDER[0], RuntimeStage.CONTROL_CHECK)

    def test_second_stage_is_receive_event(self):
        """第 1 阶段 = RECEIVE_EVENT（P0-修改3：后 exactly once 调 _on_event）。"""
        from src.runtime.stages import RUNTIME_LIFECYCLE_ORDER, RuntimeStage

        self.assertEqual(RUNTIME_LIFECYCLE_ORDER[1], RuntimeStage.RECEIVE_EVENT)

    def test_last_stage_is_response(self):
        """最后阶段 = RESPONSE（17 阶段收尾，ctx.finalized_reply 落地）。"""
        from src.runtime.stages import RUNTIME_LIFECYCLE_ORDER, RuntimeStage

        self.assertEqual(RUNTIME_LIFECYCLE_ORDER[-1], RuntimeStage.RESPONSE)

    def test_response_generation_is_stage_14(self):
        """阶段 14 = RESPONSE_GENERATION（第 15 项，index 14）。"""
        from src.runtime.stages import RUNTIME_LIFECYCLE_ORDER, RuntimeStage

        self.assertEqual(
            RUNTIME_LIFECYCLE_ORDER[14], RuntimeStage.RESPONSE_GENERATION,
        )

    def test_guard_chain_is_stage_15(self):
        """阶段 15 = GUARD_CHAIN（在 response 前校验，index 15）。"""
        from src.runtime.stages import RUNTIME_LIFECYCLE_ORDER, RuntimeStage

        self.assertEqual(RUNTIME_LIFECYCLE_ORDER[15], RuntimeStage.GUARD_CHAIN)


# =====================================================================
# pytest 入口
# =====================================================================
if __name__ == "__main__":
    unittest.main()
