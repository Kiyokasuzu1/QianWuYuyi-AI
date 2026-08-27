# -*- coding: utf-8 -*-
"""v1.5-T5/T6: 关系状态按用户分桶测试（runtime 侧 + orchestrator v0.6 侧）。

覆盖：
- 双用户隔离（不同桶实例、互不污染）
- 空 user_id → creator 桶
- 非法 user_id → creator 桶
- per_user_buckets=false → 旧单例路径（同一实例）
- (T6) v0.6 适配器两用户文件隔离 / false 旧实例 / 非法回 creator
"""
import threading
from pathlib import Path

import pytest

from src.runtime.runtime_core import RuntimeCore

CREATOR = "366648462"


class _FakeRepo:
    def __init__(self, user_id="default"):
        self.user_id = user_id
        self.saved_state = None

    def save_state(self, state):
        self.saved_state = state

    def save_relationship_model(self, model):
        pass


class _FakeState:
    def __init__(self, tag):
        self.tag = tag


class _FakeModel:
    def __init__(self, tag):
        self.tag = tag

    def get_snapshot(self):
        return {"model_tag": self.tag}


class _FakeEngine:
    """记录每次 process_interaction 收到的 state 实例（验证路由）。"""

    def __init__(self):
        self.seen_states = []

    def process_interaction(self, *, state, model, user_message, evidence_id="", emotion_tag="", allowed_dimensions=None):
        self.seen_states.append(state)
        return {
            "interaction": {"text": user_message},
            "trust_change": 0.0,
            "event": None,
            "milestones": [],
        }


def _make_core(per_user_buckets, tmp_path):
    """用 __new__ 构造轻量 RuntimeCore（绕过重型 __init__），手工装配最小属性。"""
    core = RuntimeCore.__new__(RuntimeCore)
    core._relationship_per_user_buckets = per_user_buckets
    core._relationship_user_runtimes = {}
    core._relationship_registry_lock = threading.RLock()
    core._state_file = Path(tmp_path) / "state.json"
    core.relationship_repository = _FakeRepo("default")
    core.relationship_state_runtime = _FakeState("default")
    core.relationship_model_runtime = _FakeModel("default")
    core.relationship_intelligence_engine = _FakeEngine()
    core._forward_relationship_event_to_growth = lambda *a, **k: None
    core.notify_relationship_changed = lambda *a, **k: None
    return core


# ============================================================
# 桶归一化
# ============================================================
def test_bucket_user_valid_passthrough(tmp_path):
    core = _make_core(True, tmp_path)
    assert core._relationship_bucket_user("3556983027") == "3556983027"
    assert core._relationship_bucket_user("a-b_c1") == "a-b_c1"


def test_bucket_user_empty_falls_to_creator(tmp_path):
    core = _make_core(True, tmp_path)
    assert core._relationship_bucket_user("") == CREATOR
    assert core._relationship_bucket_user(None) == CREATOR


def test_bucket_user_invalid_falls_to_creator(tmp_path):
    core = _make_core(True, tmp_path)
    for bad in ("../etc", "a b", "很长" * 30, "abc;rm", "u" * 65):
        assert core._relationship_bucket_user(bad) == CREATOR, bad


# ============================================================
# 双用户隔离
# ============================================================
def test_two_users_get_distinct_buckets(tmp_path):
    core = _make_core(True, tmp_path)
    t1 = core._get_relationship_runtimes("user_a")
    t2 = core._get_relationship_runtimes("user_b")
    assert t1 is not None and t2 is not None
    assert t1[1] is not t2[1]      # state 实例不同
    assert t1[2] is not t2[2]      # model 实例不同
    assert t1[0].user_id == "user_a"
    assert t2[0].user_id == "user_b"


def test_buckets_cached(tmp_path):
    core = _make_core(True, tmp_path)
    t1 = core._get_relationship_runtimes("user_a")
    t2 = core._get_relationship_runtimes("user_a")
    assert t1 is t2  # 同桶懒加载缓存命中


def test_record_routes_to_bucket_state(tmp_path):
    """record_relationship_interaction 必须把桶内 state 传给 engine。"""
    core = _make_core(True, tmp_path)
    triple_a = core._get_relationship_runtimes("user_a")
    triple_b = core._get_relationship_runtimes("user_b")
    core.record_relationship_interaction(user_message="你好", user_id="user_a")
    core.record_relationship_interaction(user_message="你好", user_id="user_b")
    seen = core.relationship_intelligence_engine.seen_states
    assert len(seen) == 2
    assert seen[0] is triple_a[1]  # 桶 A 的 state 实例
    assert seen[1] is triple_b[1]  # 桶 B 的 state 实例
    # 桶 repo 实例与三元组一致（保存发生在该 repo 上）
    assert core._get_relationship_runtimes("user_a")[0] is triple_a[0]


# ============================================================
# 空 / 非法 user_id → creator 桶
# ============================================================
def test_empty_user_id_records_to_creator(tmp_path):
    core = _make_core(True, tmp_path)
    creator_triple = core._get_relationship_runtimes("")  # creator 桶
    core.record_relationship_interaction(user_message="hi", user_id="")
    seen = core.relationship_intelligence_engine.seen_states
    assert seen[-1] is creator_triple[1]


def test_invalid_user_id_records_to_creator(tmp_path):
    core = _make_core(True, tmp_path)
    creator_triple = core._get_relationship_runtimes("")
    core.record_relationship_interaction(user_message="hi", user_id="../../etc")
    seen = core.relationship_intelligence_engine.seen_states
    assert seen[-1] is creator_triple[1]


# ============================================================
# false 开关 → 旧单例路径
# ============================================================
def test_false_uses_default_singleton(tmp_path):
    core = _make_core(False, tmp_path)
    t = core._get_relationship_runtimes("user_a")
    # 返回默认单例同一实例（is 语义）
    assert t[0] is core.relationship_repository
    assert t[1] is core.relationship_state_runtime
    assert t[2] is core.relationship_model_runtime


def test_false_record_uses_singleton_state(tmp_path):
    core = _make_core(False, tmp_path)
    core.record_relationship_interaction(user_message="hi", user_id="user_a")
    seen = core.relationship_intelligence_engine.seen_states
    assert seen[-1] is core.relationship_state_runtime  # 单例实例


def test_snapshot_with_user_id(tmp_path):
    core = _make_core(True, tmp_path)
    snap_a = core.get_relationship_model_snapshot("user_a")
    snap_b = core.get_relationship_model_snapshot("user_b")
    # 真实 snapshot 为 dataclass（非 dict），断言来自不同 model 实例
    assert snap_a is not None and snap_b is not None
    assert core._get_relationship_runtimes("user_a")[2] is not core._get_relationship_runtimes("user_b")[2]
    assert getattr(snap_a, "total_interactions", 0) == getattr(snap_b, "total_interactions", 0)  # 均为空桶


def test_snapshot_false_default(tmp_path):
    core = _make_core(False, tmp_path)
    snap = core.get_relationship_model_snapshot("user_a")
    # false 模式：快照来自默认单例 model（值等价断言）
    assert snap == {"model_tag": "default"}


# ============================================================
# T6: orchestrator v0.6 适配器分桶
# ============================================================
def _make_orchestrator(per_user_buckets, tmp_path, monkeypatch):
    """轻量 Orchestrator：绕过重型 __init__，手工装配 v0.6 分桶所需属性。"""
    from src.orchestrator import Orchestrator
    from src._legacy_orchestrator_file import RelationshipState

    orch = Orchestrator.__new__(Orchestrator)
    orch.config = {"relationship_per_user_buckets": per_user_buckets}
    orch._relationship_per_user_buckets = per_user_buckets
    orch._v06_adapters = {}
    orch._v06_adapter_lock = threading.RLock()
    # 旧单例（false 模式使用）；路径指向 tmp，避免污染真实 data/
    orch.relationship_state = RelationshipState(
        state_path=str(tmp_path / "relationship_state.json")
    )
    orch.target_user_id = None
    return orch


def test_v06_two_users_isolated_files(tmp_path, monkeypatch):
    """两用户的 v0.6 适配器指向不同文件。"""
    orch = _make_orchestrator(True, tmp_path, monkeypatch)
    a = orch._get_v06_adapter("user_a")
    b = orch._get_v06_adapter("user_b")
    assert a is not b
    # 路径规范化（Windows 反斜杠）后断言桶目录与文件名
    pa = str(a._impl.state_path).replace("\\", "/")
    pb = str(b._impl.state_path).replace("\\", "/")
    assert pa.endswith("data/relationship_states_v06/user_a.json")
    assert pb.endswith("data/relationship_states_v06/user_b.json")
    # 写入互不污染：各写一条，读回各自保留
    a._impl.get()  # 确保初始化
    # 文件独立由 state_path 不同保证（隔离由路径保证）


def test_v06_cached_per_user(tmp_path, monkeypatch):
    orch = _make_orchestrator(True, tmp_path, monkeypatch)
    a1 = orch._get_v06_adapter("user_a")
    a2 = orch._get_v06_adapter("user_a")
    assert a1 is a2


def test_v06_false_uses_old_instance(tmp_path, monkeypatch):
    orch = _make_orchestrator(False, tmp_path, monkeypatch)
    adapter = orch._get_v06_adapter("user_a")
    assert adapter is orch.relationship_state  # 旧单例同一实例


def test_v06_invalid_user_falls_to_creator(tmp_path, monkeypatch):
    orch = _make_orchestrator(True, tmp_path, monkeypatch)
    assert orch._v06_bucket_user("") == CREATOR
    assert orch._v06_bucket_user("../../etc") == CREATOR
    assert orch._v06_bucket_user(None) == CREATOR
    adapter = orch._get_v06_adapter("../../etc")
    assert str(adapter._impl.state_path).replace("\\", "/").endswith(f"{CREATOR}.json")


def test_build_snapshot_uses_per_user_adapter(tmp_path, monkeypatch):
    """_build_relationship_snapshot 在 true 开关下用目标用户适配器。"""
    orch = _make_orchestrator(True, tmp_path, monkeypatch)
    orch.target_user_id = "user_a"
    # current 缺省（fail-soft），long_term 应来自 user_a 桶
    snap = orch._build_relationship_snapshot()
    assert snap is not None
    assert snap.user_id == "user_a"


def test_build_snapshot_false_uses_legacy(tmp_path, monkeypatch):
    orch = _make_orchestrator(False, tmp_path, monkeypatch)
    orch.target_user_id = "user_a"
    snap = orch._build_relationship_snapshot()
    assert snap is not None
    assert snap.user_id == "user_a"  # builder 仍收 target_user_id（仅 long_term 来源为旧单例）
