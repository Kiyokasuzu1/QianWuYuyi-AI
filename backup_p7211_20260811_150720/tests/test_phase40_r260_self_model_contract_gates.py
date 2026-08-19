"""
Phase 4.0 — R2.6.0 Gates: SelfModel Contract Freeze

Gates（SM-1 ~ SM-12）：
  SM-1  顶层 7 字段冻结 exact（5 view + version + generated_at）
  SM-2  缺少任一冻结字段 → ValueError
  SM-3  多余顶层字段 → ValueError
  SM-4  version 必须非负整数
  SM-5  generated_at 必须非空字符串
  SM-6  每个 view 必须是 dict
  SM-7  create_empty_self_model() 产出合法空模型
  SM-8  SelfModelSnapshot 不可变（frozen；setattr 被拒）
  SM-9  SelfModelSnapshot.to_dict() 返回值拷贝（修改外层 dict 不影响 snapshot）
  SM-10 禁止依赖 AST 扫描：self_model 模块不 import FORBIDDEN_IMPORTS 中的任何模块
  SM-11 禁止调用 AST 扫描：self_model 模块不调用 FORBIDDEN_CALLS 中的任何函数
  SM-12 SOURCE_MAP 覆盖所有 5 个 view（无遗漏）
"""
from __future__ import annotations

import ast
import inspect
import unittest
from typing import Any, Dict

from src.self_model import (
    SelfModel,
    SelfModelSnapshot,
    FROZEN_TOP_LEVEL_KEYS,
    SOURCE_MAP,
    FORBIDDEN_IMPORTS,
    FORBIDDEN_CALLS,
    validate_self_model_shape,
    create_empty_self_model,
    build_snapshot,
)
from src.self_model.self_model_schema import (
    IdentityView,
    PersonalityView,
    DevelopmentView,
    ContradictionView,
    CapabilityView,
)


class TestPhase40R260SM1ToSM6ShapeFrozen(unittest.TestCase):
    """SM-1 ~ SM-6: 顶层字段冻结 + 类型校验"""

    def test_sm1_frozen_keys_exact_7(self):
        self.assertEqual(len(FROZEN_TOP_LEVEL_KEYS), 7)
        expected = {
            "identity_view", "personality_view", "development_view",
            "contradiction_view", "capability_view", "version", "generated_at",
        }
        self.assertEqual(set(FROZEN_TOP_LEVEL_KEYS), expected)

    def test_sm2_missing_field_raises(self):
        model = create_empty_self_model()
        del model["version"]
        with self.assertRaises(ValueError) as ctx:
            validate_self_model_shape(model)
        self.assertIn("version", str(ctx.exception))

    def test_sm3_extra_field_raises(self):
        model = create_empty_self_model()
        model["emotion_view"] = {}  # 不允许 emotion
        with self.assertRaises(ValueError) as ctx:
            validate_self_model_shape(model)
        self.assertIn("emotion_view", str(ctx.exception))

    def test_sm4_version_must_be_non_neg_int(self):
        model = create_empty_self_model()
        model["version"] = -1
        with self.assertRaises(ValueError):
            validate_self_model_shape(model)
        model["version"] = "zero"  # type: ignore[assignment]
        with self.assertRaises(ValueError):
            validate_self_model_shape(model)

    def test_sm5_generated_at_must_be_str(self):
        model = create_empty_self_model()
        model["generated_at"] = ""
        with self.assertRaises(ValueError):
            validate_self_model_shape(model)
        model["generated_at"] = 12345  # type: ignore[assignment]
        with self.assertRaises(ValueError):
            validate_self_model_shape(model)

    def test_sm6_each_view_must_be_dict(self):
        model = create_empty_self_model()
        model["identity_view"] = "not a dict"  # type: ignore[assignment]
        with self.assertRaises(ValueError) as ctx:
            validate_self_model_shape(model)
        self.assertIn("identity_view", str(ctx.exception))


class TestPhase40R260SM7EmptyModel(unittest.TestCase):
    """SM-7: create_empty_self_model 产出合法"""

    def test_sm7_empty_model_valid(self):
        m = create_empty_self_model()
        validate_self_model_shape(m)  # 不抛异常
        self.assertEqual(m["version"], 0)
        self.assertTrue(m["generated_at"])
        for view_key in ("identity_view", "personality_view", "development_view",
                          "contradiction_view", "capability_view"):
            self.assertEqual(m[view_key], {})


class TestPhase40R260SM8SM9Snapshot(unittest.TestCase):
    """SM-8 Snapshot 不可变；SM-9 to_dict 返回值拷贝"""

    def test_sm8_snapshot_frozen(self):
        m = create_empty_self_model()
        snap = build_snapshot(m)
        # 尝试修改 snapshot 属性 → 应被拒
        with self.assertRaises(AttributeError):
            snap._version = 999  # type: ignore[misc]
        # 但通过 property 读取是安全的
        self.assertEqual(snap.version, 0)

    def test_sm9_to_dict_returns_copy(self):
        m = create_empty_self_model()
        m["identity_view"] = {"origin": "test"}
        snap = build_snapshot(m)
        d1 = snap.to_dict()
        d1["identity_view"]["origin"] = "modified"
        # snapshot 内部不受影响
        d2 = snap.to_dict()
        self.assertEqual(d2["identity_view"]["origin"], "test")

    def test_sm9_view_property_returns_copy(self):
        m = create_empty_self_model()
        m["personality_view"] = {"stable_traits": {"creativity": 0.7}}
        snap = build_snapshot(m)
        pv = snap.personality_view
        pv["stable_traits"]["creativity"] = 0.0
        # snapshot 内部不受影响
        self.assertEqual(snap.personality_view["stable_traits"]["creativity"], 0.7)

    def test_sm8_snapshot_has_unique_id(self):
        m = create_empty_self_model()
        s1 = build_snapshot(m)
        s2 = build_snapshot(m)
        self.assertNotEqual(s1.snapshot_id, s2.snapshot_id)
        self.assertTrue(s1.snapshot_id.startswith("sm_snap_"))


class TestPhase40R260SM10SM11ForbiddenDependencies(unittest.TestCase):
    """SM-10 禁止依赖 AST；SM-11 禁止调用 AST"""

    @staticmethod
    def _get_clean_src(module_name: str) -> str:
        mod = __import__(module_name, fromlist=["_"])
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        # 清空字符串常量（docstring 里可能提到 forbidden 名称）
        class _Strip(ast.NodeTransformer):
            def visit_Constant(self, n):
                if isinstance(n.value, str):
                    n.value = ""
                return n
        return ast.unparse(_Strip().visit(tree))

    def test_sm10_schema_no_forbidden_imports(self):
        s = self._get_clean_src("src.self_model.self_model_schema")
        for bad in FORBIDDEN_IMPORTS:
            self.assertNotIn(bad, s, f"self_model_schema 不应 import {bad}")

    def test_sm10_snapshot_no_forbidden_imports(self):
        s = self._get_clean_src("src.self_model.self_model_snapshot")
        for bad in FORBIDDEN_IMPORTS:
            self.assertNotIn(bad, s, f"self_model_snapshot 不应 import {bad}")

    def test_sm10_init_no_forbidden_imports(self):
        s = self._get_clean_src("src.self_model")
        for bad in FORBIDDEN_IMPORTS:
            self.assertNotIn(bad, s, f"self_model.__init__ 不应 import {bad}")

    def test_sm11_no_forbidden_calls(self):
        for mod_name in (
            "src.self_model.self_model_schema",
            "src.self_model.self_model_snapshot",
            "src.self_model",
        ):
            s = self._get_clean_src(mod_name)
            for bad_call in FORBIDDEN_CALLS:
                # 检查 call 节点：`bad_call(` 模式
                self.assertNotIn(
                    f"{bad_call}(", s,
                    f"{mod_name} 不应调用 {bad_call}()",
                )


class TestPhase40R260SM12SourceMap(unittest.TestCase):
    """SM-12: SOURCE_MAP 覆盖所有 5 个 view"""

    def test_sm12_source_map_covers_all_views(self):
        view_keys = {
            "identity_view", "personality_view", "development_view",
            "contradiction_view", "capability_view",
        }
        self.assertEqual(set(SOURCE_MAP.keys()), view_keys)

    def test_sm12_source_map_entries_are_non_empty(self):
        for view_key, sources in SOURCE_MAP.items():
            self.assertIsInstance(sources, tuple, f"{view_key} sources 应为 tuple")
            self.assertGreater(len(sources), 0, f"{view_key} 应至少有 1 个来源")

    def test_sm12_source_map_only_allows_readonly_modules(self):
        """SOURCE_MAP 里的来源不应包含禁止依赖"""
        for view_key, sources in SOURCE_MAP.items():
            for src in sources:
                for forbidden in FORBIDDEN_IMPORTS:
                    # 来源路径不应该以 forbidden 开头
                    self.assertFalse(
                        src.startswith(forbidden),
                        f"{view_key} 来源 {src} 包含禁止依赖 {forbidden}",
                    )


class TestPhase40R260ViewTypedDictsExist(unittest.TestCase):
    """验证 5 个 View TypedDict 都存在且可用"""

    def test_identity_view_has_expected_fields(self):
        # TypedDict 在运行时是 dict，验证字段在 __annotations__ 里
        annotations = IdentityView.__annotations__
        for field in ("origin", "core_values", "stable_identity_markers"):
            self.assertIn(field, annotations)

    def test_personality_view_has_expected_fields(self):
        annotations = PersonalityView.__annotations__
        for field in ("stable_traits", "evolving_traits", "recent_changes"):
            self.assertIn(field, annotations)

    def test_development_view_has_expected_fields(self):
        annotations = DevelopmentView.__annotations__
        for field in ("evolution_history", "important_turning_points"):
            self.assertIn(field, annotations)

    def test_contradiction_view_has_expected_fields(self):
        annotations = ContradictionView.__annotations__
        self.assertIn("detected_tensions", annotations)

    def test_capability_view_has_expected_fields(self):
        annotations = CapabilityView.__annotations__
        for field in ("known_strengths", "limitations"):
            self.assertIn(field, annotations)


class TestPhase40R260SnapshotBuildFromValidModel(unittest.TestCase):
    """验证 build_snapshot 从合法 model 构建"""

    def test_build_snapshot_from_empty_model(self):
        m = create_empty_self_model()
        snap = build_snapshot(m)
        self.assertEqual(snap.version, 0)
        self.assertEqual(snap.identity_view, {})

    def test_build_snapshot_from_populated_model(self):
        m = create_empty_self_model()
        m["identity_view"] = {
            "origin": "由清夏铃创造",
            "core_values": ["honesty", "growth", "autonomy"],
            "stable_identity_markers": ["Authenticity", "Growth", "Independence"],
        }
        m["personality_view"] = {
            "stable_traits": {"creativity": 0.7, "curiosity": 0.8},
            "evolving_traits": {"empathy": 0.65},
            "recent_changes": [{"trait": "creativity", "delta": 0.1}],
        }
        m["version"] = 1
        snap = build_snapshot(m)
        self.assertEqual(snap.version, 1)
        self.assertEqual(len(snap.identity_view["core_values"]), 3)
        self.assertEqual(snap.personality_view["stable_traits"]["creativity"], 0.7)

    def test_build_snapshot_rejects_invalid_model(self):
        m = create_empty_self_model()
        del m["capability_view"]
        with self.assertRaises(ValueError):
            build_snapshot(m)


if __name__ == "__main__":
    unittest.main()
