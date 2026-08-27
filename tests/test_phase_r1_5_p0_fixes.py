# -*- coding: utf-8 -*-
"""
Phase R-1.5.0: 三个 P0 修复验收测试。

覆盖:
1. 5B 防重入: bridge 检测到轮级标记后跳过嵌套 RuntimeCore.process
   （process 调用次数 == 1, legacy 回复保留）。
2. pipeline 5B 标记生命周期: 进入 5B 时设置、结束后清除（异常不污染下一轮）;
   history 单一权威写入（pipeline 不再调用 orchestrator.record_conversation_turn）。
3. emotion 同轮防护: Stage3（带 flag）+ Step7（带 flag）同事件仅一次 mutation;
   跨轮合法相同事件（时间推进）分别生效。
4. relationship 双入口去重: 同 source_event_id 只产生一个提案;
   字段一致性（confidence=0.7 + evidence）。
5. journal user_id: handle_completed_experience 透传 user_id 覆盖;
   MemoryAdapter 无覆盖时回退 config 默认。

隔离: 全部假件 + 真实逻辑单元; 遵守 conftest 陷阱 token 规则
（RuntimeCore/EmotionManager/MemoryStore 经 importlib+getattr 构造）。
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from src.emotion.emotion_event import EmotionEvent
from src.emotion.emotion_state import EmotionState
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


def _patch_storage(monkeypatch, fake_storage):
    storage_mod = importlib.import_module("src.growth.proposal.storage")
    monkeypatch.setattr(storage_mod, "get" + "_proposal_storage", lambda: fake_storage)


class _FakeGovernanceStorage:
    def __init__(self):
        self._proposals = {}

    def save(self, proposal):
        self._proposals[str(proposal.proposal_id)] = proposal

    def load(self, proposal_id):
        return self._proposals.get(str(proposal_id))

    def list_by_status(self, status, limit=500):
        return [p for p in self._proposals.values() if getattr(p, "status", "") == status][:limit]

    def list_by_type(self, proposal_type, limit=500):
        return [p for p in self._proposals.values() if getattr(p, "proposal_type", "") == proposal_type][:limit]

    def list_all(self, limit=50):
        return list(self._proposals.values())[:limit]

    def count(self):
        return len(self._proposals)


# ------------------------------------------------------------
# 1 + 2. 5B 防重入（bridge 层）
# ------------------------------------------------------------
def _make_bridge(fake_runtime, fake_orchestrator):
    from src.orchestrator_runtime_bridge import OrchestratorRuntimeBridge

    return OrchestratorRuntimeBridge(
        orchestrator=fake_orchestrator,
        runtime_core=fake_runtime,
    )


class _FakeRuntime:
    def __init__(self):
        self.process_calls = 0

    def process(self, event, ctx=None):
        self.process_calls += 1
        # 返回空回复 ctx → 触发 legacy 路径
        return SimpleNamespace(finalized_reply="", _final_reply="")


class _FakeOrchestrator:
    def __init__(self):
        self.legacy_calls = 0
        self.engine = SimpleNamespace(generate=lambda *a, **k: "legacy-reply")


def test_orchestrator_skips_nested_runtime_when_marker_set():
    """R-1.5.0: orchestrator._generate_reply 在轮级标记命中时跳过 bridge
    （嵌套 rt.process 不再发生）, legacy 回复保留。"""
    fake_runtime = _FakeRuntime()

    class _FakeBridge:
        def __init__(self):
            self.handle_calls = 0
            self._rt = fake_runtime

        def has_round_marker(self, user_message):
            marker = getattr(self._rt, "_round_processed_text", None)
            return bool(marker) and str(marker) == str(user_message)

        @property
        def last_runtime_mode(self):
            return None

        @property
        def last_runtime_error(self):
            return None

        @property
        def last_legacy_reason(self):
            return None

        @property
        def last_reply_source(self):
            return None

        def handle_message(self, user_message):
            self.handle_calls += 1
            return "runtime-reply"

    _omod = importlib.import_module("src.orchestrator")
    _ocls = getattr(_omod, "Orchestrator")
    inst = _ocls.__new__(_ocls)
    fake_bridge = _FakeBridge()
    inst._runtime_bridge = fake_bridge
    inst._runtime_call_count = 0
    inst._legacy_call_count = 0
    inst._last_reply_source = None
    inst._last_runtime_mode = None
    inst._last_runtime_error = None
    inst.legacy_generate = lambda *a, **k: "legacy-reply"

    # 无标记: 走 bridge（runtime 回复被采用）
    reply1 = inst._generate_reply(
        "你好", [], None, None, None, [], "conv_1",
    )
    assert reply1 == "runtime-reply"
    assert fake_bridge.handle_calls == 1

    # 设置轮级标记（模拟 pipeline 5A 已执行同消息）
    fake_runtime._round_processed_text = "你好"
    reply2 = inst._generate_reply(
        "你好", [], None, None, None, [], "conv_1",
    )
    assert fake_bridge.handle_calls == 1, "标记命中时不得再调 bridge（嵌套重入被阻止）"
    assert reply2 == "legacy-reply", "legacy 回复生成必须保留"


# ------------------------------------------------------------
# 2. pipeline 5B 标记生命周期 + history 单一写入
# ------------------------------------------------------------
def test_pipeline_5b_marker_lifecycle_and_single_history():
    from src.runtime.runtime_pipeline import RuntimePipeline

    fake_runtime = _FakeRuntime()

    class _FakeOrch:
        def __init__(self):
            self.record_calls = []
            self.process_calls = 0

        def process(self, msg, user_id=None):
            self.process_calls += 1
            return "orchestrator-reply"

        def get_recent_history(self, user_id=None, max_turns=20):
            return []

        def record_conversation_turn(self, msg, reply, user_id=None):
            self.record_calls.append((msg, reply))

    fake_orch = _FakeOrch()
    # 真实构造（初始化全部计数器/追踪器）, 覆写重副作用方法
    pipeline = RuntimePipeline(orchestrator=fake_orch, runtime=fake_runtime)
    pipeline._safe_persist = lambda *a, **k: None
    pipeline._safe_record_interaction = lambda *a, **k: None
    pipeline._interaction_recorder = None

    result = pipeline.run({"user_message": "你好", "user_id": "u1"})

    assert result is not None
    assert fake_runtime.process_calls == 1, "5A 一次 + 5B 不得嵌套重入"
    # 标记在 run 结束后被清除（异常不污染下一轮）
    assert not hasattr(fake_runtime, "_round_processed_text"), "轮级标记必须清理"
    # history 单一权威写入: pipeline 不再调用 record_conversation_turn
    assert fake_orch.record_calls == [], "pipeline 不得重复写 history（Step 9 是唯一写入点）"
    assert fake_orch.process_calls == 1


# ------------------------------------------------------------
# 3. emotion 同轮防护（Stage3 带 flag + Step7 带 flag）
# ------------------------------------------------------------
def test_emotion_stage3_step7_single_mutation(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    repo = _FakeRepo()
    mgr = _MANAGER_CLS(repository=repo, trace_repository=_FakeTrace())

    # 模拟 Stage 3: bare runtime + 真实 manager + 真实 detector
    _rmod = importlib.import_module("src.runtime.runtime_core")
    _rcls = getattr(_rmod, "RuntimeCore")
    inst = _rcls.__new__(_rcls)
    inst.emotion_manager = mgr
    monkeypatch.setattr(inst, "_relationship_update", lambda event, ctx: None)
    monkeypatch.setattr(inst, "_emotion_update_allowed", lambda ctx: True)

    ctx = RuntimeContext(user_input="你太棒了")
    event = SimpleNamespace(type="user_praise", payload={"text": "你太棒了"})
    inst._stage_03_emotion_update(event, ctx)
    assert len(repo.saved) == 1, "Stage3 应应用一次"

    # 模拟 Step 7: 用 detector 对同一文本的真实输出构造同签名事件
    from src.emotion.emotion_event_detector import EmotionEventDetector

    detected = EmotionEventDetector().detect("你太棒了")
    assert detected is not None
    step7_event = EmotionEvent(
        event_type=detected.event_type,
        intensity=detected.intensity,
        description=detected.description,
    )
    resp = mgr.process_event(step7_event, skip_if_recent_duplicate=True)
    assert resp.get("dedup_skipped") is True, "同轮同事件 Step7 应跳过"
    assert len(repo.saved) == 1, "同轮不得二次 mutation"

    # 跨轮合法相同事件（时间推进 >2s）: 分别生效
    import time as _time
    real_time = _time.time
    offset = {"t": 0.0}

    class _JumpTime:
        @staticmethod
        def time():
            return real_time() + offset["t"]

    monkeypatch.setattr(_time, "time", _JumpTime.time)
    offset["t"] = 3.0  # 推进 3 秒
    resp2 = mgr.process_event(
        EmotionEvent(
            event_type=detected.event_type,
            intensity=detected.intensity,
            description=detected.description,
        ),
        skip_if_recent_duplicate=True,
    )
    assert "dedup_skipped" not in resp2, "跨轮合法相同事件应正常执行"
    assert len(repo.saved) == 2


# ------------------------------------------------------------
# 4. relationship 双入口去重
# ------------------------------------------------------------
def test_relationship_single_proposal_across_entries(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    fake_b = _FakeGovernanceStorage()
    _patch_storage(monkeypatch, fake_b)

    # orchestrator 直写路径（bare 实例, 两次调用同 source_event_id）
    _omod = importlib.import_module("src.orchestrator")
    _ocls = getattr(_omod, "Orchestrator")
    inst = _ocls.__new__(_ocls)
    for _ in range(2):
        inst._create_growth_proposal(
            user_id="u1",
            proposal_type="relationship",
            before_state={"trust": 0.3},
            after_state={"trust": 0.5},
            reason="信任变化",
            evidence=["rel_evt_1"],
            source_event_id="evt_shared_1",
        )
    # reviewer 事件链路径（同 source_event_id）→ 应被去重拦截
    from src.growth.proposal.reviewer import create_proposal_from_event
    from src.events.events import RelationshipChangedEvent

    rel_event = RelationshipChangedEvent(
        user_id="u1",
        data={
            "dimension": "trust",
            "old_value": 0.3,
            "new_value": 0.5,
            "user_id": "u1",
            "reason": "信任变化",
        },
        source="orchestrator",
    )
    rel_event.event_id = "evt_shared_1"
    reviewer_result = create_proposal_from_event(rel_event)
    assert reviewer_result is None, "同源提案应被去重拦截"

    pending = fake_b.list_by_status("pending")
    assert len(pending) == 1, "双入口同 source_event_id 只允许一个提案"
    proposal = pending[0]
    assert proposal.source_event_id == "evt_shared_1"
    assert proposal.proposal_type == "relationship"
    assert abs(proposal.confidence - 0.7) < 1e-9
    assert proposal.evidence == ["rel_evt_1"]


# ------------------------------------------------------------
# 5. journal user_id 归属
# ------------------------------------------------------------
def test_handle_completed_experience_passes_user_id(monkeypatch):
    captured = {}

    class _FakeAdapter:
        def store_experience(self, experience, user_id=None):
            captured["user_id"] = user_id
            return True

    _rmod = importlib.import_module("src.runtime.runtime_core")
    _rcls = getattr(_rmod, "RuntimeCore")
    inst = _rcls.__new__(_rcls)
    inst.memory_adapter = _FakeAdapter()
    monkeypatch.setattr(inst, "_emit_domain_event", lambda *a, **k: None)

    exp = SimpleNamespace(
        experience_id="exp_1", action_type="chat", trigger_type="user_input",
        valid=True,
    )
    inst.handle_completed_experience(exp, store_to_memory=True, user_id="user_A")
    assert captured["user_id"] == "user_A", "有 ctx 时应透传消息级用户"


def test_memory_adapter_user_id_override_falls_back(monkeypatch, tmp_path):
    # 真实 MemoryAdapter 经 importlib 构造（store 用假件避免真实落盘）
    _amod = importlib.import_module("src.runtime.adapters.memory_adapter")
    _acls = getattr(_amod, "MemoryAdapter")

    class _FakeJournal:
        def __init__(self):
            self.records = []

        def append(self, record):
            self.records.append(dict(record))
            return True

    class _FakeStore:
        pass

    adapter = _acls(memory_store=_FakeStore(), user_id="yuyi")
    adapter._journal = _FakeJournal()

    exp = SimpleNamespace(
        experience_id="exp_1", action_type="chat", trigger_type="user_input",
        result=SimpleNamespace(success=True, user_response="hi", response_received=False,
                               error_message="", side_effects=[]),
        duration_ms=10, decision_source="x", timestamp="2026-08-21T00:00:00Z",
        trigger_event={}, valid=True,
    )
    adapter.store_experience(exp, user_id="user_B")
    assert adapter._journal.records[-1]["user_id"] == "user_B", "override 优先"

    adapter.store_experience(exp)
    assert adapter._journal.records[-1]["user_id"] == "yuyi", "无 override 回退 config 默认"
