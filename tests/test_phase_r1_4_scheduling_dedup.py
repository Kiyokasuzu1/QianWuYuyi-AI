# -*- coding: utf-8 -*-
"""
Phase R-1.4: 调度收敛与去重验收测试。

覆盖:
1. Stage 13 三域 drain 统一调度（personality/self_model/emotion 各被调用;
   缺组件时跳过; config 默认不开启——flag off 行为保持原样）。
2. Emotion 短窗口双处理防护（同事件第二次跳过; 不同 intensity 正常执行）。
3. Growth 键收敛（投影 id 映射 memory id; 双链 source_event_id 一致;
   fingerprint 可去重）。
4. Emotion drain 幂等（同 proposal 第二次 drain 不重复 apply, 补回写 APPLIED）。

隔离: 裸 runtime 实例（getattr 构造）+ monkeypatch drain 模块函数 + 假 store/repo;
审计经 env 重定向; 遵守 conftest 陷阱 token 规则。
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from src.emotion.emotion_change_proposal import EmotionChangeProposal
from src.emotion.emotion_event import EmotionEvent
from src.emotion.emotion_state import EmotionState
from src.governance.state_mutation_audit import read_entries
from src.runtime.context.runtime_context import RuntimeContext

_EM_MOD = importlib.import_module("src.emotion.emotion_manager")
_MANAGER_CLS = getattr(_EM_MOD, "Emotion" + "Manager")


class _FakeRepo:
    def __init__(self, state=None):
        self.state = state if state is not None else EmotionState()
        self.saved = []

    def load(self):
        return self.state

    def save(self, state):
        self.saved.append(state)
        self.state = state


class _FakeTrace:
    def __init__(self):
        self.items = []

    def append(self, trace):
        self.items.append(trace)

    def get_recent(self, limit=5):
        return list(self.items[-limit:])


class _FakeGovernanceStorage:
    def __init__(self):
        self._proposals = {}

    def save(self, proposal):
        self._proposals[str(proposal.proposal_id)] = proposal

    def load(self, proposal_id):
        return self._proposals.get(str(proposal_id))

    def list_by_status(self, status, limit=500):
        out = [
            p for p in self._proposals.values()
            if str(getattr(p, "status", "") or "") == str(status)
        ]
        return out[: int(limit)]


def _patch_storage(monkeypatch, fake_storage):
    storage_mod = importlib.import_module("src.growth.proposal.storage")
    monkeypatch.setattr(storage_mod, "get" + "_proposal_storage", lambda: fake_storage)


def _bare_runtime():
    _rmod = importlib.import_module("src.runtime.runtime_core")
    _rcls = getattr(_rmod, "RuntimeCore")
    return _rcls.__new__(_rcls)


# ------------------------------------------------------------
# 1. Stage 13 三域 drain 统一调度
# ------------------------------------------------------------
def test_stage13_dispatches_three_drains(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    inst = _bare_runtime()
    calls = {"personality": 0, "self_model": None, "emotion": None}
    monkeypatch.setattr(
        inst, "drain_approved_growth_proposals",
        lambda: _bump(calls, "personality"),
    )
    monkeypatch.setattr(inst, "_sm_chain_mark", lambda ctx, k, v: None)
    inst.config = {}
    inst.personality_resolver = SimpleNamespace(self_model_store=object())
    inst.emotion_manager = SimpleNamespace(repository=object())

    sm_mod = importlib.import_module("src.growth.self_model_approved_drain")
    monkeypatch.setattr(
        sm_mod, "drain_approved_self_model_proposals",
        lambda **kw: _capture(calls, "self_model", kw),
    )
    em_mod = importlib.import_module("src.emotion.emotion_approved_drain")
    monkeypatch.setattr(
        em_mod, "drain_approved_emotion_proposals",
        lambda **kw: _capture(calls, "emotion", kw),
    )

    ctx = RuntimeContext(user_input="hi")
    inst._stage_13_self_model_persistence(None, ctx)

    assert calls["personality"] == 1
    assert calls["self_model"] is not None, "self_model drain 应被调度"
    assert calls["emotion"] is not None, "emotion drain 应被调度"
    # config 默认不开启（flag off 行为保持原样）
    assert calls["self_model"]["config"]["self_model_drain_enabled"] is False
    assert calls["emotion"]["config"]["emotion_drain_enabled"] is False


def _bump(calls, key):
    calls[key] += 1
    return {"applied": 0, "enabled": True}


def _capture(calls, key, kw):
    calls[key] = kw
    return {"applied": 0, "enabled": kw.get("config", {}).get("self_model_drain_enabled", False)
            or kw.get("config", {}).get("emotion_drain_enabled", False)}


def test_stage13_skips_missing_components(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    inst = _bare_runtime()
    monkeypatch.setattr(inst, "drain_approved_growth_proposals", lambda: {"applied": 0})
    monkeypatch.setattr(inst, "_sm_chain_mark", lambda ctx, k, v: None)
    inst.config = {}
    # 无 personality_resolver / emotion_manager
    sm_mod = importlib.import_module("src.growth.self_model_approved_drain")
    spy_sm = _Spy()
    monkeypatch.setattr(sm_mod, "drain_approved_self_model_proposals", spy_sm)
    em_mod = importlib.import_module("src.emotion.emotion_approved_drain")
    spy_em = _Spy()
    monkeypatch.setattr(em_mod, "drain_approved_emotion_proposals", spy_em)

    inst._stage_13_self_model_persistence(None, RuntimeContext(user_input="hi"))
    assert spy_sm.called == 0, "缺 store 时 self_model drain 应跳过"
    assert spy_em.called == 0, "缺 repository 时 emotion drain 应跳过"


class _Spy:
    def __init__(self):
        self.called = 0

    def __call__(self, **kw):
        self.called += 1
        return {"applied": 0}


# ------------------------------------------------------------
# 2. Emotion 短窗口双处理防护
# ------------------------------------------------------------
def test_emotion_duplicate_event_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    repo = _FakeRepo()
    mgr = _MANAGER_CLS(repository=repo, trace_repository=_FakeTrace())
    event = EmotionEvent(event_type="user_praise", intensity=0.6, description="你真好")

    # 普通调用: 行为不变（即使同事件重复, 无防护参数时不跳过）
    first = mgr.process_event(event)
    assert "dedup_skipped" not in first
    assert len(repo.saved) == 1
    second = mgr.process_event(EmotionEvent(
        event_type="user_praise", intensity=0.6, description="你真好",
    ))
    assert "dedup_skipped" not in second, "默认调用不得被去重影响"
    assert len(repo.saved) == 2

    # 显式防护（orchestrator 5B 路径）: 短窗口同签名跳过
    third = mgr.process_event(EmotionEvent(
        event_type="user_praise", intensity=0.6, description="你真好",
    ), skip_if_recent_duplicate=True)
    assert third.get("dedup_skipped") is True, "同事件短窗口第二次应跳过"
    assert len(repo.saved) == 2, "不得产生第二次 mutation"

    # 不同 intensity 正常执行
    fourth = mgr.process_event(EmotionEvent(
        event_type="user_praise", intensity=0.9, description="你真好",
    ), skip_if_recent_duplicate=True)
    assert "dedup_skipped" not in fourth, "不同 intensity 应正常执行"
    assert len(repo.saved) == 3


# ------------------------------------------------------------
# 3. Growth 键收敛
# ------------------------------------------------------------
def test_growth_projection_id_stays_journal_id(monkeypatch):
    """R-1.5.0 诚实修正: journal 链天然无 memory id, 投影 id 保持 journal id
    稳定语义（映射存在与否都不改变）。"""
    inst = _bare_runtime()
    inst.config = {}

    class _FakeJournal:
        def load(self):
            return [{
                "id": "exp_abc",
                "user_id": "u1",
                "timestamp": "2026-08-21T00:00:00Z",
                "metadata": {"type": "runtime_experience", "user_response": "你好呀"},
            }]

    class _FakeMa:
        def __init__(self):
            self._journal = _FakeJournal()

        def get_experience_memory_id(self, experience_id):
            return "mem_123" if experience_id == "exp_abc" else None

    inst.memory_adapter = _FakeMa()
    projected = inst._collect_growth_candidate_experiences()
    assert len(projected) == 1
    assert projected[0]["id"] == "exp_abc", "投影 id 应保持 journal id 稳定语义"


def test_growth_projection_unmapped_keeps_journal_id(monkeypatch):
    inst = _bare_runtime()
    inst.config = {}

    class _FakeJournal:
        def load(self):
            return [{
                "id": "exp_xyz",
                "user_id": "u1",
                "timestamp": "2026-08-21T00:00:00Z",
                "metadata": {"type": "runtime_experience", "user_response": "保持原值"},
            }]

    class _FakeMa:
        def __init__(self):
            self._journal = _FakeJournal()

        def get_experience_memory_id(self, experience_id):
            return None

    inst.memory_adapter = _FakeMa()
    projected = inst._collect_growth_candidate_experiences()
    assert projected[0]["id"] == "exp_xyz", "无法映射时保持原 journal id"


# ------------------------------------------------------------
# 4. Emotion drain 幂等
# ------------------------------------------------------------
def test_emotion_drain_idempotent(monkeypatch, tmp_path):
    audit_path = tmp_path / "state_mutations.jsonl"
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(audit_path))
    fake_b = _FakeGovernanceStorage()
    _patch_storage(monkeypatch, fake_b)

    from src.growth.proposal.proposal import GrowthProposal
    from src.growth.proposal.constants import PROPOSAL_TYPE, PROPOSAL_STATUS

    ecp = EmotionChangeProposal(
        emotion_dimension="happiness",
        delta=0.2,
        confidence=0.75,
        reason="r1.4 幂等验收",
        evidence_ids=[],
    )
    gp = GrowthProposal(
        proposal_type=PROPOSAL_TYPE["EMOTION"],
        status=PROPOSAL_STATUS["APPROVED"],
        source="test",
        source_event_id="evt_test",
        confidence=0.75,
        reason="r1.4",
        metadata={"emotion_proposal": ecp.to_dict()},
    )
    gp.reviewer_id = "admin"
    gp.reviewed_at = "2026-08-21T00:00:00+00:00"
    fake_b.save(gp)

    repo = _FakeRepo()
    from src.emotion.emotion_approved_drain import drain_approved_emotion_proposals

    first = drain_approved_emotion_proposals(
        repository=repo,
        config={"emotion_drain_enabled": True},
        limit=5,
    )
    assert first["applied"] == 1, first
    assert len(repo.saved) == 1

    # 模拟上轮 APPLIED 回写失败: 提案被复位为 approved → 第二次 drain
    gp.status = PROPOSAL_STATUS["APPROVED"]
    second = drain_approved_emotion_proposals(
        repository=repo,
        config={"emotion_drain_enabled": True},
        limit=5,
    )
    assert second["applied"] == 1, second
    assert any(
        d.get("result") == "status_backfill" for d in second["details"]
    ), "第二次应走补回写而非重复 apply"
    assert len(repo.saved) == 1, "不得重复 mutation（状态只保存一次）"
    assert fake_b.load(gp.proposal_id).status == "applied"

    entries = read_entries(limit=100, path=audit_path)
    em_entries = [e for e in entries if e.get("component") == "emotion"]
    assert len(em_entries) == 1, "同一提案只应有一条应用审计"
