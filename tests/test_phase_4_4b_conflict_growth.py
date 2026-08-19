"""
Phase 4.4-B — 冲突成长测试（Conflict Growth）

任务卡验收：
- B1: 冲突不会直接改 Personality
- B2: 不会覆盖历史 evidence（两个相反证据必须同时存在）
- B3: Proposal 可解释（evidence_ids / source_event_id / evaluator_meta）
- B4: Approval 前不生效

场景：
- 阵营 A（详细偏好）："我了解你通常喜欢详细的技术解释，我们一起养成了深入讨论的习惯"
- 阵营 B（简短偏好）："我了解我们的交流，但我个人偏好简短的回答方式"
- 两者均可被 RelationshipEventExtractor 识别为 preference_learning（关键词"了解/通常/偏好"），
  经 4.2-D 关系证据链进入 Growth——这是偏好类证据到达 Growth 的现实通道
  （自由文本直接经 Stage 4 的 evaluator confidence 仅 0.425，level=trace，不会成提案，
  这是既有评估严格性，非缺陷）。

设计原则：
- Tier 1（B1~B4）为硬断言——安全红线必须成立
- Tier 2（审批序列）只断言安全不变量；语义冲突处理能力（条件偏好/冲突证据）
  属于观察项，实际行为如实记录进报告，不断言期望中但不存在的行为
"""
from __future__ import annotations

import os
import tempfile

import pytest

DETAILED_MSG = "我了解你通常喜欢详细的技术解释，我们一起养成了深入讨论的习惯"
BRIEF_MSG = "我了解我们的交流，但我个人偏好简短的回答方式"


def _make_rc(tmp: str, adapter=None):
    from src.runtime.runtime_core import RuntimeCore

    rc = RuntimeCore(config={
        "adapters_enabled": True,
        "relationship_enabled": True,
        "event_driven_enabled": True,
        "memory_store_path": os.path.join(tmp, "mem.json"),
        "growth_proposals_path": os.path.join(tmp, "gp.json"),
        "state_file": os.path.join(tmp, "runtime.json"),
    })
    if adapter is not None:
        rc._relationship_evidence_adapter = adapter
    return rc


def _make_service(tmp: str):
    from src.growth.growth_integration import GrowthIntegrationService
    from src.growth.proposal_store import ProposalStore
    from src.growth.proposal_manager import ProposalManager
    from src.personality.personality_growth_record import PersonalityGrowthHistory

    growth_history = PersonalityGrowthHistory()
    store = ProposalStore(path=os.path.join(tmp, "proposals.jsonl"))
    manager = ProposalManager(
        store=store, growth_history=growth_history,
        config={"auto_accept_enabled": False, "confidence_threshold": 0.8},
    )
    return GrowthIntegrationService(
        proposal_manager=manager, growth_history=growth_history,
        config={"auto_accept_enabled": False, "confidence_threshold": 0.8},
    )


def _make_adapter(svc):
    from src.growth.relationship_evidence_adapter import RelationshipEvidenceAdapter

    return RelationshipEvidenceAdapter(integration_service=svc)


def _feed(rc, msg: str, tag: str, times: int = 3):
    """同一阵营重复表达（GracePeriod 首次只记账，需多次出现）。"""
    for i in range(times):
        rc.record_relationship_interaction(
            user_message=msg, evidence_id=f"mem_{tag}_{i}", emotion_tag="joy",
        )


def _pending(svc):
    return svc.list_proposals(status="pending", limit=50)


def _traits(rc):
    pr = rc.get_personality_resolver()
    if pr is None:
        return {}
    snap = pr.resolve()
    d = snap.to_dict() if hasattr(snap, "to_dict") else {}
    return d.get("traits", {}) or {}


# ============================================================
# B1 + B4：冲突不会直接改 Personality；Approval 前不生效
# ============================================================

class TestB1B4NoPersonalityChangeWithoutApproval:
    def test_conflicting_camps_leave_personality_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(tmp)
            rc = _make_rc(tmp, _make_adapter(svc))

            before_traits = dict(_traits(rc))
            _feed(rc, DETAILED_MSG, "d")
            _feed(rc, BRIEF_MSG, "b")

            # 两个相反证据都已进入 Growth 管线（前提：确实产生了提案）
            assert len(_pending(svc)) >= 2, "两个阵营应各自产生 pending proposal"

            # B4：未审批 → 成长历史为零
            assert svc.growth_count() == 0, "未经 approval 不得有任何成长应用"

            # B1：人格快照与初始完全一致（无任何自动漂移）
            after_traits = _traits(rc)
            assert after_traits == before_traits, (
                f"冲突证据不得直接改变人格：{before_traits} → {after_traits}"
            )


# ============================================================
# B2：两个相反证据共存，互不覆盖
# ============================================================

class TestB2OpposingEvidenceCoexists:
    def test_both_camps_produce_separate_proposals(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(tmp)
            rc = _make_rc(tmp, _make_adapter(svc))

            _feed(rc, DETAILED_MSG, "d")
            proposals_after_a = _pending(svc)
            assert len(proposals_after_a) >= 1
            p1 = proposals_after_a[0]
            p1_evidence = list(p1.evidence_ids)

            _feed(rc, BRIEF_MSG, "b")
            proposals_after_b = _pending(svc)

            # B2：相反阵营到来后，历史提案仍在（未覆盖/未删除）
            ids = {p.id for p in proposals_after_b}
            assert p1.id in ids, "新冲突证据不得覆盖/删除历史提案"
            assert len(proposals_after_b) >= 2, "两个相反证据必须同时存在"

            # B2：历史提案的 evidence 保持完整
            p1_after = next(p for p in proposals_after_b if p.id == p1.id)
            assert list(p1_after.evidence_ids) == p1_evidence
            assert p1_after.status == "pending"

    def test_same_camp_repeats_dedupe(self):
        """同阵营重复表达应被 fingerprint 去重（不会提案爆炸）。"""
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(tmp)
            rc = _make_rc(tmp, _make_adapter(svc))

            _feed(rc, DETAILED_MSG, "d", times=5)
            props = _pending(svc)
            assert len(props) == 1, (
                f"同一阵营 5 次重复表达应去重为 1 个提案，实际={len(props)}"
            )


# ============================================================
# B3：Proposal 可解释
# ============================================================

class TestB3ProposalExplainable:
    def test_proposals_carry_traceability(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(tmp)
            rc = _make_rc(tmp, _make_adapter(svc))

            _feed(rc, DETAILED_MSG, "d")
            _feed(rc, BRIEF_MSG, "b")
            props = _pending(svc)
            assert len(props) >= 2

            for p in props:
                # 证据锚定
                assert p.evidence_ids, f"{p.id} 缺少 evidence_ids"
                assert all(e.startswith("mem_") for e in p.evidence_ids)
                # 事件溯源
                assert p.source_event_id, f"{p.id} 缺少 source_event_id"
                # 评估元数据：来源 + 关系事件锚点 + 信号解释
                meta = p.evaluator_meta or {}
                assert meta.get("source") == "relationship"
                assert meta.get("relationship_event_type") == "preference_learning"
                # 变化项必须带人类可读理由（ChangeItem 对象，属性访问）
                for change in p.proposed_changes:
                    reason = getattr(change, "reason", None)
                    if reason is None and isinstance(change, dict):
                        reason = change.get("reason")
                    assert reason, f"{p.id} 的 change 缺少 reason"


# ============================================================
# Tier 2：审批序列观察（只断言安全不变量）
# ============================================================

class TestConflictApprovalSequence:
    def test_apply_both_camps_safety_invariants(self):
        """依次审批两个相反阵营提案：记录实际行为，断言安全边界。"""
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(tmp)
            rc = _make_rc(tmp, _make_adapter(svc))

            _feed(rc, DETAILED_MSG, "d")
            _feed(rc, BRIEF_MSG, "b")
            props = _pending(svc)
            assert len(props) >= 2
            p1, p2 = props[0], props[1]

            # 审批阵营 A
            r1 = svc.apply_proposal(p1.id, actor="user")
            assert r1.get("status") == "applied", f"阵营 A apply 失败: {r1}"
            assert svc.growth_count() == 1

            # 审批相反阵营 B——观察：是否被 conflict guard 拦截（如实记录）
            r2 = svc.apply_proposal(p2.id, actor="user")

            # 安全不变量 1：apply 流程不崩溃、返回结构完整
            assert isinstance(r2, dict) and r2.get("status") in {
                "applied", "deferred", "rejected",
            }, f"相反提案 apply 返回异常: {r2}"

            # 安全不变量 2：每个已应用变化项的 delta 受 schema 上限约束
            # （MAX_SINGLE_EVENT_DELTA=0.01）。注意：本测试装配中 apply 作用于
            # ProposalManager 内置 PersonalityAdapter（growth history 可验证），
            # rc 的 PersonalityResolver 是独立实例——漂移上界以提案数据为准。
            from src.growth import growth_schema as _gs

            for p in (p1, p2):
                for change in p.proposed_changes:
                    before = float(getattr(change, "before", 0.0) or 0.0)
                    after = float(getattr(change, "after", 0.0) or 0.0)
                    assert abs(after - before) <= _gs.MAX_SINGLE_EVENT_DELTA, (
                        f"单变化项 delta 越界: {before} → {after}"
                    )

            # 安全不变量 3：resolver 若暴露 traits，漂移必须有界
            traits = _traits(rc)
            for k, v in traits.items():
                assert abs(float(v) - 0.5) < 0.05, (
                    f"人格维度 {k} 漂移越界: {v}"
                )

            # 观察记录（不断言方向）：相反提案是否触发冲突守卫
            print(
                f"\n[4.4-B 观察] 相反提案 apply 结果: status={r2.get('status')}, "
                f"reasons={r2.get('reasons') or r2.get('reason')}"
            )

    def test_last_input_does_not_overwrite(self):
        """任务卡核心红线：最后一次输入不得覆盖人格。

        先 A 后 B 全部审批后，人格不得等于"B 单独决定"的状态——
        因为 A 的成长记录必须仍在 growth history 中（可追溯、未被回滚）。
        """
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(tmp)
            rc = _make_rc(tmp, _make_adapter(svc))

            _feed(rc, DETAILED_MSG, "d")
            _feed(rc, BRIEF_MSG, "b")
            props = _pending(svc)
            for p in props:
                svc.apply_proposal(p.id, actor="user")

            # 两个阵营的成长记录都必须存在（历史不被最后一次输入抹除）
            assert svc.growth_count() >= 2, (
                "两个阵营的成长记录应都保留，不允许 last-input-overwrite"
            )
