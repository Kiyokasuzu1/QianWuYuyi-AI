"""
Phase 4.2-D — RelationshipEvent → Growth 证据链验收测试

验收目标（顾问任务卡）：
1. Preference 证据：长期互动（用户多次要求详细技术解释）→ GrowthProposal（pending）
2. Relationship 红线：关系事件不能产生 PersonalityTraitChange / CoreValueChange / IdentityChange
3. Evidence 可追踪：proposal 必须含 evidence_ids + source=relationship
4. 原有 Growth 测试不回归（本文件外，由全量套件保证）

边界：适配器只做证据翻译；评估红线在 GrowthEvaluator（不改），审批在 Approval 流（不改）。
"""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

import pytest


# ============================================================
# Helpers
# ============================================================

def _rel_event(
    rel_type: str,
    content: str,
    *,
    confidence: float = 0.7,
    evidence_id: str = "",
) -> Dict[str, Any]:
    """构造一条统一契约的 RelationshipEvent（TypedDict dict）。"""
    eid = evidence_id or f"mem_42d_{uuid.uuid4().hex[:8]}"
    return {
        "id": f"rel_evt_{uuid.uuid4().hex[:12]}",
        "type": rel_type,
        "content": content,
        "source_memory_id": eid,
        "confidence": confidence,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "observed",
        "meaning": None,
        "memory_type": None,
        "user_id": "user_42d",
        "participants": ["user", "yuyi"],
        "evidence_ids": [eid],
        "potential_dimensions": [],
    }


@pytest.fixture
def tmp_proposal_path(tmp_path):
    return str(tmp_path / "proposals.jsonl")


@pytest.fixture
def integration_service(tmp_proposal_path):
    """tmp 隔离的既有 Approval 流入口（安全配置不弱化）。"""
    from src.growth.growth_integration import GrowthIntegrationService
    from src.growth.proposal_store import ProposalStore
    from src.growth.proposal_manager import ProposalManager
    from src.personality.personality_growth_record import PersonalityGrowthHistory

    growth_history = PersonalityGrowthHistory()
    store = ProposalStore(path=tmp_proposal_path)
    manager = ProposalManager(
        store=store,
        growth_history=growth_history,
        config={"auto_accept_enabled": False, "confidence_threshold": 0.8},
    )
    return GrowthIntegrationService(
        proposal_manager=manager,
        growth_history=growth_history,
        config={"auto_accept_enabled": False, "confidence_threshold": 0.8},
    )


@pytest.fixture
def adapter(integration_service):
    from src.growth.relationship_evidence_adapter import RelationshipEvidenceAdapter

    return RelationshipEvidenceAdapter(integration_service=integration_service)


# ============================================================
# 1. 转换规则（类型如实标注）
# ============================================================

class TestConvertMapping:
    def test_preference_learning_maps_to_preference(self, adapter):
        ev = _rel_event("preference_learning", "我了解你通常喜欢详细的技术解释")
        raw = adapter.convert(ev)
        assert raw is not None
        assert raw["event_type"] == "preference"
        assert raw["metadata"]["source"] == "relationship"
        assert raw["metadata"]["relationship_event_type"] == "preference_learning"
        assert raw["metadata"]["relationship_event_id"] == ev["id"]
        # 证据锚定
        assert raw["source_ids"] == ev["evidence_ids"]
        assert raw["evidence"][0]["memory_id"] == ev["evidence_ids"][0]

    @pytest.mark.parametrize("rel_type", [
        "collaboration", "trust_building", "boundary_respect",
        "promise", "declaration", "milestone", "support", "boundary", "other",
    ])
    def test_other_types_map_to_relationship(self, adapter, rel_type):
        raw = adapter.convert(_rel_event(rel_type, "我们一起长期合作这个项目"))
        assert raw is not None
        assert raw["event_type"] == "relationship"

    def test_invalid_input_returns_none(self, adapter):
        assert adapter.convert(None) is None
        assert adapter.convert({}) is None
        assert adapter.convert({"type": ""}) is None
        assert adapter.convert("not_a_dict") is None


# ============================================================
# 2. 验收 #1：Preference 证据 → GrowthProposal（pending）
# ============================================================

class TestPreferenceEvidenceProposal:
    def test_longterm_preference_produces_pending_proposal(
        self, adapter, integration_service, tmp_proposal_path,
    ):
        """长期互动：用户多次获得详细技术解释 → preference 证据 → pending proposal。"""
        ev = _rel_event(
            "preference_learning",
            "你已经多次给我详细的技术解释，帮我养成了深入研究架构的习惯",
            confidence=0.85,
        )
        result = adapter.process_relationship_event(ev)

        assert result["pipeline_state"] == "created", (
            f"preference 证据应创建 proposal，实际={result['pipeline_state']} "
            f"reasons={result['reasons']}"
        )
        assert result["proposal_id"]

        # evaluator 判定摘要：走了 preference 域
        evaluated = result["evaluated"]
        assert evaluated["growth_domain"] == "preference"
        assert evaluated["growth_allowed"] is True

        proposal = integration_service.get_proposal(result["proposal_id"])
        assert proposal is not None
        assert proposal.status == "pending"  # auto_accept=False → 必须停在 pending

        # 持久化验证
        assert os.path.exists(tmp_proposal_path)


# ============================================================
# 3. 验收 #2：Relationship 红线（永不进 preference/trait，delta=0）
# ============================================================

class TestRelationshipRedLines:
    @pytest.mark.parametrize("rel_type", [
        "collaboration", "trust_building", "milestone", "promise", "declaration",
    ])
    def test_relationship_event_never_produces_personality_change(
        self, adapter, integration_service, rel_type,
    ):
        ev = _rel_event(
            rel_type,
            "我们已经一起长期合作了很久，我非常信任你，你一直陪着我",
            confidence=0.9,
        )
        result = adapter.process_relationship_event(ev)

        # 事件必须如实标注为 relationship（不是 preference）
        evaluated = result["evaluated"]
        if evaluated is not None:
            assert evaluated["event_type"] == "relationship"
            assert evaluated["growth_domain"] == "relationship_context"
            assert evaluated["growth_level"] in ("trace", "context"), (
                f"关系事件 growth_level 不得进入 preference/trait，"
                f"实际={evaluated['growth_level']}"
            )
            assert evaluated["applied_delta"] == 0.0, (
                f"关系事件 applied_delta 必须恒为 0，实际={evaluated['applied_delta']}"
            )

        # 即便产生了 proposal，也绝不能含人格维度变更
        if result["proposal_id"]:
            proposal = integration_service.get_proposal(result["proposal_id"])
            assert proposal is not None
            for change in proposal.proposed_changes:
                path = change.path if hasattr(change, "path") else change.get("path", "")
                assert not path.startswith("personality."), (
                    f"关系事件 proposal 不得包含人格变更，实际 path={path}"
                )

    def test_relationship_event_type_never_relabeled_preference(self, adapter):
        """转换器映射规则写死：非 preference_learning 永不标注 preference。"""
        for rel_type in ("trust_building", "collaboration", "milestone"):
            raw = adapter.convert(_rel_event(rel_type, "内容"))
            assert raw["event_type"] == "relationship"


# ============================================================
# 4. 验收 #3：Evidence 可追踪
# ============================================================

class TestEvidenceTraceability:
    def test_proposal_carries_evidence_ids_and_source(
        self, adapter, integration_service,
    ):
        ev = _rel_event(
            "preference_learning",
            "你已经多次给我详细的技术解释，帮我养成了深入研究的习惯",
            confidence=0.85,
        )
        result = adapter.process_relationship_event(ev)
        assert result["pipeline_state"] == "created", f"reasons={result['reasons']}"

        proposal = integration_service.get_proposal(result["proposal_id"])
        assert proposal is not None

        # evidence_ids 锚定原 RelationshipEvent 证据
        assert ev["evidence_ids"][0] in proposal.evidence_ids

        # evaluator_meta 来源标注
        meta = proposal.evaluator_meta
        assert meta.get("source") == "relationship"
        assert meta.get("relationship_event_id") == ev["id"]
        assert meta.get("relationship_event_type") == "preference_learning"

        # 触发事件溯源
        assert proposal.source_event_id == ev["id"]


# ============================================================
# 5. Runtime 生产接线（fail-soft 转发）
# ============================================================

class TestRuntimeWiring:
    def test_record_interaction_forwards_event_to_growth(
        self, adapter, integration_service, tmp_proposal_path,
    ):
        from src.runtime.runtime_core import RuntimeCore

        with tempfile.TemporaryDirectory() as tmp:
            rc = RuntimeCore(config={
                "adapters_enabled": True,
                "relationship_enabled": True,
                "event_driven_enabled": True,
                "memory_store_path": os.path.join(tmp, "mem.json"),
                "growth_proposals_path": os.path.join(tmp, "gp.json"),
                "state_file": os.path.join(tmp, "runtime.json"),
            })
            # 注入 tmp 隔离的适配器（替代懒创建的默认实例，避免污染真实 data/）
            rc._relationship_evidence_adapter = adapter

            result = rc.record_relationship_interaction(
                user_message="我了解你通常喜欢详细的技术解释，你已经帮我养成了研究的习惯",
                evidence_id="mem_42d_rt_1",
                emotion_tag="joy",
            )
            assert result is not None
            assert result.get("event") is not None, "本条消息应提取出 preference_learning 事件"
            assert result["event"]["type"] == "preference_learning"

            # 领域事件必须带 growth_evidence_state（证据链已被触发）
            # 注意：notify_relationship_changed 的 payload 在领域事件里嵌套一层
            # 注意：RuntimeEventBus 包装的是 EventBus 全局单例（src/core/event_bus.py），
            # 历史为跨实例共享且按时间倒序；不能用 history[-1] 取"本轮"事件，
            # 必须以本轮唯一 evidence_id 锚定属于本测试的事件。
            history = rc.get_domain_event_history(event_type="relationship_changed", limit=50)
            assert history, "应有 relationship_changed 领域事件"
            payload = None
            for e in history:
                outer = e.get("payload", {})
                inner = outer.get("payload", outer)
                interaction = inner.get("interaction") or {}
                if "mem_42d_rt_1" in (interaction.get("evidence_ids") or []):
                    payload = inner
                    break
            assert payload is not None, (
                "全局事件历史中未找到本测试 interaction（evidence_id=mem_42d_rt_1）"
            )
            state = payload.get("growth_evidence_state")
            assert state in {
                "created", "deduped", "no_growth",
                "rejected_low_confidence", "rejected_no_evidence",
            }, f"growth_evidence_state 缺失或非法: {state}"

    def test_forward_fail_soft(self):
        from src.runtime.runtime_core import RuntimeCore

        with tempfile.TemporaryDirectory() as tmp:
            rc = RuntimeCore(config={
                "relationship_enabled": True,
                "memory_store_path": os.path.join(tmp, "mem.json"),
                "state_file": os.path.join(tmp, "runtime.json"),
            })
            # None / 非法输入 → None，不抛异常
            assert rc._forward_relationship_event_to_growth(None) is None
            assert rc._forward_relationship_event_to_growth({}) is None

    def test_adapter_error_isolated(self, integration_service):
        """适配器内部异常必须 fail-soft 为 error 状态，不抛出。"""
        from src.growth.relationship_evidence_adapter import RelationshipEvidenceAdapter

        bad = RelationshipEvidenceAdapter(integration_service=integration_service)
        result = bad.process_relationship_event({"type": None})
        assert result["pipeline_state"] in ("rejected_invalid_event", "error")
