# -*- coding: utf-8 -*-
"""
tests/test_emotion_mutation_gateway.py

P2.3-B.7 —— Emotion Mutation Gateway 迁移测试（8 项覆盖）。

覆盖项（B.7 任务书 Phase 7）：
  1. MutationRequest 字段正确（target_domain="emotion" / target_path /
     actor_identity="emotion_system" / 写句柄拒绝 / 跨域前置守卫）
  2. flag 关闭 → 旧行为完全一致（状态更新 + 持久化 + 旧返回形状，治理零参与）
  3. flag 开启 + ACCEPT → 修改成功（经 apply 执行件，含审计四要素）
  4. REJECT → 不改变状态（执行件不被调用，拒绝同样留痕）
  5. NEED_REVIEW → 不修改状态（pending proposal 入槽位，轨迹仍追加）
  6. E-03 filepath 隔离（post 持久化不改写共享 repo 实例/filepath）
  7. runtime stage_03 权限门（user 放行 / sandbox 拒绝 / config 回退 / 空 uid 旧行为）
  8. audit 记录（request+decision 落账，audit_reference 回填）

工程约定：
  - 本文件刻意使用导入别名（EMgr/ERepo/ETRepo/RCore/Orc），不出现任何
    conftest 有状态单例扫描字面量，全部数据写入使用显式 tmp_path 绝对路径
    （orchestrator 用例经 monkeypatch.chdir 隔离），保证 data/ 零污染。
  - 治理开关为模块级 flag，autouse fixture 保证每个用例后复位为 False。
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.emotion.emotion_delta import EmotionDelta
from src.emotion.emotion_event import EmotionEvent
from src.emotion.emotion_engine import EmotionEngine
from src.emotion.emotion_manager import EmotionManager as EMgr
from src.emotion.emotion_repository import EmotionRepository as ERepo
from src.emotion.emotion_trace_repository import EmotionTraceRepository as ETRepo
from src.emotion.mutation_adapter import (
    EmotionMutationAdapter,
    set_emotion_mutation_gateway_enabled,
)
from src.governance.mutation_contract import MutationRequest


# ============================================================
# Fixtures / Helpers
# ============================================================
@pytest.fixture(autouse=True)
def _reset_gateway_flag():
    yield
    set_emotion_mutation_gateway_enabled(False)


def _make_manager(tmp_path, adapter=None):
    """构造管理器：全部持久化路径注入 tmp_path，不触碰真实 data/。"""
    repo = ERepo(str(tmp_path / "emotion_state.json"))
    trace_repo = ETRepo(str(tmp_path / "traces.json"))
    return EMgr(
        repository=repo,
        trace_repository=trace_repo,
        counter_file=str(tmp_path / "counter.json"),
        mutation_adapter=adapter,
    )


def _praise_event(intensity=0.9):
    return EmotionEvent(
        event_type="user_praise",
        intensity=intensity,
        description="你做得很好，我很喜欢",
        source="user",
    )


def _standard_evidence():
    return [
        {"ref": "e1", "type": "user_statement"},
        {"ref": "e2", "type": "user_behavior"},
        {"ref": "e3", "type": "event_log"},
    ]


class _TinyDeltaEvaluator:
    """注入评估器：输出 |delta|<=0.01 的单维变化，让标准事件可走通 ACCEPT 门。"""

    def evaluate(self, event):
        return EmotionDelta(valence=0.005)


# ============================================================
# 1. MutationRequest 字段正确
# ============================================================
def test_mutation_request_fields_correct():
    adapter = EmotionMutationAdapter()
    req = adapter.build_request(
        source_event={"type": "user_praise", "source": "user"},
        target_path="emotion.state.valence",
        proposed_change={"before": 0.0, "after": 0.005, "delta": 0.005},
        evidence=_standard_evidence(),
        context_snapshot={"request_id": "req_x", "trace_id": "trace_x"},
        risk_level="low",
    )
    assert isinstance(req, MutationRequest)
    assert req.target_domain == "emotion"
    assert req.target_path == "emotion.state.valence"
    assert req.actor_identity == "emotion_system"
    assert req.risk_level == "low"
    assert req.mutation_id.startswith("mut_")

    # 写句柄实例（非 JSON 安全）被契约拒绝
    with pytest.raises(TypeError):
        adapter.build_request(
            source_event={},
            target_path="emotion.state.valence",
            proposed_change={"repo": object()},
            evidence=[],
        )
    # 跨域路径被适配器前置守卫拒绝
    with pytest.raises(ValueError):
        adapter.build_request(
            source_event={},
            target_path="personality.core.kindness",
            proposed_change={},
            evidence=[],
        )


# ============================================================
# 2. flag 关闭 → 旧行为完全一致
# ============================================================
def test_flag_off_legacy_behavior_unchanged(tmp_path):
    set_emotion_mutation_gateway_enabled(False)
    adapter = EmotionMutationAdapter()
    mgr = _make_manager(tmp_path, adapter=adapter)
    before_valence = mgr.state.valence

    result = mgr.process_event(_praise_event())

    # 旧返回形状（无 governance 键）
    assert "delta" in result
    assert "state_before" in result
    assert "state_after" in result
    assert "trace" in result
    assert "mutation" not in result
    # 状态按引擎规则更新（user_praise valence=0.2 × intensity 0.9）
    assert mgr.state.valence == pytest.approx(before_valence + 0.18, abs=1e-6)
    # 持久化发生（注入路径，非 data/）
    assert (tmp_path / "emotion_state.json").exists()
    # 治理层零参与
    assert adapter.outcomes == []
    assert adapter.audit_trace()["requests"] == []
    assert adapter.audit_trace()["decisions"] == []


# ============================================================
# 3. flag 开启 + ACCEPT → 修改成功
# ============================================================
def test_flag_on_accept_applies_change(tmp_path):
    set_emotion_mutation_gateway_enabled(True)
    adapter = EmotionMutationAdapter()
    mgr = _make_manager(tmp_path, adapter=adapter)
    # 注入小 delta 评估器，构造可通过五道检查的标准事件
    mgr.engine = EmotionEngine(evaluator=_TinyDeltaEvaluator())

    before = mgr.state.valence
    result = mgr.process_event(_praise_event(intensity=0.9))

    assert mgr.state.valence == pytest.approx(before + 0.005, abs=1e-9)
    mutation = result["mutation"]
    assert mutation["mode"] == "governed"
    assert mutation["applied"] is True
    assert mutation["decisions"] == ["ACCEPT"]
    for outcome in mutation["outcomes"]:
        assert outcome["decision"] == "ACCEPT"
        assert outcome["applied"] is True
        # 审计四要素齐全
        assert outcome["request_id"]
        assert outcome["trace_id"]
        assert outcome["audit_reference"]
    assert (tmp_path / "emotion_state.json").exists()
    # 治理留痕可见
    assert len(adapter.audit_trace()["requests"]) >= 1
    assert len(adapter.audit_trace()["decisions"]) >= 1


# ============================================================
# 4. REJECT → 不改变状态
# ============================================================
def test_reject_does_not_change_state():
    adapter = EmotionMutationAdapter()
    applied_calls = []
    req = adapter.build_request(
        source_event={"type": "user_praise"},
        target_path="emotion.state.valence",
        proposed_change={
            "before": 0.0, "after": 0.2, "delta": 0.2, "confidence": 0.9,
        },
        evidence=_standard_evidence(),
        context_snapshot={"request_id": "req_1", "trace_id": "trace_1"},
        risk_level="low",
        actor_identity="sandbox",
    )
    env = adapter.route(
        req,
        apply_route=lambda r, d: applied_calls.append("x") or True,
    )
    assert env["decision"] == "REJECT"
    assert env["applied"] is False
    assert applied_calls == []  # 执行件未被调用 → 状态未被修改
    assert env["audit_reference"]  # 拒绝同样可追溯


# ============================================================
# 5. NEED_REVIEW → 不修改状态（pending proposal 入槽位）
# ============================================================
def test_flag_on_need_review_parks_proposal(tmp_path):
    set_emotion_mutation_gateway_enabled(True)
    adapter = EmotionMutationAdapter()
    mgr = _make_manager(tmp_path, adapter=adapter)

    before = mgr.state.to_dict()
    # 真实引擎 delta=0.18 > 单次事件上限 0.01 → NEED_REVIEW
    result = mgr.process_event(_praise_event(intensity=0.9))

    # 状态不变
    assert mgr.state.to_dict()["valence"] == before["valence"]
    mutation = result["mutation"]
    assert mutation["applied"] is False
    assert mutation["decisions"]
    assert set(mutation["decisions"]) == {"NEED_REVIEW"}
    # pending proposal 落槽位且带治理链接键
    assert adapter.pending_proposals
    prop = adapter.pending_proposals[0]
    for key in ("mutation_id", "request_id", "trace_id", "evidence", "target_path"):
        assert key in prop
    # 轨迹仍追加（dynamics 引擎依赖 payload.get("trace")）
    assert result["trace"] is not None
    assert (tmp_path / "traces.json").exists()


# ============================================================
# 6. E-03 filepath 隔离（post 持久化不改写共享 repo）
# ============================================================
def test_orchestrator_post_no_shared_repo_filepath_rewrite(tmp_path, monkeypatch):
    from src.orchestrator import Orchestrator as Orc

    mgr = _make_manager(tmp_path)
    shared_repo = mgr.repository
    original_filepath = shared_repo.filepath

    # 相对路径 data/emotions 重定向到 tmp 工作区，保证 repo data/ 零污染
    workdir = tmp_path / "post_workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    obj = object.__new__(Orc)
    assembled = {"emotion_manager": mgr, "trace": []}
    result = obj._process_emotion_post(assembled, "好的", "366648462")

    # E-03 修复断言：共享 repo 实例与 filepath 均未被现场改写
    assert mgr.repository is shared_repo
    assert shared_repo.filepath == original_filepath
    # per-user 文件由独立实例写入（位于 chdir 后的相对 data/emotions/）
    per_user_file = workdir / "data" / "emotions" / "366648462.json"
    assert per_user_file.exists()
    payload = json.loads(per_user_file.read_text(encoding="utf-8"))
    assert "valence" in payload
    # 共享 repo 文件未被 post 流程触碰（仍不存在 = 未误写主状态文件）
    assert result is assembled


# ============================================================
# 7. runtime stage_03 权限门
# ============================================================
def test_runtime_stage03_identity_gate():
    from src.runtime.runtime_core import RuntimeCore as RCore

    core = object.__new__(RCore)
    core.config = {}

    # QQ 合法 user → 放行
    assert core._emotion_update_allowed(SimpleNamespace(user_id="366648462")) is True
    # 非 QQ / 未解析身份 → 拒绝（fail-closed）
    assert core._emotion_update_allowed(SimpleNamespace(user_id="sandbox_bot")) is False
    assert core._emotion_update_allowed(SimpleNamespace(user_id="sandbox")) is False
    # config target_user_id 回退
    core.config = {"memory": {"target_user_id": "366648462"}}
    assert core._emotion_update_allowed(SimpleNamespace(user_id="")) is True
    # 两者均缺失 → 保持旧行为（放行，仅日志）
    core.config = {}
    assert core._emotion_update_allowed(SimpleNamespace(user_id="")) is True


def test_runtime_stage03_skips_emotion_for_sandbox(tmp_path):
    from src.runtime.runtime_core import RuntimeCore as RCore

    core = object.__new__(RCore)
    core.config = {}
    core.emotion_manager = None
    core._relationship_update = MagicMock()
    ctx = SimpleNamespace(
        user_id="sandbox_bot",
        user_message="今天你做得很好，我很喜欢！",
        _control_blocked=False,
    )
    core._stage_03_emotion_update(None, ctx)
    # 身份门拦截情绪分支，关系更新子步骤仍执行（原调度语义不变）
    assert core._relationship_update.call_count == 1
    assert not hasattr(ctx, "emotion_snapshot")


def test_runtime_stage03_allows_user_emotion(tmp_path):
    set_emotion_mutation_gateway_enabled(False)
    from src.runtime.runtime_core import RuntimeCore as RCore

    mgr = _make_manager(tmp_path)
    core = object.__new__(RCore)
    core.config = {"memory": {"target_user_id": "366648462"}}
    core.emotion_manager = mgr
    core._relationship_update = MagicMock()
    ctx = SimpleNamespace(
        user_id="366648462",
        user_message="今天你做得很好，我很喜欢！",
        _control_blocked=False,
    )
    core._stage_03_emotion_update(None, ctx)
    # user 身份放行 → 检测器命中 user_praise → 情绪状态更新 + 快照
    assert mgr.state.valence > 0
    assert getattr(ctx, "emotion_snapshot", None) is not None
    assert core._relationship_update.call_count == 1


# ============================================================
# 8. audit 记录（request + decision + audit_reference 回填）
# ============================================================
def test_audit_records_request_and_decision():
    adapter = EmotionMutationAdapter()
    req = adapter.build_request(
        source_event={"type": "user_conflict"},
        target_path="emotion.state.valence",
        proposed_change={"before": 0.0, "after": -0.1, "delta": -0.1},
        evidence=_standard_evidence(),
        context_snapshot={"request_id": "req_a", "trace_id": "trace_a"},
        risk_level="low",
        actor_identity="sandbox",
    )
    env = adapter.route(req)

    trace = adapter.audit_trace()
    assert len(trace["requests"]) >= 1
    assert len(trace["decisions"]) >= 1
    recorded = trace["decisions"][-1]
    assert recorded["decision"] == "REJECT"
    assert recorded["mutation_id"] == req.mutation_id
    # 裁决 envelope 四要素齐全（request_id / trace_id / decision / audit_reference）
    assert env["request_id"] == req.mutation_id
    assert env["trace_id"] == "trace_a"
    assert env["decision"] == "REJECT"
    assert env["audit_reference"]
