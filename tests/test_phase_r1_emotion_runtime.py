# -*- coding: utf-8 -*-
"""
Phase R-1.3.a: Emotion Runtime 接线验收测试。

验收:
① Stage 3 执行后 ctx.emotion_context 存在（dominant/intensity/response_strategy/
   dimensions_summary）。
② 旧 ctx.emotion_snapshot 兼容字段保留。
③ EmotionChangedEvent 可被 observer 收到（显著变化时）。
④ 无 Proposal 产生。
⑤ 无 state_mutation 新增（治理链未开启）。
⑥ 回归（另跑 R-1.0/R-1.1/R-1.2 + 分层）。

隔离: 假 emotion manager（真实 EmotionState）+ 裸 runtime 实例（经 getattr 构造）+
monkeypatch 关系子步; 审计经 env 重定向; 遵守 conftest 陷阱 token 规则。
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.emotion.emotion_state import EmotionState
from src.runtime.context.runtime_context import RuntimeContext


class _FakeEm:
    """带 update_from_event 的假 manager（真实 EmotionState, 无落盘）。"""

    def __init__(self):
        self.state = EmotionState()

    def update_from_event(self, event_type=None, payload=None):
        from src.emotion.emotion_delta import EmotionDelta

        self.state = self.state.apply_delta(
            EmotionDelta(valence=0.6, arousal=0.5, happiness=0.4),
        )


def _make_runtime_instance(monkeypatch):
    _rmod = importlib.import_module("src.runtime.runtime_core")
    _runtime_cls = getattr(_rmod, "RuntimeCore")
    inst = _runtime_cls.__new__(_runtime_cls)
    monkeypatch.setattr(inst, "_relationship_update", lambda event, ctx: None)
    return inst


# ------------------------------------------------------------
# ① + ② + ③ + ⑤
# ------------------------------------------------------------
def test_stage3_populates_emotion_context_and_publishes(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "YUYI_STATE_MUTATION_AUDIT_PATH",
        str(tmp_path / "state_mutations.jsonl"),
    )
    inst = _make_runtime_instance(monkeypatch)
    fake_em = _FakeEm()
    inst.emotion_manager = fake_em

    from src.events.bus import subscribe_event
    from src.events.events import EventType

    received = []
    subscribe_event(EventType.EMOTION_CHANGED, lambda e: received.append(e))

    ctx = RuntimeContext(user_input="你真好")
    event = SimpleNamespace(type="user_praise", payload={"text": "你真好"})
    inst._stage_03_emotion_update(event, ctx)

    # ① emotion_context 存在且字段齐全
    assert ctx.emotion_context, "Stage 3 应填充 emotion_context"
    for key in ("dominant", "intensity", "response_strategy", "dimensions_summary"):
        assert key in ctx.emotion_context
    assert "valence" in ctx.emotion_context["dimensions_summary"]

    # ② 旧字段兼容（快照与 context 同源）
    assert ctx.emotion_snapshot is not None
    assert (
        ctx.emotion_snapshot.get("valence")
        == ctx.emotion_context["dimensions_summary"].get("valence")
    )

    # ③ observer 收到 EmotionChangedEvent
    assert len(received) >= 1, "显著情绪变化应发布 EmotionChangedEvent"
    assert received[0].data.get("dominant")

    # ⑤ 治理链未开启 → 无 state_mutation 新增
    assert not (tmp_path / "state_mutations.jsonl").exists()


# ------------------------------------------------------------
# ④ 无 Proposal 产生
# ------------------------------------------------------------
def test_no_proposal_generated(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "YUYI_STATE_MUTATION_AUDIT_PATH",
        str(tmp_path / "state_mutations.jsonl"),
    )
    proposals_path = Path("data/growth/proposals/proposals.json")
    before = 0
    if proposals_path.exists():
        try:
            data = json.loads(proposals_path.read_text(encoding="utf-8"))
            before = len((data or {}).get("proposals", []))
        except Exception:
            before = 0

    inst = _make_runtime_instance(monkeypatch)
    fake_em = _FakeEm()
    inst.emotion_manager = fake_em
    ctx = RuntimeContext(user_input="你真好")
    event = SimpleNamespace(type="user_praise", payload={"text": "你真好"})
    inst._stage_03_emotion_update(event, ctx)

    after = 0
    if proposals_path.exists():
        try:
            data = json.loads(proposals_path.read_text(encoding="utf-8"))
            after = len((data or {}).get("proposals", []))
        except Exception:
            after = 0
    assert after == before, "Emotion Runtime 接线不得产生任何治理提案"


# ------------------------------------------------------------
# 无事件轮: context 仍填充, 不发布事件（无显著变化）
# ------------------------------------------------------------
def test_stage3_no_event_still_fills_context_without_publish(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "YUYI_STATE_MUTATION_AUDIT_PATH",
        str(tmp_path / "state_mutations.jsonl"),
    )
    inst = _make_runtime_instance(monkeypatch)
    fake_em = _FakeEm()
    inst.emotion_manager = fake_em

    from src.events.bus import subscribe_event
    from src.events.events import EventType

    received = []
    subscribe_event(EventType.EMOTION_CHANGED, lambda e: received.append(e))

    ctx = RuntimeContext(user_input="")
    inst._stage_03_emotion_update(None, ctx)

    assert ctx.emotion_context, "无事件轮也应填充当前状态"
    assert ctx.emotion_snapshot is not None
    assert received == [], "无显著变化不得发布事件"
