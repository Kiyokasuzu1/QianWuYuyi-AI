# -*- coding: utf-8 -*-
"""P2.1.3 Permission Gate 接入测试。

通过真实 Orchestrator（LLM mock + 隔离工作目录）验证权限门在
状态修改链路上的实际行为：

    1. user（真实 QQ）    → memory / emotion / self_model / growth 全部允许
    2. sandbox（_unknown_sender）→ 全部拒绝（且聊天回复流程正常）
    3. unknown（非 QQ 任意值）   → fail-close（等同 sandbox 拒绝）
    4. 普通聊天流程不受影响（两种身份均返回正常 reply）

约定：
    - 使用临时 CWD 隔离 data/（不污染项目真实数据）
    - growth / self_model 链使用 MagicMock 精确断言"是否被触发"
    - emotion 门直接调用 _process_emotion_pre/post 入口（detector stub 化，确定性）
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# 强制 mock 模式（无 API key）
os.environ["YUYI_LLM_MOCK"] = "1"
os.environ.setdefault("DEEPSEEK_API_KEY", "")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

SANDBOX = "_unknown_sender"
USER_QQ = "366648462"


@pytest.fixture(scope="module")
def orch_env():
    """隔离 CWD 下构建真实 Orchestrator（LLM mock），返回 (orch, tmpdir)。"""
    tmp = Path(tempfile.mkdtemp(prefix="p213_perm_"))
    (tmp / "data").mkdir()
    (tmp / "data" / "memory.json").write_text("[]", encoding="utf-8")
    (tmp / "data" / "self_model.json").write_text("{}", encoding="utf-8")
    (tmp / "data" / "growth" / "proposals").mkdir(parents=True)
    (tmp / "data" / "growth" / "proposals" / "proposals.json").write_text("[]", encoding="utf-8")
    (tmp / "data" / "emotions").mkdir()
    (tmp / "data" / "audit").mkdir()

    old_cwd = os.getcwd()
    os.chdir(tmp)
    try:
        from src.orchestrator import Orchestrator

        orch = Orchestrator(config={"runtime": {"enabled": False}})
        orch.engine = MagicMock()
        orch.engine.generate.return_value = "好的"
        yield orch, tmp
    finally:
        os.chdir(old_cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def _memory_count(orch) -> int:
    try:
        return len(orch.memory_store.load())
    except Exception:
        return -1


def _stub_emotion_detector(orch):
    """替换 emotion_event_detector：任何消息都检测出一次 praise 情绪事件。"""
    from src.emotion.emotion_event import EmotionEvent

    stub = MagicMock()
    stub.detect.return_value = EmotionEvent(
        event_type="user_praise",
        intensity=0.5,
        description="测试赞许",
        source="user",
    )
    orch.emotion_event_detector = stub
    return stub


def _ctx_with_emotion(orch):
    return {"emotion_manager": orch.emotion_manager, "trace": []}


class TestUserAllMutationsAllowed:
    """真实 QQ 用户：四个门全部放行。"""

    def test_01_user_memory_write_allowed(self, orch_env):
        orch, _tmp = orch_env
        before = _memory_count(orch)
        reply = orch.process("我今天完成了一个重要的项目，感觉很有成就感", user_id=USER_QQ)
        assert isinstance(reply, str) and reply
        assert _memory_count(orch) == before + 1

    def test_02_user_emotion_pre_allowed(self, orch_env):
        orch, _tmp = orch_env
        stub = _stub_emotion_detector(orch)
        initial_valence = orch.emotion_manager.state.valence
        orch._process_emotion_pre(_ctx_with_emotion(orch), "你真棒", USER_QQ)
        assert stub.detect.called, "user 身份应触发情绪事件检测"
        assert orch.emotion_manager.state.valence != initial_valence

    def test_03_user_emotion_post_allowed(self, orch_env):
        orch, _tmp = orch_env
        uid = "55555555"
        per_user_file = Path("data") / "emotions" / f"{uid}.json"
        if per_user_file.exists():
            per_user_file.unlink()
        orch._process_emotion_post(_ctx_with_emotion(orch), "谢谢", uid)
        assert per_user_file.exists(), "user 身份应持久化情绪状态（per-user 文件）"

    def test_04_user_growth_trigger_allowed(self, orch_env):
        orch, _tmp = orch_env
        orch._growth_pipeline = MagicMock()
        orch._growth_pipeline.incremental_update.return_value = {"growth_records": []}
        orch.process("普通聊天", user_id=USER_QQ)
        orch._growth_pipeline.incremental_update.assert_called_once()

    def test_05_user_self_model_chain_allowed(self, orch_env):
        orch, _tmp = orch_env
        orch._growth_pipeline = MagicMock()
        orch._growth_pipeline.incremental_update.return_value = {"growth_records": [{"confidence": 0.9}]}
        updater = MagicMock()
        updater.create_proposal_from_growth.return_value = MagicMock()
        orch._self_model_updater = updater
        policy = MagicMock()
        policy.evaluate.return_value.action.value = "auto_apply"
        orch._governance_policy = policy
        orch._approval_queue = MagicMock()
        smo = MagicMock()
        orch.self_model_orchestrator = smo
        orch.process("一次有意义的对话", user_id=USER_QQ)
        assert updater.apply_proposal.called, "user 身份应允许 self_model 治理链应用"
        assert smo.run_after_event.called, "user 身份应允许 SelfModel 编排器运行"


class TestSandboxAllMutationsDenied:
    """沙盒身份：四个门全部拒绝。"""

    def test_06_sandbox_memory_write_denied(self, orch_env):
        orch, _tmp = orch_env
        before = _memory_count(orch)
        reply = orch.process("我今天完成了一个重要的项目，感觉很有成就感", user_id=SANDBOX)
        assert isinstance(reply, str) and reply
        assert _memory_count(orch) == before, "sandbox 不应产生新记忆"

    def test_07_sandbox_emotion_pre_denied(self, orch_env):
        orch, _tmp = orch_env
        stub = _stub_emotion_detector(orch)
        initial_valence = orch.emotion_manager.state.valence
        result = orch._process_emotion_pre(_ctx_with_emotion(orch), "你真棒", SANDBOX)
        assert not stub.detect.called, "sandbox 不应触发情绪事件检测"
        assert orch.emotion_manager.state.valence == initial_valence, "sandbox 不应改变情绪状态"
        assert result is not None

    def test_08_sandbox_emotion_post_denied(self, orch_env):
        orch, _tmp = orch_env
        per_user_file = Path("data") / "emotions" / f"{SANDBOX}.json"
        if per_user_file.exists():
            per_user_file.unlink()
        orch._process_emotion_post(_ctx_with_emotion(orch), "谢谢", SANDBOX)
        assert not per_user_file.exists(), "sandbox 不应持久化情绪状态"

    def test_09_sandbox_growth_trigger_denied(self, orch_env):
        orch, _tmp = orch_env
        orch._growth_pipeline = MagicMock()
        orch._growth_pipeline.incremental_update.return_value = {"growth_records": []}
        orch.process("普通聊天", user_id=SANDBOX)
        assert not orch._growth_pipeline.incremental_update.called, "sandbox 不应触发 GrowthPipeline"

    def test_10_sandbox_self_model_denied(self, orch_env):
        orch, _tmp = orch_env
        orch._growth_pipeline = MagicMock()
        orch._growth_pipeline.incremental_update.return_value = {"growth_records": [{"confidence": 0.9}]}
        updater = MagicMock()
        updater.create_proposal_from_growth.return_value = MagicMock()
        orch._self_model_updater = updater
        policy = MagicMock()
        policy.evaluate.return_value.action.value = "auto_apply"
        orch._governance_policy = policy
        orch._approval_queue = MagicMock()
        smo = MagicMock()
        orch.self_model_orchestrator = smo
        orch.process("一次有意义的对话", user_id=SANDBOX)
        assert not updater.apply_proposal.called, "sandbox 不应修改 self_model"
        assert not smo.run_after_event.called, "sandbox 不应触发 SelfModel 编排器"


class TestUnknownIdentityFailClose:
    """未知身份：fail-close（与 sandbox 行为一致）。"""

    def test_11_unknown_id_memory_denied(self, orch_env):
        orch, _tmp = orch_env
        before = _memory_count(orch)
        orch.process("随便聊点什么", user_id="guest_12345")
        assert _memory_count(orch) == before

    def test_12_unknown_id_growth_denied(self, orch_env):
        orch, _tmp = orch_env
        orch._growth_pipeline = MagicMock()
        orch._growth_pipeline.incremental_update.return_value = {"growth_records": []}
        orch.process("普通聊天", user_id="anonymous_visitor")
        assert not orch._growth_pipeline.incremental_update.called


class TestChatFlowUnaffected:
    """普通聊天回复流程不受权限门影响。"""

    def test_13_sandbox_reply_normal(self, orch_env):
        orch, _tmp = orch_env
        reply = orch.process("你好羽依", user_id=SANDBOX)
        assert isinstance(reply, str) and reply == "好的"

    def test_14_user_reply_normal(self, orch_env):
        orch, _tmp = orch_env
        reply = orch.process("你好羽依", user_id=USER_QQ)
        assert isinstance(reply, str) and reply == "好的"
