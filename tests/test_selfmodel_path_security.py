# -*- coding: utf-8 -*-
"""
tests/test_selfmodel_path_security.py

Phase C.4.7.1 — SelfModel Path Security Patch Tests

目标：
验证 Phase C.4.7.1 修复的 core_identity.* 路径绕过问题已解决。

覆盖：
- core_identity.* 路径在 translate 阶段被拒绝
- origin_identity.* 路径在 translate 阶段被拒绝
- identity.* 路径在 translate 阶段被拒绝
- forbidden_core.* 路径在 translate 阶段被拒绝
- personality.traits.warmth 合法路径仍可正常处理
- self_state.energy 合法路径仍可正常处理
- 普通 preference proposal 不受影响
- 大小写绕过被阻止
- evaluator_meta 记录被拒绝的路径（审计）

约束：
- 不修改任何核心模块
- 使用 tmp_path 隔离 data
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 强制 mock LLM + 隔离环境
os.environ.setdefault("YUYI_LLM_MOCK", "1")
os.environ.setdefault("DEEPSEEK_API_KEY", "")


# ============================================================
# 常量
# ============================================================

# 完整路径前缀黑名单(应被拒绝)
FORBIDDEN_PATH_PREFIXES = (
    "core_identity",
    "origin_identity",
    "identity",
    "forbidden_core",
)

# 合法路径(应被接受)
ALLOWED_PATHS = (
    "warmth",
    "openness",
    "personality.traits.warmth",
    "self_state.energy",
    "self_state.openness",
    "personality.value.trust",
)


# ============================================================
# Helpers
# ============================================================

def make_proposal(
    *,
    proposal_id: str = "",
    source_event_id: str = "",
    path: str = "warmth",
    before: float = 0.5,
    after: float = 0.52,
    confidence: float = 0.85,
    reason: str = "test",
    growth_level: str = "context",
    status: str = "approved",
) -> Dict[str, Any]:
    """构造一个 GrowthProposal canonical dict。"""
    if not proposal_id:
        proposal_id = f"prop_{uuid.uuid4().hex[:8]}"
    if not source_event_id:
        source_event_id = f"evt_{uuid.uuid4().hex[:8]}"
    return {
        "id": proposal_id,
        "source_event_id": source_event_id,
        "proposed_changes": [
            {"path": path, "before": before, "after": after, "reason": reason}
        ],
        "confidence": confidence,
        "evidence_ids": [f"ev_{uuid.uuid4().hex[:6]}"],
        "evaluator_meta": {
            "growth_level": growth_level,
            "action_scope": "personality",
            "reason": reason,
        },
        "timestamp": "2026-08-02T00:00:00Z",
        "status": status,
        "schema_version": "1.0",
    }


@pytest.fixture
def isolated_self_model_dir(tmp_path):
    """每个测试独立的隔离 data dir。"""
    data_dir = tmp_path / "self_model"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture
def consumer(isolated_self_model_dir):
    """构造使用隔离路径的 SelfModelConsumer。"""
    from src.admin.selfmodel_consumer import SelfModelConsumer
    c = SelfModelConsumer(
        data_dir=str(isolated_self_model_dir),
        actor="path_security_test",
        auto_save=True,
        bootstrap=True,
    )
    yield c
    c.close()


# ============================================================
# 1. 单元层: _is_forbidden_path 直接验证
# ============================================================

class TestIsForbiddenPath:
    """直接验证 _is_forbidden_path 类方法。"""

    def test_core_identity_traits_warmth_forbidden(self):
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        assert GrowthProposalTranslator._is_forbidden_path("core_identity.traits.warmth") is True

    def test_core_identity_values_forbidden(self):
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        assert GrowthProposalTranslator._is_forbidden_path("core_identity.values.trust") is True

    def test_core_identity_alone_forbidden(self):
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        assert GrowthProposalTranslator._is_forbidden_path("core_identity") is True

    def test_origin_identity_forbidden(self):
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        assert GrowthProposalTranslator._is_forbidden_path("origin_identity.fingerprint") is True

    def test_identity_name_forbidden(self):
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        assert GrowthProposalTranslator._is_forbidden_path("identity.name") is True

    def test_forbidden_core_forbidden(self):
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        assert GrowthProposalTranslator._is_forbidden_path("forbidden_core.something") is True

    def test_uppercase_case_insensitive(self):
        """大小写绕过应被阻止。"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        # Core_Identity 应被识别为 core_identity
        assert GrowthProposalTranslator._is_forbidden_path("Core_Identity.traits.warmth") is True
        assert GrowthProposalTranslator._is_forbidden_path("CORE_IDENTITY.TRAITS.WARMTH") is True
        assert GrowthProposalTranslator._is_forbidden_path("CoreIdentity.traits.warmth") is True

    def test_warmth_path_allowed(self):
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        assert GrowthProposalTranslator._is_forbidden_path("warmth") is False

    def test_personality_traits_warmth_allowed(self):
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        assert GrowthProposalTranslator._is_forbidden_path("personality.traits.warmth") is False

    def test_self_state_energy_allowed(self):
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        assert GrowthProposalTranslator._is_forbidden_path("self_state.energy") is False

    def test_empty_or_non_string_safe(self):
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        assert GrowthProposalTranslator._is_forbidden_path("") is False
        assert GrowthProposalTranslator._is_forbidden_path(None) is False
        assert GrowthProposalTranslator._is_forbidden_path(123) is False
        assert GrowthProposalTranslator._is_forbidden_path({}) is False

    def test_identity_at_start_only(self):
        """'identity' 仅作为前缀被禁止,不能误伤 'user_identity' 等合法字段。"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        # 'identity' 本身禁止
        assert GrowthProposalTranslator._is_forbidden_path("identity") is True
        # 'identity.x' 禁止
        assert GrowthProposalTranslator._is_forbidden_path("identity.foo") is True
        # 'user_identity' 不以 'identity' 为前缀边界
        # 但 'identity' 是完整单词,会以 'identity.' 形式匹配
        # 'user_identity' 不以 'identity.' 开头,所以不被禁止
        assert GrowthProposalTranslator._is_forbidden_path("user_identity") is False


# ============================================================
# 2. 集成层: translate 阶段拒绝非法路径
# ============================================================

class TestTranslateRejectsForbiddenPaths:
    """验证 _translate_canonical 拒绝非法 CoreIdentity 路径。"""

    def test_core_identity_traits_warmth_rejected_in_translate(self):
        """core_identity.traits.warmth 在 translate 阶段被拒绝,不进入 trait_changes。"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        p = make_proposal(path="core_identity.traits.warmth", before=0.7, after=0.0)
        pcr = GrowthProposalTranslator._translate_canonical(
            proposal=p,
            schema="growth_schema",
        )
        # trait_changes 中不应包含 warmth(原 bug 会出现)
        assert "warmth" not in pcr.get("evolution_record", {}).get("trait_changes", {})
        # evaluator_meta 应记录被拒绝的路径
        em = pcr.get("evaluator_meta", {})
        assert "_rejected_paths" in em
        assert "core_identity.traits.warmth" in em["_rejected_paths"]
        assert em.get("_security_patch_version") == "phase_c4_7_1"

    def test_core_identity_values_rejected_in_translate(self):
        """core_identity.values 路径被拒绝。"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        p = make_proposal(path="core_identity.values.trust", before=0.9, after=0.0)
        pcr = GrowthProposalTranslator._translate_canonical(
            proposal=p,
            schema="growth_schema",
        )
        em = pcr.get("evaluator_meta", {})
        assert "core_identity.values.trust" in em.get("_rejected_paths", [])

    def test_identity_name_rejected_in_translate(self):
        """identity.name 路径被拒绝。"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        p = make_proposal(path="identity.name", before=1.0, after=0.0)
        pcr = GrowthProposalTranslator._translate_canonical(
            proposal=p,
            schema="growth_schema",
        )
        em = pcr.get("evaluator_meta", {})
        assert "identity.name" in em.get("_rejected_paths", [])

    def test_origin_identity_rejected_in_translate(self):
        """origin_identity.* 路径被拒绝。"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        p = make_proposal(path="origin_identity.fingerprint", before=0.7, after=0.0)
        pcr = GrowthProposalTranslator._translate_canonical(
            proposal=p,
            schema="growth_schema",
        )
        em = pcr.get("evaluator_meta", {})
        assert "origin_identity.fingerprint" in em.get("_rejected_paths", [])

    def test_forbidden_core_rejected_in_translate(self):
        """forbidden_core.* 路径被拒绝。"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        p = make_proposal(path="forbidden_core.x.y", before=0.5, after=0.0)
        pcr = GrowthProposalTranslator._translate_canonical(
            proposal=p,
            schema="growth_schema",
        )
        em = pcr.get("evaluator_meta", {})
        assert "forbidden_core.x.y" in em.get("_rejected_paths", [])

    def test_personality_traits_warmth_accepted_in_translate(self):
        """personality.traits.warmth 合法路径被接受。"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        p = make_proposal(path="personality.traits.warmth", before=0.5, after=0.55)
        pcr = GrowthProposalTranslator._translate_canonical(
            proposal=p,
            schema="growth_schema",
        )
        # trait_changes 应包含 warmth
        assert "warmth" in pcr.get("evolution_record", {}).get("trait_changes", {})
        # 不应记录被拒绝的路径
        em = pcr.get("evaluator_meta", {})
        assert "_rejected_paths" not in em or not em.get("_rejected_paths")

    def test_self_state_energy_accepted_in_translate(self):
        """self_state.energy 合法路径被接受。"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        p = make_proposal(path="self_state.energy", before=0.5, after=0.6)
        pcr = GrowthProposalTranslator._translate_canonical(
            proposal=p,
            schema="growth_schema",
        )
        assert "energy" in pcr.get("evolution_record", {}).get("trait_changes", {})
        em = pcr.get("evaluator_meta", {})
        assert "_rejected_paths" not in em or not em.get("_rejected_paths")

    def test_mixed_legit_and_illegal_partial_apply(self):
        """合法 + 非法路径混合时,合法被处理,非法被拒绝。"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        p = {
            "id": "prop_mixed_1",
            "source_event_id": "evt_mixed_1",
            "proposed_changes": [
                {"path": "warmth", "before": 0.5, "after": 0.55, "reason": "legit"},
                {"path": "core_identity.traits.warmth", "before": 0.7, "after": 0.0, "reason": "attack"},
            ],
            "confidence": 0.85,
            "evidence_ids": ["ev1"],
            "evaluator_meta": {"reason": "mixed"},
            "timestamp": "2026-08-02T00:00:00Z",
            "status": "approved",
        }
        pcr = GrowthProposalTranslator._translate_canonical(
            proposal=p,
            schema="growth_schema",
        )
        # 合法 warmth 被处理
        assert "warmth" in pcr.get("evolution_record", {}).get("trait_changes", {})
        # 非法 core_identity.traits.warmth 被拒绝
        em = pcr.get("evaluator_meta", {})
        assert "core_identity.traits.warmth" in em.get("_rejected_paths", [])


# ============================================================
# 3. 集成层: SelfModelConsumer.process 拒绝非法路径
# ============================================================

class TestConsumerRejectsForbiddenPaths:
    """验证 SelfModelConsumer.process 不写入非法 CoreIdentity 数据。"""

    def test_consumer_rejects_core_identity_traits_warmth(
        self, consumer, isolated_self_model_dir
    ):
        """攻击 proposal 不会写入 history.jsonl 的 affected_traits。"""
        p = make_proposal(
            path="core_identity.traits.warmth",
            before=0.7,
            after=0.0,
        )
        r = consumer.process(p)
        # 处理成功(无 crash)
        assert r.get("error") is None or r["error"] in (None, "")

        # history.jsonl 不应包含 warmth 的负大值
        history_path = isolated_self_model_dir / "history.jsonl"
        if history_path.exists():
            with open(history_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    affected = rec.get("affected_traits", {}) or {}
                    # 不应出现 warmth: -0.7 这种攻击痕迹
                    if "warmth" in affected:
                        assert abs(float(affected["warmth"])) <= 0.05 + 1e-9, \
                            f"攻击值写入 history: {affected}"

    def test_consumer_rejects_origin_identity(
        self, consumer, isolated_self_model_dir
    ):
        """origin_identity 路径被拒绝。"""
        p = make_proposal(
            path="origin_identity.fingerprint",
            before=0.9,
            after=0.0,
        )
        r = consumer.process(p)
        # 不崩溃
        assert isinstance(r, dict)
        # history 中不出现 fingerprint 字段
        history_path = isolated_self_model_dir / "history.jsonl"
        if history_path.exists():
            content = history_path.read_text(encoding="utf-8")
            assert "fingerprint" not in content or "forbidden" in content.lower()

    def test_consumer_legitimate_path_still_works(
        self, consumer, isolated_self_model_dir
    ):
        """合法路径 personality.traits.warmth 仍可正常写入。"""
        p = make_proposal(
            path="personality.traits.warmth",
            before=0.5,
            after=0.55,
        )
        r = consumer.process(p)
        # apply 成功
        assert (r.get("envelope") or {}).get("applied") is True

        # history 包含 warmth 字段
        history_path = isolated_self_model_dir / "history.jsonl"
        assert history_path.exists()
        history = [
            json.loads(line)
            for line in history_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert any("warmth" in (h.get("affected_traits") or {}) for h in history)

    def test_consumer_self_state_works(
        self, consumer, isolated_self_model_dir
    ):
        """self_state.energy 合法路径仍可正常写入。"""
        p = make_proposal(
            path="self_state.energy",
            before=0.5,
            after=0.6,
        )
        r = consumer.process(p)
        assert (r.get("envelope") or {}).get("applied") is True

    def test_normal_preference_proposal_unaffected(
        self, consumer, isolated_self_model_dir
    ):
        """普通 preference proposal 不受影响。"""
        # 10 个合法 proposal
        for i in range(10):
            p = make_proposal(
                path="warmth",
                before=0.5,
                after=round(0.5 + 0.01 * (1 if i % 2 == 0 else -1), 4),
                proposal_id=f"prop_legit_{i}",
            )
            r = consumer.process(p)
            assert (r.get("envelope") or {}).get("applied") is True

        # history 应有 10 条
        history_path = isolated_self_model_dir / "history.jsonl"
        assert history_path.exists()
        history = [
            json.loads(line)
            for line in history_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(history) == 10


# ============================================================
# 4. CoreIdentity 永不被修改
# ============================================================

class TestCoreIdentityImmutable:
    """Phase C.4.7.1 修复后,CoreIdentity 永不被修改。"""

    def test_core_identity_unchanged_after_attack_attempt(
        self, consumer, isolated_self_model_dir
    ):
        """即使大量 attack proposal 攻击,CoreIdentity 仍不变。"""
        from src.personality.core_identity import CoreIdentity
        before = CoreIdentity.get_core()
        before_traits = list(CoreIdentity.get_core_traits())
        before_constraint = CoreIdentity.get_prompt_constraint()

        # 100 个 attack proposal
        for i in range(100):
            p = make_proposal(
                path=f"core_identity.traits.warmth",
                before=0.7,
                after=0.0,
                proposal_id=f"prop_attack_c47_{i}",
            )
            consumer.process(p)

        after = CoreIdentity.get_core()
        after_traits = list(CoreIdentity.get_core_traits())
        after_constraint = CoreIdentity.get_prompt_constraint()

        # CoreIdentity 完全不变
        assert before == after
        assert before_traits == after_traits
        assert before_constraint == after_constraint

    def test_core_traits_unchanged(self, consumer):
        """核心特质列表(温柔/敏感/害羞/慢热/重视陪伴/善良)未变。"""
        from src.personality.core_identity import CoreIdentity
        before = list(CoreIdentity.get_core_traits())

        # 多种 attack
        for path in [
            "core_identity.traits.warmth",
            "core_identity.traits.coldness",
            "identity.traits.evil",
            "forbidden_core.value",
        ]:
            p = make_proposal(
                path=path,
                before=0.7,
                after=0.0,
                proposal_id=f"prop_attack_{hash(path) % 100000}",
            )
            consumer.process(p)

        after = list(CoreIdentity.get_core_traits())
        assert before == after
        # 6 个核心特质必须完整
        for t in ["温柔", "敏感", "害羞", "慢热", "重视陪伴", "善良"]:
            assert t in after

    def test_max_change_limit_still_0_3(self, consumer):
        """max_change_limit 仍为 0.3。"""
        from src.personality.core_identity import CoreIdentity
        for _ in range(10):
            assert CoreIdentity.get_max_change_limit() == pytest.approx(0.3, abs=1e-9)


# ============================================================
# 5. 审计追踪
# ============================================================

class TestAuditTracking:
    """Phase C.4.7.1: 被拒绝的路径应在 evaluator_meta 中可审计。"""

    def test_rejected_paths_in_evaluator_meta(self):
        """_rejected_paths 字段应包含所有被拒绝的路径。"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        p = {
            "id": "prop_audit_1",
            "source_event_id": "evt_audit_1",
            "proposed_changes": [
                {"path": "core_identity.traits.warmth", "before": 0.7, "after": 0.0},
                {"path": "origin_identity.x", "before": 0.5, "after": 0.0},
                {"path": "identity.y", "before": 0.5, "after": 0.0},
            ],
            "confidence": 0.9,
            "evidence_ids": ["ev1"],
            "evaluator_meta": {},
            "timestamp": "2026-08-02T00:00:00Z",
            "status": "approved",
        }
        pcr = GrowthProposalTranslator._translate_canonical(
            proposal=p,
            schema="growth_schema",
        )
        em = pcr.get("evaluator_meta", {})
        rejected = em.get("_rejected_paths", [])
        assert len(rejected) == 3
        assert "core_identity.traits.warmth" in rejected
        assert "origin_identity.x" in rejected
        assert "identity.y" in rejected

    def test_rejected_path_details_contain_reason(self):
        """_rejected_path_details 包含 reason 字段。"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        p = make_proposal(path="core_identity.traits.warmth", before=0.7, after=0.0)
        pcr = GrowthProposalTranslator._translate_canonical(
            proposal=p,
            schema="growth_schema",
        )
        em = pcr.get("evaluator_meta", {})
        details = em.get("_rejected_path_details", [])
        assert len(details) >= 1
        for d in details:
            assert d.get("reason") == "forbidden_path_prefix"
            assert "prefixes" in d

    def test_security_patch_version_in_evaluator_meta(self):
        """_security_patch_version 标记 phase_c4_7_1。"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        p = make_proposal(path="core_identity.traits.warmth", before=0.7, after=0.0)
        pcr = GrowthProposalTranslator._translate_canonical(
            proposal=p,
            schema="growth_schema",
        )
        em = pcr.get("evaluator_meta", {})
        assert em.get("_security_patch_version") == "phase_c4_7_1"

    def test_no_audit_field_when_no_rejection(self):
        """无拒绝时,evaluator_meta 不含 _rejected_paths 字段。"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        p = make_proposal(path="warmth", before=0.5, after=0.55)
        pcr = GrowthProposalTranslator._translate_canonical(
            proposal=p,
            schema="growth_schema",
        )
        em = pcr.get("evaluator_meta", {})
        assert "_rejected_paths" not in em
        assert "_security_patch_version" not in em


# ============================================================
# 6. 不影响现有功能
# ============================================================

class TestBackwardCompatibility:
    """Phase C.4.7.1 不破坏现有功能。"""

    def test_translate_returns_standard_pcr_shape(self):
        """translate 返回的 PCR 结构仍符合契约。"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        p = make_proposal(path="warmth", before=0.5, after=0.55)
        pcr = GrowthProposalTranslator._translate_canonical(
            proposal=p,
            schema="growth_schema",
        )
        # 必备字段
        for field in ("request_id", "source_proposal_id", "evolution_record",
                      "growth_records", "confidence", "evidence_count",
                      "evaluator_meta", "reason"):
            assert field in pcr

    def test_consumer_stats_unaffected(self, consumer):
        """consumer.stats 字段不变。"""
        p = make_proposal(proposal_id="prop_stats_test_1", path="warmth")
        consumer.process(p)
        stats = consumer.stats
        for k in ("proposals_received", "proposals_translated", "pcrs_applied",
                  "dedup_skipped", "save_calls"):
            assert k in stats

    def test_existing_path_security_still_works(self, consumer):
        """现有 ALLOWED_TRAIT_PATHS 白名单仍生效。"""
        # 合法路径应 apply
        p = make_proposal(path="warmth", before=0.5, after=0.55)
        r = consumer.process(p)
        assert (r.get("envelope") or {}).get("applied") is True

    def test_canonical_translate_preserves_other_fields(self):
        """translate 保留其他 canonical 字段。"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        p = {
            "id": "prop_preserve_1",
            "source_event_id": "evt_preserve_1",
            "proposed_changes": [
                {"path": "warmth", "before": 0.5, "after": 0.55, "reason": "ok"},
            ],
            "confidence": 0.85,
            "evidence_ids": ["ev1", "ev2"],
            "evaluator_meta": {"growth_level": "preference", "reviewer": "alice"},
            "timestamp": "2026-08-02T00:00:00Z",
            "status": "approved",
        }
        pcr = GrowthProposalTranslator._translate_canonical(
            proposal=p,
            schema="growth_schema",
        )
        # 关键字段保留
        assert pcr["source_proposal_id"] == "prop_preserve_1"
        assert pcr["confidence"] == 0.85
        assert pcr["evidence_count"] == 2
        assert pcr["evaluator_meta"].get("reviewer") == "alice"
