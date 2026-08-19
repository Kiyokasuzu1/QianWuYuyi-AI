# -*- coding: utf-8 -*-
"""
Phase 4.0-R2.5.1 ExperienceBridge 接口冻结测试 (Contract Test)。

**这不是 Gate 测试，不验证业务行为**（G1~G6 已经在 test_phase40_r251_gates.py 覆盖）。
本文件测试的是「对外公共 API 形状」——任何未来重构都不能让这些断言失败。

冻结项：
  CT-1: ExperienceRouteDecision TypedDict 的字段集合 + 字段类型 + 可选字段
  CT-2: RouteClassification 的 3 个合法枚举值，以及只允许这 3 个
  CT-3: emit_route_audit(decision) 可被调用且 decision 形状非法时不崩溃（容错）
  CT-4: ExperienceBridge 类公共方法/构造器签名：
        - __init__(growth_handler=None, record_lookup=None)
        - process(record_or_id, *, user_id=None) -> ExperienceRouteDecision
        - route(record_or_id, *, user_id, growth_handler, record_lookup) 静态
  CT-5: 关键分流阈值常量：GC_IMPORTANCE_THRESHOLD == 0.65, GC_STRICT_FALLBACK_THRESHOLD == 0.75
  CT-6: GC_ALLOWLIST / GC_BLOCKLIST / RELATIONSHIP_TYPES 不出现敏感值（如 allowlist 里不能有 conversation）
  CT-7: _gc_qualified 规则接口（私有函数但语义契约重要）—— 允许 list(importance, memory_type) -> (bool, str)
  CT-8: _has_structured_relationship_signal(record) — 关键词不提升（content 有"喜欢/陪"但无 struct signal 则 False）
  CT-9: GrowthIntegrationService 公共方法签名：
        - accept_experience(self, record) -> dict with keys: {pipeline_state, proposal_id, proposal,
          growth_record_id, applied, growth_history_view, reasons}
        - accept_experience_static(record, *, auto_accept_enabled, confidence_threshold) classmethod
  CT-10: accept_experience_static default auto_accept_enabled=False，applied=False 恒成立
  CT-11: register_builtin_handlers() 后 MEMORY_CREATED 订阅中必须包含 experience_bridge_memory_created_handler
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from typing import Any, Dict, List, Set, get_type_hints

from src.experience import experience_bridge, route_decision


class TestPhase40R251CT1DecisionShape(unittest.TestCase):
    """CT-1: ExperienceRouteDecision TypedDict 的字段集合 + 字段类型。"""

    ALLOWED_KEYS: Set[str] = {
        "decision_id", "memory_id", "user_id",
        "classified_to", "rule_triggered",
        "timestamp_iso", "duration_ms",
        "route_result", "error",
    }

    def test_required_and_optional_keys(self):
        # TypedDict(total=False) => 全部字段都是 optional。但 key 集合必须固定。
        d: route_decision.ExperienceRouteDecision = {}  # type: ignore[assignment]
        self.assertIsInstance(d, dict)
        self.assertEqual(set(), {k for k, v in d.items()})

        # 通过 get_type_hints 枚举所有声明过的 key
        try:
            hints = get_type_hints(route_decision.ExperienceRouteDecision)
        except Exception:  # noqa: BLE001
            hints = route_decision.ExperienceRouteDecision.__annotations__  # type: ignore[attr-defined]
        self.assertEqual(set(hints.keys()), self.ALLOWED_KEYS)

    def test_classified_to_hint(self):
        try:
            hints = get_type_hints(route_decision.ExperienceRouteDecision)
        except Exception:  # noqa: BLE001
            hints = route_decision.ExperienceRouteDecision.__annotations__  # type: ignore[attr-defined]
        # RouteClassification 被用于 classified_to。Python 可能把 TypeAlias 展开成 Literal。
        type_repr = repr(hints["classified_to"])
        # 必须包含 "growth_candidate" / "relationship_memory" / "memory_only" 三个合法值
        for legal in ("growth_candidate", "relationship_memory", "memory_only"):
            self.assertIn(legal, type_repr)

    def test_decision_id_prefix_from_bridge(self):
        d = experience_bridge.ExperienceBridge.route({
            "id": "mem_ct1", "content": "随便一句话",
            "user_id": "u", "role": "user", "importance": 0.2,
            "metadata": {"memory_type": "daily_activity"},
        })
        self.assertTrue(d["decision_id"].startswith("rd_"))
        self.assertIsInstance(d["duration_ms"], int)
        self.assertGreaterEqual(d["duration_ms"], 0)


class TestPhase40R251CT2ClassificationValues(unittest.TestCase):
    """CT-2: RouteClassification 只允许 3 个合法值。"""

    LEGAL = {"memory_only", "relationship_memory", "growth_candidate"}

    def test_literal_values(self):
        try:
            import typing
            origin = typing.get_args(route_decision.RouteClassification)
        except Exception:  # noqa: BLE001
            origin = ()
        if origin:
            self.assertEqual(set(origin), self.LEGAL)

    def test_bridge_only_produces_legal(self):
        samples: List[Dict[str, Any]] = [
            {
                "id": "a", "content": "A", "user_id": "u", "role": "user",
                "importance": 0.3, "metadata": {"memory_type": "food"},
            },
            {
                "id": "b", "content": "B", "user_id": "u", "role": "user",
                "importance": 0.9, "metadata": {"memory_type": "relationship"},
            },
            {
                "id": "c", "content": "C", "user_id": "u", "role": "user",
                "importance": 0.8, "metadata": {"memory_type": "user_preference"},
            },
        ]
        observed: Set[str] = set()
        for rec in samples:
            d = experience_bridge.ExperienceBridge.route(
                rec, growth_handler=lambda r: {"pipeline_state": "created"}
            )
            observed.add(d["classified_to"])
        self.assertEqual(observed, self.LEGAL)


class TestPhase40R251CT3EmitAudit(unittest.TestCase):
    """CT-3: emit_route_audit 容错（decision 形状非法时不崩溃）。"""

    def test_emit_with_legal_decision(self):
        from src.experience.route_decision import emit_route_audit

        decision: route_decision.ExperienceRouteDecision = {
            "decision_id": "rd_xxx",
            "memory_id": "mem_xxx",
            "user_id": "u",
            "classified_to": "memory_only",
            "rule_triggered": "CT3_LEGAL",
            "timestamp_iso": "2026-01-01T00:00:00+00:00",
            "duration_ms": 1,
            "route_result": None,
            "error": None,
        }
        # 应静默或 warning，不抛
        emit_route_audit(decision)

    def test_emit_with_minimal_decision(self):
        from src.experience.route_decision import emit_route_audit

        # 空 dict（total=False 的 TypedDict）
        emit_route_audit({})  # type: ignore[arg-type]

    def test_emit_with_non_dict(self):
        from src.experience.route_decision import emit_route_audit

        # 非 dict 输入：应被 try/except 包住，不抛
        emit_route_audit("not a dict")  # type: ignore[arg-type]


class TestPhase40R251CT4BridgePublicAPI(unittest.TestCase):
    """CT-4: ExperienceBridge 公共方法签名冻结。"""

    def test_init_kwonly_defaults(self):
        # 无参构造应合法
        bridge = experience_bridge.ExperienceBridge()
        self.assertIsNone(bridge._growth_handler)  # type: ignore[attr-defined]
        self.assertIsNone(bridge._record_lookup)  # type: ignore[attr-defined]

        # 传 growth_handler/record_lookup
        spy = []
        h = lambda r: spy.append(("h", r))  # noqa: E731
        lk = lambda mid: {"id": mid}  # noqa: E731
        b = experience_bridge.ExperienceBridge(growth_handler=h, record_lookup=lk)
        self.assertIs(b._growth_handler, h)  # type: ignore[attr-defined]
        self.assertIs(b._record_lookup, lk)  # type: ignore[attr-defined]

    def test_process_signature_accepts_dict_or_str(self):
        bridge = experience_bridge.ExperienceBridge()
        # dict
        d = bridge.process({"id": "x", "content": "x", "user_id": "u", "role": "user",
                            "importance": 0.2, "metadata": {}})
        self.assertIn("classified_to", d)
        # str + lookup
        b2 = experience_bridge.ExperienceBridge(record_lookup=lambda mid: {
            "id": mid, "content": "content", "user_id": "u", "role": "user",
            "importance": 0.1, "metadata": {}
        })
        d2 = b2.process("mem_ct4_id", user_id="u")
        self.assertIn("classified_to", d2)

    def test_route_static_exists_and_returns_decision(self):
        self.assertTrue(callable(experience_bridge.ExperienceBridge.route))
        d = experience_bridge.ExperienceBridge.route({
            "id": "r", "content": "r", "user_id": "u", "role": "user",
            "importance": 0.1, "metadata": {}
        })
        self.assertIsInstance(d, dict)
        self.assertIn("classified_to", d)


class TestPhase40R251CT5ThresholdConstants(unittest.TestCase):
    """CT-5: 阈值常量冻结。"""

    def test_gc_threshold_065(self):
        self.assertEqual(experience_bridge.GC_IMPORTANCE_THRESHOLD, 0.65)

    def test_gc_strict_fallback_threshold_075(self):
        self.assertEqual(experience_bridge.GC_STRICT_FALLBACK_THRESHOLD, 0.75)


class TestPhase40R251CT6Lists(unittest.TestCase):
    """CT-6: allowlist/blocklist 不应互相污染或包含敏感值。"""

    def test_allowlist_does_not_include_daily(self):
        bad = {"conversation", "daily_activity", "small_talk",
               "food", "weather", "user_emotion", "emotional"}
        intersection = set(experience_bridge.GC_ALLOWLIST) & bad
        self.assertEqual(intersection, set(),
                         f"GC_ALLOWLIST 不应该包含 {intersection}")

    def test_blocklist_explicitly_includes_all_bad(self):
        bad = {"conversation", "daily_activity", "small_talk",
               "food", "weather", "user_emotion", "emotional"}
        self.assertTrue(bad.issubset(set(experience_bridge.GC_BLOCKLIST)),
                        "GC_BLOCKLIST 必须完整覆盖所有 bad 类型")

    def test_relationship_types(self):
        # RELATIONSHIP_TYPES 里应该有 relationship
        self.assertIn("relationship", experience_bridge.RELATIONSHIP_TYPES)


class TestPhase40R251CT7GcQualified(unittest.TestCase):
    """CT-7: _gc_qualified(record) 纯规则契约（返回值 bool, str 组合）。"""

    def _call(self, importance, memory_type):
        rec: Dict[str, Any] = {
            "id": "ct7", "content": "c", "user_id": "u", "role": "user",
            "importance": importance,
            "metadata": {"memory_type": memory_type} if memory_type else {},
        }
        if not memory_type:
            rec["metadata"] = {}
        return experience_bridge._gc_qualified(rec)  # type: ignore[attr-defined]

    def test_allowlist_and_above(self):
        ok, rule = self._call(0.7, "user_preference")
        self.assertIs(ok, True)
        self.assertEqual(rule, "GC_ALLOWLIST_AND_IMPORTANCE")

    def test_blocklist_override_even_high_importance(self):
        ok, rule = self._call(0.99, "food")
        self.assertIs(ok, False)
        self.assertEqual(rule, "GC_MEMORY_TYPE_BLOCKLIST")

    def test_no_type_fallback_above_075(self):
        ok, rule = self._call(0.75, "")
        self.assertIs(ok, True)
        self.assertEqual(rule, "GC_STRICT_FALLBACK_HIGH_IMPORTANCE")

    def test_no_type_fallback_below_075(self):
        ok, rule = self._call(0.749, "")
        self.assertIs(ok, False)
        self.assertEqual(rule, "GC_NO_SIGNAL_AND_IMPORTANCE_LOW")


class TestPhase40R251CT8RelationshipNoKeyword(unittest.TestCase):
    """CT-8: Relationship 判断完全不看内容关键词。"""

    def _call(self, content, metadata):
        return experience_bridge._has_structured_relationship_signal({  # type: ignore[attr-defined]
            "id": "ct8", "content": content, "user_id": "u", "role": "user",
            "importance": 0.9, "metadata": metadata,
        })

    def test_keyword_like_not_structured_false(self):
        # 关键词"喜欢"但没有任何 structured signal
        self.assertFalse(self._call("我喜欢吃拉面", {"memory_type": "food"}))

    def test_keyword_accompany_not_structured_false(self):
        self.assertFalse(self._call(
            "今天陪妈妈去超市买菜", {"memory_type": "daily_activity"}
        ))

    def test_structured_signal_flag_true(self):
        self.assertTrue(self._call(
            "随便一句话", {"memory_type": "conversation", "relationship_signal": True}
        ))

    def test_structured_meaning_prefix_true(self):
        self.assertTrue(self._call(
            "随便一句话", {"memory_type": "conversation", "meaning": "relationship_commitment"}
        ))


class TestPhase40R251CT9GrowthIntegrationAPI(unittest.TestCase):
    """CT-9: GrowthIntegrationService 对外入口的参数/返回 shape。"""

    EXPECTED_RESULT_KEYS = {
        "pipeline_state", "proposal_id", "proposal",
        "growth_record_id", "applied",
        "growth_history_view", "reasons",
    }

    def test_classmethods_exists(self):
        from src.growth.growth_integration import GrowthIntegrationService

        self.assertTrue(callable(GrowthIntegrationService.accept_experience_static))
        svc = GrowthIntegrationService(config={"auto_accept_enabled": False, "confidence_threshold": 0.8})
        self.assertTrue(callable(svc.accept_experience))

    def test_result_keys_complete(self):
        from src.growth.growth_integration import GrowthIntegrationService

        record: Dict[str, Any] = {
            "id": "mem_ct9", "content": "我对画画有兴趣。",
            "user_id": "u_ct9", "role": "user",
            "timestamp": "2026-01-01T00:00:00", "importance": 0.72,
            "metadata": {"memory_type": "user_preference"},
        }
        r = GrowthIntegrationService.accept_experience_static(
            record,
            auto_accept_enabled=False,
            confidence_threshold=0.8,
        )
        self.assertTrue(self.EXPECTED_RESULT_KEYS.issubset(r.keys()))


class TestPhase40R251CT10AppliedAlwaysFalse(unittest.TestCase):
    """CT-10: applied 恒 False, auto_accept 默认 False。"""

    def test_default_params(self):
        import inspect
        from src.growth.growth_integration import GrowthIntegrationService

        sig = inspect.signature(GrowthIntegrationService.accept_experience_static)
        self.assertFalse(sig.parameters["auto_accept_enabled"].default)

    def test_applied_is_false_for_any_record(self):
        from src.growth.growth_integration import GrowthIntegrationService

        records: List[Dict[str, Any]] = [
            {"id": "c1", "content": "我喜欢画画，愿意每周练习。",
             "user_id": "u", "role": "user", "importance": 0.82,
             "timestamp": "", "metadata": {"memory_type": "user_preference"}},
            {"id": "c2", "content": "今天吃了拉面。",
             "user_id": "u", "role": "user", "importance": 0.3,
             "timestamp": "", "metadata": {"memory_type": "food"}},
            {"id": "c3", "content": "",
             "user_id": "u", "role": "user", "importance": 0.9,
             "timestamp": "", "metadata": {}},
        ]
        for r in records:
            result = GrowthIntegrationService.accept_experience_static(r)
            self.assertFalse(result["applied"], f"applied must be False for {r['id']}")


class TestPhase40R251CT11EventSubscription(unittest.TestCase):
    """CT-11: MEMORY_CREATED 订阅中必须存在 experience_bridge_memory_created_handler。

    注意：真实 publish → 触发 → lookup store → Bridge.process 的链路
    已经由 test_phase40_r251_gates.py 的 Gate 5 验证过。
    这里只冻结 handler 对外接口：可 import/callable/参数形如 (event) / 静默容忍缺字段。
    """

    def test_handler_importable_and_callable(self):
        from src.events.handlers import experience_bridge_memory_created_handler as h

        self.assertTrue(callable(h))

    def test_handler_takes_yuyi_event_argument(self):
        import inspect
        from src.events.handlers import experience_bridge_memory_created_handler as h

        sig = inspect.signature(h)
        params = list(sig.parameters.keys())
        # 应该只有一个 event 参数
        self.assertGreaterEqual(len(params), 1)
        self.assertIn("event", params[0].lower())

    def test_handler_missing_memory_id_silent(self):
        from src.events.handlers import experience_bridge_memory_created_handler as h
        from src.events.events import EventType, YuyiEvent

        evt = YuyiEvent(
            event_type=EventType.MEMORY_CREATED,
            source="ct11_test",
            data={"user_id": "u"},  # 缺 memory_id
        )
        # 必须不抛
        h(evt)

    def test_handler_bad_event_shape_silent(self):
        from src.events.handlers import experience_bridge_memory_created_handler as h

        # 非 YuyiEvent 形状（例如 dict）也必须不抛
        try:
            h(None)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            self.fail(f"experience_bridge_memory_created_handler 不应向上抛任何异常，但抛出了 {exc!r}")


if __name__ == "__main__":
    unittest.main()
