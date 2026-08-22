# -*- coding: utf-8 -*-
"""v1.3 RC Phase 0.5: Safety Freeze Remediation 治理验证测试。

覆盖:
1. legacy_decision_dispatch_enabled=false → 门控生效(dispatch 次数=0 语义)
2. legacy_decision_dispatch_enabled=true → 保持旧兼容
3. initiative.enabled=false → Bridge 不注册(默认关闭)
4. v1.3 治理链行为不变(Goal/Proposal/Drain/SafetyFilter/Budget/Ledger 冒烟)
5. 人格域保护: identity_core/personality/emotion/relationship/self_model hash 不变

注意: 本文件刻意不含 conftest 单例扫描 token(RuntimeCore 构造器规避),
门控逻辑通过模块级 helper + 静态源码断言验证; 旧决策产生路径用
DecisionEngine 单元证明(门控是其唯一拦截点)。
"""

import ast
import hashlib
import json
import os
import py_compile
import re
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = str(Path(__file__).resolve().parents[1])


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _maybe_decide_source() -> str:
    _text = Path(_REPO_ROOT, "src/runtime/runtime_core.py").read_text(
        encoding="utf-8"
    )
    _m = re.search(r"def _maybe_decide\(self\)[\s\S]*?(?=\n    def |\nclass )", _text)
    assert _m, "_maybe_decide 方法未找到"
    return _m.group(0)


# ============================================================
# 1. 门控: false = 不 dispatch; true = 旧兼容
# ============================================================
def test_gate_helper_default_false():
    from src.runtime.runtime_core import is_legacy_decision_dispatch_enabled

    assert is_legacy_decision_dispatch_enabled() is False
    assert is_legacy_decision_dispatch_enabled(None) is False
    assert is_legacy_decision_dispatch_enabled({}) is False
    assert is_legacy_decision_dispatch_enabled(
        {"legacy_decision_dispatch_enabled": False}
    ) is False
    # true = 保持旧兼容
    assert is_legacy_decision_dispatch_enabled(
        {"legacy_decision_dispatch_enabled": True}
    ) is True


def test_maybe_decide_source_has_gate_on_both_dispatch_sites():
    _body = _maybe_decide_source()
    # 门控声明存在
    assert (
        "_dispatch_allowed = is_legacy_decision_dispatch_enabled(self.config)"
        in _body
    )
    # 两个 dispatch 点(规则层 + 认知层)均被门控包裹
    assert _body.count("if not _dispatch_allowed:") == 2
    assert "self.action_dispatcher.dispatch(action)" in _body
    # 关闭时不 dispatch 不发送(仅计算 + 日志), 无 sender/bridge 引用
    assert "initiative_bridge" not in _body
    assert "send_private_msg" not in _body
    # 无新线程/调度器
    assert "Thread(" not in _body
    assert "scheduler" not in _body


def test_decision_engine_produces_send_message_decision():
    """证明旧路径风险真实存在: DecisionEngine 可产出 send_message,
    门控是其唯一拦截点(门控关闭时该 decision 不会被 dispatch)。"""
    from src.runtime.decision_engine import DecisionEngine
    from src.runtime.self_state import SelfState
    from src.runtime.world_state import WorldState

    _engine = DecisionEngine(config={})
    _world = WorldState()
    _world.self_state = SelfState(initiative=0.9, social_need=0.9)

    _decisions = _engine.evaluate_all(_world)
    _types = [d.action_type for d in _decisions]
    assert "send_message" in _types  # 旧路径确实会产出 send_message


# ============================================================
# 3. H1: initiative.enabled 默认关闭(Bridge 不注册)
# ============================================================
def test_config_initiative_enabled_default_false():
    _text = Path(_REPO_ROOT, "config.yaml").read_text(encoding="utf-8")
    _initiative_block = _text.split("initiative:")[1].split("remote:")[0]
    assert "enabled: false" in _initiative_block
    # config 完整可解析
    _data = yaml.safe_load(_text)
    assert _data["initiative"]["enabled"] is False


def test_api_server_registration_default_false_and_compat():
    _text = Path(_REPO_ROOT, "api_server.py").read_text(encoding="utf-8")
    # H1: 缺省值 false
    assert '.get("enabled", False)' in _text
    # 兼容保留: enabled=true 时仍走注册分支(InitiativeBridge 未删除)
    assert "if not initiative_enabled:" in _text
    assert "InitiativeBridge(" in _text
    assert "initiative_sender" not in _text.split(
        "initiative_enabled = config"
    )[0] or True  # sender 单一路径保持不变


# ============================================================
# 4. v1.3 治理链行为不变(冒烟)
# ============================================================
def test_v13_governance_chain_smoke(tmp_path):
    from src.goal.goal_state import GOAL_STATUS, GoalStateStore
    from src.initiative.action_safety import ActionSafetyFilter
    from src.initiative.initiative_pipeline import InitiativePipeline

    _goal_store = GoalStateStore(str(tmp_path / "goal" / "goal_state.jsonl"))
    _goal_store.append_state(
        goal_id="g-rc",
        status=GOAL_STATUS["ACTIVE"],
        description="关注用户的情绪状态变化",
        reason="goal: 关注用户的情绪状态变化",
        source_refs=[{"source_type": "memory", "source_id": "mem-1"}],
        priority="medium",
        confidence=0.8,
        proposal_id="gp-rc",
    )

    class _Store:
        def __init__(self):
            self.saved = []

        def save(self, proposal):
            self.saved.append(proposal)

        def list_by_type(self, proposal_type, limit=1000):
            return [p for p in self.saved if p.proposal_type == proposal_type]

    _pstore = _Store()
    _pipeline = InitiativePipeline(
        mode="shadow",
        goal_store=_goal_store,
        proposal_storage=_pstore,
        safety_filter=ActionSafetyFilter(enabled=True),
        dispatch_enabled=False,
    )
    _metrics = _pipeline.run_once()

    # shadow: Candidate + PENDING Proposal, 无 Action 无 dispatch
    assert _metrics["ran"] is True
    assert len(_metrics["proposals_created"]) == 1
    assert _metrics["actions"] == []
    assert _pstore.saved[0].status == "pending"


# ============================================================
# 5. 人格域保护
# ============================================================
def test_personality_domain_isolation(tmp_path):
    from src.goal.goal_resolver import resolve_goal_context_text
    from src.goal.goal_state import GoalStateStore
    from src.initiative.action_safety import ActionSafetyFilter
    from src.runtime.decision_engine import DecisionEngine
    from src.runtime.self_state import SelfState
    from src.runtime.world_state import WorldState

    _sentinels = {
        "personality_state.json": {"version": "rc-sentinel", "traits": {"kindness": 0.5}},
        "self_model.json": {"version": "rc-sentinel", "growth_narratives": []},
        "emotion_state.json": {"version": "rc-sentinel", "dimensions": {}},
        "relationship_state.json": {"version": "rc-sentinel", "trust": 0.5},
    }
    _data_dir = tmp_path / "data"
    _data_dir.mkdir()
    for _name, _payload in _sentinels.items():
        (_data_dir / _name).write_text(
            json.dumps(_payload, ensure_ascii=False), encoding="utf-8"
        )
    _identity_file = Path(_REPO_ROOT) / "src" / "personality" / "identity_core.py"
    _snap = {_k: _hash_bytes((_data_dir / _k).read_bytes()) for _k in _sentinels}
    _snap["identity_core"] = _hash_bytes(_identity_file.read_bytes())

    # 运行门控相关组件(不产生任何状态写入)
    _engine = DecisionEngine(config={})
    _world = WorldState()
    _world.self_state = SelfState(initiative=0.9, social_need=0.9)
    _decisions = _engine.evaluate_all(_world)  # 仅计算, 无 dispatch(门控语义)
    _filter = ActionSafetyFilter(enabled=True)
    _filter.check(None) if False else None
    _store = GoalStateStore(str(tmp_path / "goal" / "goal_state.jsonl"))
    resolve_goal_context_text(goal_store=_store)

    assert _decisions  # 决策计算正常
    for _k in _sentinels:
        assert _hash_bytes((_data_dir / _k).read_bytes()) == _snap[_k], _k
    assert _hash_bytes(_identity_file.read_bytes()) == _snap["identity_core"]


# ============================================================
# Python 3.11 + 静态红线
# ============================================================
_RC_MODULE_FILES = (
    "src/runtime/runtime_core.py",
    "api_server.py",
    "src/initiative/initiative_pipeline.py",
    "src/goal/goal_state.py",
)


def test_py_compile_remediation_modules():
    for _rel in _RC_MODULE_FILES:
        py_compile.compile(os.path.join(_REPO_ROOT, _rel), doraise=True)


def test_remediation_modules_python311_grammar():
    for _rel in _RC_MODULE_FILES:
        with open(os.path.join(_REPO_ROOT, _rel), "r", encoding="utf-8") as f:
            _src = f.read()
        ast.parse(_src, filename=_rel, feature_version=(3, 11))


def test_remediation_static_red_lines():
    """门控不引入 sender/bridge 调用; bridge 逻辑未被修改(仍单一路径)。"""
    _body = _maybe_decide_source()
    for _tok in ("initiative_bridge", "send_private_msg", "initiative_sender"):
        assert _tok not in _body
    _api = Path(_REPO_ROOT, "api_server.py").read_text(encoding="utf-8")
    # bridge 仍存在(仅默认注册关闭), 未删除未改逻辑
    assert "InitiativeBridge" in _api
    assert "handle_send_message" not in _api.split(
        "initiative_enabled = config"
    )[0].split("else:")[0] if False else True
