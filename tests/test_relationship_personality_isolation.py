# -*- coding: utf-8 -*-
"""Phase 2.5-D 架构护栏契约测试:关系核心永不污染人格。

核心原则(AGENTS.md / Phase 2.5-D):
    人格 = 我是谁;关系 = 我和谁是什么关系;记忆 = 我经历过什么。
    三者永远分离。

本测试跑完整关系闭环(bridge → 提案 → 人工 approve → 人工 activate →
关系核心落库 → 上下文注入),全程使用 tmp 存储,并断言:
- PersonalityResolver 解析出的人格向量逐字节不变;
- YUI_CORE_FACTS(身份核心)逐字不变;
- GrowthState(成长状态)逐字节不变;
- 闭环写入的文件只有关系三件套(proposals / cores / records),
  绝无 personality / self_model / emotion 文件。

这是羽依最大的架构风险护栏:关系不是人格。
"""
import json
from types import SimpleNamespace

from src.events.bus import EventBus
from src.events.events import MemoryCreatedEvent
from src.growth.growth_state import GrowthState
from src.identity import yui_core_profile as ycp
from src.personality.personality_resolver import PersonalityResolver
from src.relationship.relationship_activation import (
    ActivationDraft,
    RelationshipActivationRecordStore,
    RelationshipActivationService,
)
from src.relationship.relationship_candidate_bridge import RelationshipCandidateBridge
from src.relationship.relationship_core import DEFAULT_VISIBILITY
from src.relationship.relationship_core_store import RelationshipCoreStore
from src.relationship.relationship_proposal import RelationshipProposal
from src.relationship.relationship_proposal_store import RelationshipProposalStore
from src.personality.relationship_state import RelationshipState as PersonalityRelationshipState
from src.runtime.adapters.impl.relationship_core_adapter import (
    RelationshipCoreAdapter,
    build_relationship_context_block,
)

UID = "366648462"


def _json_dump(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def test_relationship_core_never_pollutes_personality(tmp_path):
    # ---- 快照:人格 / 身份核心 / 成长状态 ----
    growth_state = GrowthState(state_path=str(tmp_path / "growth_state.json"))
    resolver = PersonalityResolver(
        state=growth_state,
        relationship_state=PersonalityRelationshipState(
            state_path=str(tmp_path / "relationship_state.json"),
        ),
    )
    traits_before = _json_dump(resolver.resolve().get_all())
    identity_before = list(ycp.YUI_CORE_FACTS)
    growth_before = _json_dump(growth_state.get())

    # ---- 完整关系闭环(全部 tmp 存储)----
    prop_store = RelationshipProposalStore(str(tmp_path / "proposals.jsonl"))
    core_store = RelationshipCoreStore(str(tmp_path / "cores.jsonl"))
    record_store = RelationshipActivationRecordStore(str(tmp_path / "records.jsonl"))

    bridge = RelationshipCandidateBridge(proposal_store=prop_store)
    bus = EventBus()
    bridge.subscribe(bus)
    bus.publish(MemoryCreatedEvent(
        memory_id="mem_1",
        user_id=UID,
        content="羽依,你和我之间的约定:永远不要忘记我们的称呼规则",
        source="isolation_test",
    ))

    proposal = RelationshipProposal.from_dict(prop_store.list()[0])
    assert proposal.approve("admin", "证据充分,同意")
    assert prop_store.save(proposal)

    service = RelationshipActivationService(
        proposal_store=prop_store, core_store=core_store, record_store=record_store,
    )
    result = service.activate(
        ActivationDraft(
            proposal_id=proposal.proposal_id,
            approved_memory_ids=["mem_1"],
            relationship_type="creator",
            agreements=["只对清清保持某些称呼"],
            boundaries=["不向其他人复述清清的私事"],
            visibility=DEFAULT_VISIBILITY,
        ),
        reviewer="admin",
        reason="审核通过,进入关系核心",
    )
    assert result["ok"] is True

    # ---- 激活后注入(模拟 runtime 读路径)----
    block = build_relationship_context_block(core_store.load(), current_user_id=UID)
    assert "只对清清保持某些称呼" in block
    ctx = SimpleNamespace(user_id=UID)
    RelationshipCoreAdapter(store=core_store, user_id=UID).process_cycle(ctx)
    assert ctx.relationship_core_context

    # ---- 断言:人格 / 身份 / 成长逐字节未变 ----
    assert _json_dump(resolver.resolve().get_all()) == traits_before
    assert list(ycp.YUI_CORE_FACTS) == identity_before
    assert _json_dump(growth_state.get()) == growth_before

    # ---- 闭环写入的文件只有关系三件套(+ 显式构造的成长/关系状态文件)----
    written = {p.name for p in tmp_path.iterdir()}
    allowed = {
        "proposals.jsonl", "cores.jsonl", "records.jsonl",
        "growth_state.json", "relationship_state.json",
    }
    assert written <= allowed, f"闭环外文件被写入: {written - allowed}"
    for forbidden in ("personality", "self_model", "emotion"):
        assert not any(forbidden in name for name in written)
