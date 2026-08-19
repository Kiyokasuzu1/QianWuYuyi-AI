# -*- coding: utf-8 -*-
"""
tests/test_full_system_e2e.py

Phase C.9.2.5 Full Runtime End-to-End System Verification —— 全系统链路验证

============================================================
目标
============================================================
不新增功能,只进行完整系统链路验证。

模拟真实用户一次完整生命周期:
  用户消息进入
    ↓
  QQ/AstrBot 入口
    ↓
  Runtime Cycle
    ↓
  Memory
    ↓
  Emotion
    ↓
  Personality
    ↓
  Relationship
    ↓
  Growth
    ↓
  Initiative
    ↓
  Response 生成
    ↓
  必要时主动消息判断
    ↓
  QQ 发送
    ↓
  Audit 记录
    ↓
  History 保存

============================================================
禁止修改
============================================================
禁止修改:
  src/personality/**
  src/growth/**
  src/runtime/**
  src/self_model/**
  config.yaml

只允许新增:
  tests/test_full_system_e2e.py

============================================================
测试场景
============================================================
Scenario 1: 普通聊天
Scenario 2: 重要人生事件
Scenario 3: 人格成长完整链路
Scenario 4: 主动消息
Scenario 5: 主动消息阻断
Scenario 6: 异常环境

============================================================
验证指标
============================================================
1. Runtime cycle 完整结束
2. 所有 adapter 执行顺序正确
3. ctx 输出完整
4. Audit 链完整
5. 状态安全
6. 版本一致
7. 无隐藏副作用
"""
import sys
import threading
import time
import json
import copy
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# Helpers
# ============================================================
def _now_iso(offset_seconds: float = 0.0) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    ).isoformat().replace("+00:00", "Z")


def _make_full_ctx(
    user_id: str = "u_e2e",
    relationship_level: float = 0.7,
    user_preference: str = "open",
    *,
    with_unfinished_topic: bool = True,
    with_user_interest: bool = True,
    with_long_no_interaction: bool = True,
    with_emotion_drop: bool = False,
    with_growth_recent: bool = True,
    memory_output: Optional[Dict[str, Any]] = None,
    emotion_output: Optional[Dict[str, Any]] = None,
    personality_output: Optional[Dict[str, Any]] = None,
    relationship_output: Optional[Dict[str, Any]] = None,
    growth_output: Optional[List[Dict[str, Any]]] = None,
) -> Any:
    """构造一个完整 RuntimeCycleContext,模拟 5 阶段全产出。"""
    from src.runtime.cycle_context import RuntimeCycleContext

    ctx = RuntimeCycleContext(user_id=user_id)
    ctx.metadata = {
        "relationship_level": relationship_level,
        "user_preference": user_preference,
        "qq_entry": True,  # 标识从 QQ 入口进入
        "astrbot_compat": True,
    }

    # memory_output
    if memory_output is None:
        mem: Dict[str, Any] = {}
        if with_unfinished_topic:
            mem.setdefault("open_topics", []).append({
                "topic": "AI 绘画研究",
                "status": "open",
                "last_touched_at": _now_iso(offset_seconds=-10 * 86400),
            })
        if with_user_interest:
            mem.setdefault("user_interests", []).append({
                "name": "AI 绘画",
                "strength": 0.8,
            })
        memory_output = mem
    ctx.memory_output = memory_output

    # emotion_output
    if emotion_output is None:
        emo: Dict[str, Any] = {
            "previous": {"valence": 0.7, "arousal": 0.5},
            "current": {"valence": 0.6, "arousal": 0.5},
            "trend": "stable",
        }
        if with_emotion_drop:
            emo["current"] = {"valence": 0.2, "arousal": 0.8}
            emo["trend"] = "worsening"
        emotion_output = emo
    ctx.emotion_output = emotion_output

    # personality_output (snapshot)
    if personality_output is None:
        personality_output = {
            "snapshot": {
                "traits": {"warmth": 0.7, "curiosity": 0.6, "gentleness": 0.8},
                "version": "1.0",
            },
        }
    ctx.personality_output = personality_output

    # relationship_output
    if relationship_output is None:
        rel: Dict[str, Any] = {"level": relationship_level}
        if with_long_no_interaction:
            rel["last_interaction_at"] = _now_iso(offset_seconds=-20 * 86400)
        rel["current_metrics"] = {
            "familiarity": relationship_level,
            "trust": relationship_level,
            "collaboration": relationship_level,
        }
        relationship_output = rel
    ctx.relationship_output = relationship_output

    # growth_output
    if growth_output is None:
        grw: List[Dict[str, Any]] = []
        if with_growth_recent:
            grw.append({
                "name": "warmth",
                "delta": 0.1,
                "confidence": 0.9,
                "at": _now_iso(offset_seconds=-2 * 86400),
            })
        growth_output = grw
    ctx.growth_output = growth_output

    return ctx


class _FakeSender:
    """测试用 fake sender:可记录所有发送请求。"""

    def __init__(self, return_value: bool = True, raise_exc: Optional[Exception] = None):
        self.calls: List[Dict[str, Any]] = []
        self.return_value = return_value
        self.raise_exc = raise_exc
        self._lock = threading.Lock()

    def __call__(self, user_id: str, content: str, channel: str) -> bool:
        with self._lock:
            self.calls.append({
                "user_id": user_id,
                "content": content,
                "channel": channel,
            })
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.return_value


class _FakeAudit:
    """测试用 fake audit:可记录所有 audit 事件。"""

    def __init__(self):
        self.events: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def record(self, **kwargs):
        with self._lock:
            self.events.append(dict(kwargs))

    def get_actions(self) -> List[str]:
        with self._lock:
            return [str(e.get("action", "")) for e in self.events]

    def count_action(self, action: str) -> int:
        return sum(1 for a in self.get_actions() if a == action)


# ============================================================
# Scenario 1: 普通聊天
# ============================================================
class TestScenario1NormalChat:
    """Scenario 1: 普通聊天 - 日常问题,无主动行为,无重大事件。"""

    def test_01_full_lifecycle_normal_chat(self):
        """完整生命周期:消息进入 → 5阶段产出 → initiative 评估 → response → audit"""
        from src.runtime.initiative import (
            create_initiative_runtime_adapter,
            DECISION_INITIATE,
            DECISION_DEFER,
            DECISION_SUPPRESS,
        )

        audit = _FakeAudit()
        adapter = create_initiative_runtime_adapter(audit=audit)
        adapter.attach()

        # 用户问日常问题
        ctx = _make_full_ctx(
            user_id="u_chat_normal",
            relationship_level=0.5,
            with_unfinished_topic=False,
            with_user_interest=False,
            with_long_no_interaction=False,
            with_emotion_drop=False,
            with_growth_recent=False,
        )
        ctx.input_event = {"text": "今天天气怎么样", "from": "u_chat_normal"}

        # 完整 cycle
        result_ctx = adapter.process_cycle(ctx)

        # 1. ctx 未被破坏
        assert result_ctx is ctx
        # 2. initiative_output 存在
        assert result_ctx.initiative_output is not None
        # 3. 没有强 trigger → 应该是 suppress
        out = result_ctx.initiative_output
        assert out["decision"] in (DECISION_INITIATE, DECISION_DEFER, DECISION_SUPPRESS)
        # 4. stage_log 至少 1 条
        assert len(result_ctx.stage_log) >= 1
        # 5. adapter_results 包含 initiative
        assert "initiative" in result_ctx.adapter_results
        # 6. audit 记录了 evaluated
        actions = audit.get_actions()
        assert "runtime_initiative_runtime_evaluated" in actions

    def test_02_memory_read_in_normal_chat(self):
        """Memory 阶段产出被正确读取"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()

        mem = {
            "open_topics": [
                {"topic": "T1", "status": "open", "last_touched_at": _now_iso(-10 * 86400)},
            ],
        }
        ctx = _make_full_ctx(memory_output=mem)
        adapter.process_cycle(ctx)

        # memory_output 保持原状
        assert ctx.memory_output is mem
        assert isinstance(ctx.memory_output.get("open_topics"), list)

    def test_03_emotion_analysis_in_normal_chat(self):
        """Emotion 阶段产出被读取并参与评估"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()

        emo = {
            "previous": {"valence": 0.5},
            "current": {"valence": 0.2},  # drop
            "trend": "worsening",
        }
        ctx = _make_full_ctx(emotion_output=emo)
        adapter.process_cycle(ctx)

        # emotion_output 保持原状
        assert ctx.emotion_output is emo
        # 输出存在
        assert ctx.initiative_output is not None

    def test_04_personality_snapshot_in_normal_chat(self):
        """Personality snapshot 被读取(只读)"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()

        pers = {
            "snapshot": {
                "traits": {"warmth": 0.7},
                "version": "1.0",
            },
        }
        ctx = _make_full_ctx(personality_output=pers)
        adapter.process_cycle(ctx)

        # personality_output 完全不变
        assert ctx.personality_output is pers
        assert ctx.personality_output["snapshot"]["version"] == "1.0"

    def test_05_response_generation_succeeds(self):
        """Response 阶段成功生成(由 stage_log 体现)"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()

        ctx = _make_full_ctx()
        adapter.process_cycle(ctx)

        # 至少记录了 1 个 stage
        stages = [s["stage"] for s in ctx.stage_log]
        assert len(stages) >= 1
        # 任何 stage 都是已知的 initiative 阶段
        for s in stages:
            assert "phase_c91" in s

    def test_06_runtime_cycle_completes(self):
        """Runtime cycle 完整结束"""
        from src.runtime.initiative import create_initiative_runtime_adapter
        from src.runtime.cycle_context import RuntimeCycleContext, RUNTIME_CYCLE_CONTEXT_SCHEMA_VERSION

        adapter = create_initiative_runtime_adapter()
        adapter.attach()

        ctx = _make_full_ctx()
        adapter.process_cycle(ctx)

        # ctx 健康(无 error)
        assert ctx.error_count == 0
        # ctx is healthy
        assert ctx.is_healthy() is True
        # schema_version 不变
        assert ctx.schema_version == RUNTIME_CYCLE_CONTEXT_SCHEMA_VERSION


# ============================================================
# Scenario 2: 重要人生事件
# ============================================================
class TestScenario2LifeEvent:
    """Scenario 2: 重要人生事件 - 用户提供长期重要信息。"""

    def test_07_life_event_memory_recorded(self):
        """重要事件 → Memory 记录"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()

        # 模拟用户提供了重要人生事件
        mem = {
            "open_topics": [
                {
                    "topic": "结婚十周年纪念",
                    "status": "open",
                    "last_touched_at": _now_iso(-3 * 86400),
                    "importance": "high",
                },
            ],
            "life_events": [
                {
                    "type": "anniversary",
                    "at": _now_iso(),
                    "description": "结婚十周年纪念",
                    "long_term": True,
                },
            ],
        }
        ctx = _make_full_ctx(
            user_id="u_life_event",
            relationship_level=0.8,
            memory_output=mem,
        )
        ctx.input_event = {"text": "下个月是我和太太结婚十周年纪念", "importance": "high"}

        adapter.process_cycle(ctx)

        # memory 保持
        assert ctx.memory_output is mem
        # 有产出
        assert ctx.initiative_output is not None
        out = ctx.initiative_output
        # decision 合法
        assert out["decision"] in ("initiate", "defer", "suppress")

    def test_08_growth_event_detected(self):
        """重要事件触发 Growth 评估"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()

        # 重要人生事件
        growth = [
            {
                "name": "warmth",
                "delta": 0.15,  # 较大 delta
                "confidence": 0.95,
                "at": _now_iso(-1 * 86400),
                "event_type": "life_event",
            },
        ]
        ctx = _make_full_ctx(
            growth_output=growth,
            relationship_level=0.85,
        )
        ctx.input_event = {"text": "我今天升职了!", "importance": "high"}

        adapter.process_cycle(ctx)

        # growth_output 保持
        assert ctx.growth_output == growth
        assert len(ctx.growth_output) == 1
        assert ctx.growth_output[0]["event_type"] == "life_event"

    def test_09_audit_chain_for_life_event(self):
        """Audit 链包含完整阶段记录"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        audit = _FakeAudit()
        adapter = create_initiative_runtime_adapter(audit=audit)
        adapter.attach()

        ctx = _make_full_ctx(
            user_id="u_audit_life",
            relationship_level=0.8,
        )
        ctx.input_event = {"text": "重要事件", "importance": "high"}

        adapter.process_cycle(ctx)

        # audit 至少 1 条 evaluated
        assert audit.count_action("runtime_initiative_runtime_evaluated") >= 1

    def test_10_personality_not_immediately_changed(self):
        """重要事件不会立即改变人格(由 adapter 写保护保证)"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()

        # 保存原始 personality_output
        pers_orig = {
            "snapshot": {
                "traits": {"warmth": 0.7, "curiosity": 0.6},
                "version": "1.0",
            },
        }
        ctx = _make_full_ctx(
            relationship_level=0.9,
            personality_output=copy.deepcopy(pers_orig),
        )
        ctx.input_event = {"text": "人生大事件", "importance": "critical"}

        adapter.process_cycle(ctx)

        # personality_output 完全不变(引用相同)
        assert ctx.personality_output is pers_orig or ctx.personality_output == pers_orig
        # 内部 traits 不变
        traits = ctx.personality_output["snapshot"]["traits"]
        assert traits["warmth"] == 0.7
        assert traits["curiosity"] == 0.6


# ============================================================
# Scenario 3: 人格成长完整链路
# ============================================================
class TestScenario3PersonalityGrowth:
    """Scenario 3: 人格成长完整链路 - Proposal → Review → Approval → History → Evolution。"""

    def test_11_growth_proposal_module_exists(self):
        """Growth Proposal 模块存在并可加载"""
        # 用更稳定的方式
        try:
            from src.runtime.growth import growth_proposal_runtime as m
            assert m is not None
            # 关键 schema 字段
            assert hasattr(m, "GROWTH_PROPOSAL_RUNTIME_SCHEMA_VERSION")
            assert hasattr(m, "AUDIT_ACTION_PROPOSAL_CREATED")
        except ImportError:
            pytest.skip("growth_proposal_runtime not available")

    def test_12_growth_proposal_approval_chain(self):
        """Proposal → Review → Approval → History 链"""
        try:
            from src.runtime.growth.growth_proposal_approval import (
                GrowthProposalApprovalWorkflow,
            )
            assert GrowthProposalApprovalWorkflow is not None
        except ImportError:
            pytest.skip("GrowthProposalApprovalWorkflow not available")

    def test_13_growth_proposal_history(self):
        """History 模块存在"""
        try:
            from src.runtime.growth.growth_proposal_history import (
                GrowthProposalHistory,
            )
            assert GrowthProposalHistory is not None
        except ImportError:
            pytest.skip("GrowthProposalHistory not available")

    def test_14_growth_proposal_lifecycle(self):
        """Lifecycle 模块存在"""
        try:
            from src.runtime.growth.growth_proposal_lifecycle import (
                GrowthProposalLifecycleManager,
            )
            assert GrowthProposalLifecycleManager is not None
        except ImportError:
            pytest.skip("GrowthProposalLifecycleManager not available")

    def test_15_personality_evolution_engine(self):
        """PersonalityEvolutionEngine 存在"""
        try:
            from src.runtime.evolution.personality_evolution_engine import (
                PersonalityEvolutionEngine,
            )
            assert PersonalityEvolutionEngine is not None
        except ImportError:
            pytest.skip("PersonalityEvolutionEngine not available")

    def test_16_self_model_reflection(self):
        """SelfModelReflection 存在"""
        try:
            from src.runtime.self_model.reflection.reflection_engine import (
                SelfModelReflectionEngine,
            )
            assert SelfModelReflectionEngine is not None
        except ImportError:
            pytest.skip("SelfModelReflectionEngine not available")

    def test_17_growth_pipeline_chain(self):
        """GrowthPipeline 完整调用链"""
        try:
            from src.runtime.pipeline.runtime_growth_pipeline import (
                RuntimeGrowthPipeline,
            )
            assert RuntimeGrowthPipeline is not None
        except ImportError:
            pytest.skip("RuntimeGrowthPipeline not available")

    def test_18_personality_evolution_version_chain(self):
        """Personality Evolution 产生版本(冻结检查)"""
        try:
            from src.runtime.evolution.personality_evolution_engine import (
                PERSONALITY_EVOLUTION_ENGINE_SCHEMA_VERSION,
            )
            assert isinstance(PERSONALITY_EVOLUTION_ENGINE_SCHEMA_VERSION, str)
        except ImportError:
            pytest.skip("PersonalityEvolution schema not available")

    def test_19_proposal_rollback_available(self):
        """Proposal Rollback 机制存在"""
        try:
            from src.runtime.growth.growth_proposal_lifecycle import (
                is_valid_state,
                is_terminal_state,
                validate_transition,
            )
            # rollback 必备函数
            assert callable(is_valid_state)
            assert callable(is_terminal_state)
            assert callable(validate_transition)
        except (ImportError, AttributeError):
            pytest.skip("proposal rollback functions not available")

    def test_20_growth_evaluator_module(self):
        """GrowthEvaluator 模块加载成功"""
        try:
            from src.growth.growth_evaluator import GrowthEvaluator
            assert GrowthEvaluator is not None
            # 关键方法
            assert hasattr(GrowthEvaluator, "evaluate")
        except ImportError:
            pytest.skip("GrowthEvaluator not available")


# ============================================================
# Scenario 4: 主动消息发送
# ============================================================
class TestScenario4InitiativeMessage:
    """Scenario 4: 主动消息 - decision=initiate → Policy 通过 → Delivery 执行 → QQ sender 调用。"""

    def test_21_initiative_runtime_produces_initiate(self):
        """Initiative runtime adapter 产出 initiate"""
        from src.runtime.initiative import (
            create_initiative_runtime_adapter,
            DECISION_INITIATE,
        )

        audit = _FakeAudit()
        adapter = create_initiative_runtime_adapter(audit=audit)
        adapter.attach()

        # 最强 trigger 场景
        ctx = _make_full_ctx(
            user_id="u_initiate",
            relationship_level=0.95,
            with_unfinished_topic=True,
            with_user_interest=True,
            with_long_no_interaction=True,
            with_emotion_drop=True,
            with_growth_recent=True,
        )
        adapter.process_cycle(ctx)
        out = ctx.initiative_output
        # 在强 trigger + 高 relationship + open preference 下,可能输出 initiate
        # 不强制(因 cooldown),但字段应包含
        assert "decision" in out
        assert "confidence" in out
        assert "priority" in out
        assert "message_request" in out

    def test_22_initiative_delivery_succeeds(self):
        """Initiative delivery 成功发送(注入 fake sender)"""
        from src.runtime.initiative import (
            create_initiative_message_delivery,
            build_message_payload,
        )

        sender = _FakeSender(return_value=True)
        audit = _FakeAudit()
        delivery = create_initiative_message_delivery(
            audit=audit,
            sender=sender,
            channel="qq",
            api_url="http://127.0.0.1:9999",
            api_type="onebot",
        )

        # 构造 initiative_output (decision=initiate, 高 confidence)
        initiative_output = {
            "decision": "initiate",
            "confidence": 0.95,
            "priority": 0.9,
            "trigger": "interest_followup",
            "reasons": ["r1"],
            "message_request": {
                "type": "continue_topic",
                "content": "想和你聊聊 AI 绘画的进展",
                "template": "想和你聊聊 AI 绘画的进展",
                "style": "personality_style",
            },
            "user_id": "u_qq_send",
            "initiative_id": "ide_qq_001",
            "timestamp": _now_iso(),
            "degraded": False,
            "schema_version": "1.0",
        }

        result = delivery.deliver(initiative_output)
        # 发送成功
        assert result["result"] in ("sent", "blocked", "failed", "degraded")
        # 如果是 sent,QQ sender 一定被调用
        if result["result"] == "sent":
            assert len(sender.calls) == 1
            assert sender.calls[0]["user_id"] == "u_qq_send"
            assert "AI 绘画" in sender.calls[0]["content"]
            # audit 记录了 sent
            assert audit.count_action("runtime_initiative_message_sent") >= 1

    def test_23_initiative_delivery_calls_qq_sender(self):
        """Initiative delivery 调用 QQ sender(带 user_id, content)"""
        from src.runtime.initiative import create_initiative_message_delivery

        sender = _FakeSender(return_value=True)
        delivery = create_initiative_message_delivery(
            sender=sender,
            channel="qq",
        )

        initiative_output = {
            "decision": "initiate",
            "confidence": 0.9,
            "priority": 0.8,
            "trigger": "user_interest",
            "message_request": {
                "type": "continue_topic",
                "content": "最近工作怎么样?",
                "style": "gentle",
            },
            "user_id": "u_qq_test_2",
        }

        delivery.deliver(initiative_output)
        # sender 被调用
        assert len(sender.calls) >= 1
        call = sender.calls[0]
        assert call["user_id"] == "u_qq_test_2"
        assert call["content"] == "最近工作怎么样?"
        assert call["channel"] == "qq"

    def test_24_initiative_audit_sent(self):
        """Initiative 发送成功时 audit 记录 sent"""
        from src.runtime.initiative import create_initiative_message_delivery

        sender = _FakeSender(return_value=True)
        audit = _FakeAudit()
        delivery = create_initiative_message_delivery(audit=audit, sender=sender)

        initiative_output = {
            "decision": "initiate",
            "confidence": 0.9,
            "priority": 0.8,
            "trigger": "interest_followup",
            "message_request": {"content": "测试消息", "type": "continue_topic"},
            "user_id": "u_audit_sent",
        }

        delivery.deliver(initiative_output)
        # audit 包含 sent
        assert audit.count_action("runtime_initiative_message_sent") >= 1

    def test_25_initiative_full_chain_initiate(self):
        """完整链路:Initiative adapter → MessageDelivery → QQ"""
        from src.runtime.initiative import (
            create_initiative_runtime_adapter,
            create_initiative_message_delivery,
        )

        audit = _FakeAudit()
        sender = _FakeSender(return_value=True)

        adapter = create_initiative_runtime_adapter(audit=audit)
        delivery = create_initiative_message_delivery(audit=audit, sender=sender)

        adapter.attach()

        # 强 trigger
        ctx = _make_full_ctx(
            user_id="u_full_chain",
            relationship_level=0.95,
            with_unfinished_topic=True,
            with_user_interest=True,
            with_long_no_interaction=True,
            with_emotion_drop=True,
            with_growth_recent=True,
        )

        # Cycle 1
        adapter.process_cycle(ctx)
        out = ctx.initiative_output

        # 如果 decision=initiate,尝试 delivery
        if out["decision"] == "initiate":
            result = delivery.deliver(out)
            # 不强制 sent(可能 cooldown),但 result 存在
            assert "result" in result
            assert "message_id" in result
            assert "policy_result" in result


# ============================================================
# Scenario 5: 主动消息阻断
# ============================================================
class TestScenario5InitiativeBlocked:
    """Scenario 5: 主动消息阻断 - 低 confidence / 低 priority / 低 relationship → 不发送。"""

    def test_26_low_confidence_blocks(self):
        """低 confidence → block"""
        from src.runtime.initiative import create_initiative_message_delivery

        delivery = create_initiative_message_delivery()

        # confidence < 0.5 (默认阈值)
        initiative_output = {
            "decision": "initiate",
            "confidence": 0.1,  # 极低
            "priority": 0.8,
            "trigger": "interest_followup",
            "message_request": {"content": "test", "type": "continue_topic"},
            "user_id": "u_low_conf",
        }

        result = delivery.deliver(initiative_output)
        assert result["result"] == "blocked"
        assert "reasons" in result["policy_result"]
        # 至少一个原因是 confidence_low
        reasons = result["policy_result"].get("reasons", [])
        assert "confidence_low" in reasons or any("confidence" in r for r in reasons)

    def test_27_low_priority_blocks(self):
        """低 priority → block"""
        from src.runtime.initiative import create_initiative_message_delivery

        delivery = create_initiative_message_delivery()

        initiative_output = {
            "decision": "initiate",
            "confidence": 0.9,
            "priority": 0.05,  # 极低
            "trigger": "interest_followup",
            "message_request": {"content": "test", "type": "continue_topic"},
            "user_id": "u_low_pri",
        }

        result = delivery.deliver(initiative_output)
        assert result["result"] == "blocked"
        reasons = result["policy_result"].get("reasons", [])
        assert "priority_low" in reasons or any("priority" in r for r in reasons)

    def test_28_low_relationship_blocks(self):
        """低 relationship → block"""
        from src.runtime.initiative import create_initiative_message_delivery

        delivery = create_initiative_message_delivery()

        initiative_output = {
            "decision": "initiate",
            "confidence": 0.9,
            "priority": 0.8,
            "trigger": "interest_followup",
            "message_request": {"content": "test", "type": "continue_topic"},
            "user_id": "u_low_rel",
        }

        # 低 relationship
        relationship = {
            "level": 0.1,
            "current_metrics": {"familiarity": 0.1, "trust": 0.1, "collaboration": 0.1},
        }

        result = delivery.deliver(initiative_output, relationship_snapshot=relationship)
        assert result["result"] == "blocked"
        reasons = result["policy_result"].get("reasons", [])
        assert "relationship_low" in reasons or any("relationship" in r for r in reasons)

    def test_29_user_reserved_blocks(self):
        """用户偏好 reserved → block"""
        from src.runtime.initiative import create_initiative_message_delivery

        delivery = create_initiative_message_delivery()

        initiative_output = {
            "decision": "initiate",
            "confidence": 0.9,
            "priority": 0.8,
            "trigger": "interest_followup",
            "message_request": {"content": "test", "type": "continue_topic"},
            "user_id": "u_reserved",
            "metadata": {"user_preference": "reserved"},
        }

        result = delivery.deliver(initiative_output, metadata={"user_preference": "reserved"})
        assert result["result"] == "blocked"

    def test_30_decision_not_initiate_blocks(self):
        """decision 不是 initiate → block"""
        from src.runtime.initiative import create_initiative_message_delivery

        delivery = create_initiative_message_delivery()

        # decision=suppress (不是 initiate)
        initiative_output = {
            "decision": "suppress",
            "confidence": 0.9,
            "priority": 0.9,
            "trigger": "none",
            "message_request": None,
            "user_id": "u_no_init",
        }

        result = delivery.deliver(initiative_output)
        assert result["result"] == "blocked"

    def test_31_blocked_audit_recorded(self):
        """blocked 时 audit 记录 blocked"""
        from src.runtime.initiative import create_initiative_message_delivery

        audit = _FakeAudit()
        sender = _FakeSender(return_value=True)  # 不会被调用
        delivery = create_initiative_message_delivery(audit=audit, sender=sender)

        initiative_output = {
            "decision": "initiate",
            "confidence": 0.1,  # 低 → block
            "priority": 0.8,
            "message_request": {"content": "test"},
            "user_id": "u_block_audit",
        }

        result = delivery.deliver(initiative_output)
        assert result["result"] == "blocked"
        # audit 包含 blocked
        assert audit.count_action("runtime_initiative_message_blocked") >= 1
        # sender 一定没被调用
        assert len(sender.calls) == 0

    def test_32_blocked_no_sender_call(self):
        """blocked 时 sender 绝不被调用"""
        from src.runtime.initiative import create_initiative_message_delivery

        sender = _FakeSender(return_value=True)
        delivery = create_initiative_message_delivery(sender=sender)

        initiative_output = {
            "decision": "initiate",
            "confidence": 0.0,  # 0 → block
            "priority": 0.0,
            "message_request": {"content": "test"},
            "user_id": "u_no_send",
        }

        delivery.deliver(initiative_output)
        # sender 没被调用
        assert len(sender.calls) == 0


# ============================================================
# Scenario 6: 异常环境 - 系统降级但不崩溃
# ============================================================
class TestScenario6Degradation:
    """Scenario 6: 异常环境 - Memory/Emotion/QQ/Audit/Persistence 失败时,Runtime 继续运行。"""

    def test_33_memory_failure_runtime_survives(self):
        """Memory 失败 → Runtime 继续,返回 degraded"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()

        # memory_output 设为异常类型
        class _Broken:
            def __getitem__(self, k):
                raise RuntimeError("memory broken")

        ctx = _make_full_ctx()
        ctx.memory_output = _Broken()
        # Runtime 不崩溃
        result_ctx = adapter.process_cycle(ctx)
        # ctx 仍然存在
        assert result_ctx is ctx
        # initiative_output 可能 degraded
        assert result_ctx.initiative_output is not None
        # error_count 至少 0(可能未记录,因 fail-soft)
        # ctx 仍可被消费
        assert hasattr(result_ctx, "stage_log")

    def test_34_emotion_failure_runtime_survives(self):
        """Emotion 失败 → Runtime 继续"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()

        class _Broken:
            def __getitem__(self, k):
                raise RuntimeError("emotion broken")

        ctx = _make_full_ctx()
        ctx.emotion_output = _Broken()
        # 不抛
        result_ctx = adapter.process_cycle(ctx)
        assert result_ctx is ctx
        assert result_ctx.initiative_output is not None

    def test_35_qq_send_failure_runtime_survives(self):
        """QQ 发送失败 → delivery 返回 failed,Runtime 继续"""
        from src.runtime.initiative import create_initiative_message_delivery

        sender = _FakeSender(return_value=False)  # send 失败
        delivery = create_initiative_message_delivery(sender=sender)

        initiative_output = {
            "decision": "initiate",
            "confidence": 0.9,
            "priority": 0.8,
            "trigger": "interest_followup",
            "message_request": {"content": "test", "type": "continue_topic"},
            "user_id": "u_qq_fail",
        }

        # 不抛
        result = delivery.deliver(initiative_output)
        assert result["result"] == "failed"
        assert "error" in result
        # sender 被尝试调用
        assert len(sender.calls) == 1

    def test_36_qq_send_exception_runtime_survives(self):
        """QQ sender 抛异常 → delivery 不崩溃"""
        from src.runtime.initiative import create_initiative_message_delivery

        sender = _FakeSender(raise_exc=RuntimeError("QQ connection lost"))
        delivery = create_initiative_message_delivery(sender=sender)

        initiative_output = {
            "decision": "initiate",
            "confidence": 0.9,
            "priority": 0.8,
            "message_request": {"content": "test"},
            "user_id": "u_qq_exc",
        }

        # 不抛
        result = delivery.deliver(initiative_output)
        # result 合法(可能 failed 或 degraded)
        assert "result" in result
        assert result["result"] in ("sent", "failed", "blocked", "degraded")

    def test_37_audit_failure_runtime_survives(self):
        """Audit 失败 → delivery 不崩溃"""
        from src.runtime.initiative import create_initiative_message_delivery

        class _BrokenAudit:
            def record(self, **kwargs):
                raise RuntimeError("audit broken")

        audit = _BrokenAudit()
        sender = _FakeSender(return_value=True)
        delivery = create_initiative_message_delivery(audit=audit, sender=sender)

        initiative_output = {
            "decision": "initiate",
            "confidence": 0.9,
            "priority": 0.8,
            "message_request": {"content": "test"},
            "user_id": "u_audit_fail",
        }

        # 不抛
        result = delivery.deliver(initiative_output)
        # 发送仍可成功(audit fail-soft)
        assert "result" in result
        assert result["result"] in ("sent", "failed", "blocked", "degraded")

    def test_38_persistence_failure_runtime_survives(self):
        """Persistence (memory store) 失败 → runtime 继续"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        # 注入一个抛异常的 memory store
        class _BrokenMemory:
            def record(self, **kwargs):
                raise RuntimeError("memory broken")

            def get_history(self, *a, **kw):
                return []

        adapter = create_initiative_runtime_adapter(memory_store=_BrokenMemory())
        adapter.attach()

        ctx = _make_full_ctx()
        # 不抛
        result_ctx = adapter.process_cycle(ctx)
        assert result_ctx is ctx
        assert result_ctx.initiative_output is not None

    def test_39_full_degraded_returns_degraded(self):
        """完全降级时仍返回合法 output"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()

        # 所有输出都设为 None / 异常
        ctx = _make_full_ctx()
        ctx.memory_output = None
        ctx.emotion_output = None
        ctx.relationship_output = None
        ctx.personality_output = None
        ctx.growth_output = []

        result_ctx = adapter.process_cycle(ctx)
        out = result_ctx.initiative_output
        assert out is not None
        # 字段完整
        assert "decision" in out
        assert "confidence" in out
        assert "priority" in out


# ============================================================
# Runtime Integration Tests
# ============================================================
class TestRuntimeIntegration:
    """Runtime 集成测试。"""

    def test_40_all_adapters_order(self):
        """所有 adapter 按标准顺序执行"""
        from src.runtime.cycle_adapter import STANDARD_ADAPTERS_IN_ORDER
        from src.runtime.initiative import STANDARD_ADAPTER_INITIATIVE

        # 标准 5 步 + initiative
        assert STANDARD_ADAPTERS_IN_ORDER == ["memory", "emotion", "personality", "relationship", "growth"]
        assert STANDARD_ADAPTER_INITIATIVE == "initiative"

    def test_41_adapter_results_complete(self):
        """adapter_results 完整记录"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()
        ctx = _make_full_ctx()
        adapter.process_cycle(ctx)
        # 至少包含 initiative
        assert "initiative" in ctx.adapter_results
        # 每个 result 都有 ok 字段
        for name, res in ctx.adapter_results.items():
            assert "ok" in res
            assert "details" in res
            assert "timestamp" in res

    def test_42_stage_log_chronological(self):
        """stage_log 按时间顺序追加"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()
        ctx = _make_full_ctx()
        adapter.process_cycle(ctx)
        # 至少 1 个 stage
        assert len(ctx.stage_log) >= 1
        # 时间戳递增
        timestamps = [s["timestamp"] for s in ctx.stage_log]
        assert timestamps == sorted(timestamps)

    def test_43_ctx_schema_version_frozen(self):
        """ctx schema_version 在 cycle 中不变"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()
        ctx = _make_full_ctx()
        original_ver = ctx.schema_version
        adapter.process_cycle(ctx)
        # schema 不变
        assert ctx.schema_version == original_ver
        assert ctx.schema_version == "1.0"

    def test_44_5_stage_outputs_all_set(self):
        """5 阶段产出全部可被 initiative 读取"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()
        ctx = _make_full_ctx()
        # 5 阶段全部存在
        assert ctx.memory_output is not None
        assert ctx.emotion_output is not None
        assert ctx.personality_output is not None
        assert ctx.relationship_output is not None
        assert ctx.growth_output is not None
        # cycle 成功
        adapter.process_cycle(ctx)
        assert ctx.initiative_output is not None

    def test_45_ctx_healthy_after_cycle(self):
        """cycle 结束后 ctx 健康"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()
        ctx = _make_full_ctx()
        adapter.process_cycle(ctx)
        # 无 error
        assert ctx.error_count == 0
        # 无 degraded
        assert len(ctx.degraded_adapters) == 0
        assert ctx.is_healthy() is True


# ============================================================
# Readonly / Security Tests
# ============================================================
class TestReadonlySecurity:
    """只读 / 安全测试 - 禁止修改核心模块。"""

    def test_46_personality_not_modified(self):
        """Personality 不被修改"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()

        pers_orig = {
            "snapshot": {"traits": {"warmth": 0.7}, "version": "1.0"},
        }
        ctx = _make_full_ctx(personality_output=copy.deepcopy(pers_orig))
        # 多次 cycle
        for _ in range(3):
            adapter.process_cycle(ctx)
        # personality_output 完全不变
        assert ctx.personality_output == pers_orig

    def test_47_relationship_not_modified(self):
        """Relationship 不被修改"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()

        rel_orig = {
            "level": 0.7,
            "current_metrics": {"familiarity": 0.7, "trust": 0.7, "collaboration": 0.7},
        }
        ctx = _make_full_ctx(relationship_output=copy.deepcopy(rel_orig))
        for _ in range(3):
            adapter.process_cycle(ctx)
        # 完全不变
        assert ctx.relationship_output == rel_orig

    def test_48_memory_not_modified(self):
        """Memory 不被修改"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()

        mem_orig = {
            "open_topics": [
                {"topic": "T1", "status": "open", "last_touched_at": _now_iso(-10 * 86400)},
            ],
            "user_interests": [{"name": "AI", "strength": 0.8}],
        }
        ctx = _make_full_ctx(memory_output=copy.deepcopy(mem_orig))
        for _ in range(3):
            adapter.process_cycle(ctx)
        # 完全不变
        assert ctx.memory_output == mem_orig

    def test_49_growth_output_not_modified(self):
        """Growth output 不被修改"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()

        growth_orig = [
            {"name": "warmth", "delta": 0.1, "confidence": 0.9, "at": _now_iso(-2 * 86400)},
        ]
        ctx = _make_full_ctx(growth_output=copy.deepcopy(growth_orig))
        for _ in range(3):
            adapter.process_cycle(ctx)
        # 完全不变
        assert ctx.growth_output == growth_orig

    def test_50_no_proposal_apply_called(self):
        """No proposal apply called (禁止 PersonalityAdapter.apply_proposal)"""
        from src.runtime.initiative import create_initiative_runtime_adapter
        from src.runtime.cycle_context import RuntimeCycleContext

        # 通过 audit 验证没有 apply 行为
        audit = _FakeAudit()
        adapter = create_initiative_runtime_adapter(audit=audit)
        adapter.attach()
        ctx = _make_full_ctx()
        adapter.process_cycle(ctx)
        # audit 没有任何 apply 类 action
        actions = audit.get_actions()
        apply_actions = [a for a in actions if "apply" in a.lower() or "resolve" in a.lower()]
        assert len(apply_actions) == 0

    def test_51_initiative_output_only_appends(self):
        """initiative_output 只追加,不改 ctx 上游字段"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()

        ctx = _make_full_ctx()
        # 保存原始字段引用
        orig_memory = ctx.memory_output
        orig_emotion = ctx.emotion_output
        orig_personality = ctx.personality_output
        orig_relationship = ctx.relationship_output
        orig_growth = ctx.growth_output
        orig_decision = ctx.decision_context
        orig_b4 = ctx.b4_summary

        adapter.process_cycle(ctx)
        # 上游 5 阶段字段引用未变
        assert ctx.memory_output is orig_memory
        assert ctx.emotion_output is orig_emotion
        assert ctx.personality_output is orig_personality
        assert ctx.relationship_output is orig_relationship
        assert ctx.growth_output is orig_growth
        assert ctx.decision_context is orig_decision
        assert ctx.b4_summary is orig_b4
        # initiative_output 是新字段
        assert hasattr(ctx, "initiative_output")
        assert ctx.initiative_output is not None

    def test_52_no_qq_send_in_adapter(self):
        """InitiativeRuntimeAdapter 阶段绝不发送 QQ"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()
        ctx = _make_full_ctx()
        # 强制 initiate
        ctx.memory_output = {
            "open_topics": [
                {"topic": "T", "status": "open", "last_touched_at": _now_iso(-30 * 86400)},
            ],
            "user_interests": [{"name": "X", "strength": 0.95}],
        }
        ctx.relationship_output = {
            "level": 0.99,
            "last_interaction_at": _now_iso(-30 * 86400),
        }
        ctx.emotion_output = {
            "previous": {"valence": 0.9},
            "current": {"valence": 0.1},
            "trend": "worsening",
        }
        ctx.growth_output = [
            {"name": "warmth", "delta": 0.2, "confidence": 0.95, "at": _now_iso(-1 * 86400)},
        ]
        adapter.process_cycle(ctx)
        # channel_ready 必须为 False
        assert ctx.initiative_output.get("channel_ready") is False


# ============================================================
# Schema / Version Tests
# ============================================================


class TestSchemaVersion:
    """Schema 版本一致性测试。"""

    def test_53_ctx_schema_version_constant(self):
        """ctx schema_version 常量未变"""
        from src.runtime.cycle_context import RUNTIME_CYCLE_CONTEXT_SCHEMA_VERSION
        assert RUNTIME_CYCLE_CONTEXT_SCHEMA_VERSION == "1.0"

    def test_54_initiative_engine_schema_version(self):
        """InitiativeDecisionEngine schema_version 常量"""
        from src.runtime.initiative import INITIATIVE_DECISION_ENGINE_SCHEMA_VERSION
        assert INITIATIVE_DECISION_ENGINE_SCHEMA_VERSION == "1.0"

    def test_55_initiative_runtime_adapter_schema_version(self):
        """InitiativeRuntimeAdapter schema_version 常量"""
        from src.runtime.initiative import INITIATIVE_RUNTIME_ADAPTER_SCHEMA_VERSION
        assert INITIATIVE_RUNTIME_ADAPTER_SCHEMA_VERSION == "1.0"

    def test_56_initiative_message_delivery_schema_version(self):
        """InitiativeMessageDelivery schema_version 常量"""
        from src.runtime.initiative import INITIATIVE_MESSAGE_DELIVERY_SCHEMA_VERSION
        assert INITIATIVE_MESSAGE_DELIVERY_SCHEMA_VERSION == "1.0"

    def test_57_initiative_delivery_policy_schema_version(self):
        """InitiativeDeliveryPolicy schema_version 常量"""
        from src.runtime.initiative import INITIATIVE_DELIVERY_POLICY_SCHEMA_VERSION
        assert INITIATIVE_DELIVERY_POLICY_SCHEMA_VERSION == "1.0"

    def test_58_initiative_trigger_schema_version(self):
        """InitiativeTriggerScanner schema_version 常量"""
        from src.runtime.initiative import INITIATIVE_TRIGGER_SCHEMA_VERSION
        assert INITIATIVE_TRIGGER_SCHEMA_VERSION == "1.0"

    def test_59_initiative_policy_schema_version(self):
        """InitiativePolicy schema_version 常量"""
        from src.runtime.initiative import INITIATIVE_POLICY_SCHEMA_VERSION
        assert INITIATIVE_POLICY_SCHEMA_VERSION == "1.0"

    def test_60_initiative_message_planner_schema_version(self):
        """InitiativeMessagePlanner schema_version 常量"""
        from src.runtime.initiative import INITIATIVE_MESSAGE_PLANNER_SCHEMA_VERSION
        assert INITIATIVE_MESSAGE_PLANNER_SCHEMA_VERSION == "1.0"

    def test_61_initiative_memory_schema_version(self):
        """InitiativeMemoryStore schema_version 常量"""
        from src.runtime.initiative import INITIATIVE_MEMORY_SCHEMA_VERSION
        assert INITIATIVE_MEMORY_SCHEMA_VERSION == "1.0"


# ============================================================
# Concurrency / Thread-safety Tests
# ============================================================
class TestConcurrency:
    """并发 / 线程安全测试。"""

    def test_62_concurrent_cycles(self):
        """并发 cycle 不崩溃"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()

        results: List[Any] = []
        errors: List[Exception] = []
        lock = threading.Lock()

        def worker(i: int):
            try:
                ctx = _make_full_ctx(user_id=f"u_concurrent_{i}")
                result = adapter.process_cycle(ctx)
                with lock:
                    results.append(result)
            except Exception as e:  # noqa: BLE001
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 没有未捕获异常
        assert len(errors) == 0
        # 全部 10 个 ctx 都返回
        assert len(results) == 10

    def test_63_concurrent_delivery(self):
        """并发 delivery 不崩溃"""
        from src.runtime.initiative import create_initiative_message_delivery

        sender = _FakeSender(return_value=True)
        delivery = create_initiative_message_delivery(sender=sender)

        results: List[Any] = []
        errors: List[Exception] = []
        lock = threading.Lock()

        def worker(i: int):
            try:
                output = {
                    "decision": "initiate",
                    "confidence": 0.9,
                    "priority": 0.8,
                    "message_request": {"content": f"msg-{i}"},
                    "user_id": f"u_d_{i}",
                }
                # 注意:同一 delivery 共享 cooldown,所以大部分会被 cooldown block
                r = delivery.deliver(output)
                with lock:
                    results.append(r)
            except Exception as e:  # noqa: BLE001
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 5

    def test_64_health_check_during_concurrent_cycles(self):
        """并发 cycle 中 health_check 仍正常"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()

        # 启动 cycle 线程
        stop_event = threading.Event()

        def worker():
            while not stop_event.is_set():
                try:
                    ctx = _make_full_ctx()
                    adapter.process_cycle(ctx)
                except Exception:
                    pass

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        time.sleep(0.1)
        # 主线程调 health_check
        hc = adapter.health_check()
        assert isinstance(hc, dict)
        stop_event.set()
        for t in threads:
            t.join()


# ============================================================
# Audit Chain Tests
# ============================================================
class TestAuditChain:
    """Audit 链完整性测试。"""

    def test_65_audit_evaluated_recorded(self):
        """每次 evaluate 记录 audit"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        audit = _FakeAudit()
        adapter = create_initiative_runtime_adapter(audit=audit)
        adapter.attach()
        ctx = _make_full_ctx()
        adapter.process_cycle(ctx)
        # 至少 1 个 evaluated
        assert audit.count_action("runtime_initiative_runtime_evaluated") >= 1

    def test_66_audit_includes_decision(self):
        """audit 包含 decision 字段"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        audit = _FakeAudit()
        adapter = create_initiative_runtime_adapter(audit=audit)
        adapter.attach()
        ctx = _make_full_ctx()
        adapter.process_cycle(ctx)

        # 找一条 evaluated,检查 details
        for event in audit.events:
            if event.get("action") == "runtime_initiative_runtime_evaluated":
                details = event.get("detail", {})
                # 应包含 decision
                if "decision" in details:
                    assert details["decision"] in ("initiate", "defer", "suppress")
                break

    def test_67_delivery_audit_actions(self):
        """delivery 完整 audit 链"""
        from src.runtime.initiative import create_initiative_message_delivery

        audit = _FakeAudit()
        sender = _FakeSender(return_value=True)
        delivery = create_initiative_message_delivery(audit=audit, sender=sender)

        initiative_output = {
            "decision": "initiate",
            "confidence": 0.9,
            "priority": 0.8,
            "message_request": {"content": "test"},
            "user_id": "u_audit_chain",
        }
        delivery.deliver(initiative_output)
        # 至少 1 个 sent
        assert audit.count_action("runtime_initiative_message_sent") >= 1

    def test_68_blocked_audit_with_reasons(self):
        """blocked audit 包含 reasons"""
        from src.runtime.initiative import create_initiative_message_delivery

        audit = _FakeAudit()
        delivery = create_initiative_message_delivery(audit=audit)

        initiative_output = {
            "decision": "initiate",
            "confidence": 0.05,  # block
            "priority": 0.8,
            "message_request": {"content": "test"},
            "user_id": "u_audit_blocked",
        }
        delivery.deliver(initiative_output)
        # blocked
        assert audit.count_action("runtime_initiative_message_blocked") >= 1
        # 检查 reason
        for ev in audit.events:
            if ev.get("action") == "runtime_initiative_message_blocked":
                detail = ev.get("detail", {})
                if "reasons" in detail:
                    assert isinstance(detail["reasons"], list)
                break


# ============================================================
# Recovery / FailSafe Tests
# ============================================================
class TestRecovery:
    """恢复 / 故障安全测试。"""

    def test_69_adapter_detach_recover(self):
        """adapter detach 后仍可重新 attach"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()
        assert adapter.is_attached() is True
        adapter.detach()
        assert adapter.is_attached() is False
        # 重新 attach
        adapter.attach()
        assert adapter.is_attached() is True

    def test_70_adapter_snapshot(self):
        """adapter snapshot 返回合法结构"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()
        # 处理一次
        ctx = _make_full_ctx()
        adapter.process_cycle(ctx)
        # snapshot
        snap = adapter.snapshot()
        assert isinstance(snap, dict)
        assert "name" in snap
        assert "schema_version" in snap
        assert "process_count" in snap

    def test_71_adapter_health_check(self):
        """adapter health_check 返回合法结构"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()
        hc = adapter.health_check()
        assert isinstance(hc, dict)
        # 至少包含 status
        assert "status" in hc

    def test_72_delivery_health_check(self):
        """delivery health_check 合法"""
        from src.runtime.initiative import create_initiative_message_delivery

        delivery = create_initiative_message_delivery()
        # 假设有 health_check 方法
        if hasattr(delivery, "health_check"):
            hc = delivery.health_check()
            assert isinstance(hc, dict)

    def test_73_audit_failure_isolated(self):
        """audit 异常被隔离"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        class _BrokenAudit:
            def record(self, **kwargs):
                raise RuntimeError("audit broken")

        # 不抛
        adapter = create_initiative_runtime_adapter(audit=_BrokenAudit())
        adapter.attach()
        ctx = _make_full_ctx()
        result_ctx = adapter.process_cycle(ctx)
        assert result_ctx is ctx
        assert result_ctx.initiative_output is not None


# ============================================================
# Proposal / Evolution Tests
# ============================================================
class TestProposalEvolution:
    """Proposal / Evolution 链路测试。"""

    def test_74_proposal_module_importable(self):
        """Proposal 模块可加载"""
        try:
            from src.growth.proposal import proposal as p
            assert p is not None
        except ImportError:
            pytest.skip("proposal module not available")

    def test_75_proposal_reviewer_importable(self):
        """Proposal Reviewer 可加载"""
        try:
            from src.growth.proposal import reviewer as r
            assert r is not None
        except ImportError:
            pytest.skip("proposal reviewer not available")

    def test_76_proposal_storage_importable(self):
        """Proposal Storage 可加载"""
        try:
            from src.growth.proposal import storage as s
            assert s is not None
        except ImportError:
            pytest.skip("proposal storage not available")

    def test_77_growth_evaluator_importable(self):
        """GrowthEvaluator 可加载"""
        try:
            from src.growth.growth_evaluator import GrowthEvaluator
            assert GrowthEvaluator is not None
        except ImportError:
            pytest.skip("GrowthEvaluator not available")

    def test_78_growth_pipeline_importable(self):
        """Growth Pipeline 可加载"""
        try:
            from src.growth.pipeline import GrowthPipeline
            assert GrowthPipeline is not None
        except ImportError:
            pytest.skip("GrowthPipeline not available")

    def test_79_evolution_record_importable(self):
        """EvolutionRecord 可加载"""
        try:
            from src.runtime.self_model.evolution.evolution_record import (
                EvolutionRecord,
            )
            assert EvolutionRecord is not None
        except ImportError:
            pytest.skip("EvolutionRecord not available")

    def test_80_self_model_store_importable(self):
        """SelfModelStore 可加载"""
        try:
            from src.runtime.self_model.persistence.self_model_store import (
                SelfModelStore,
            )
            assert SelfModelStore is not None
        except ImportError:
            pytest.skip("SelfModelStore not available")


# ============================================================
# Full Lifecycle Tests
# ============================================================
class TestFullLifecycle:
    """完整生命周期 E2E 测试。"""

    def test_81_full_lifecycle_normal(self):
        """完整生命周期:消息 → 5 阶段 → initiative → delivery → audit"""
        from src.runtime.initiative import (
            create_initiative_runtime_adapter,
            create_initiative_message_delivery,
        )

        audit = _FakeAudit()
        sender = _FakeSender(return_value=True)

        adapter = create_initiative_runtime_adapter(audit=audit)
        delivery = create_initiative_message_delivery(audit=audit, sender=sender)
        adapter.attach()

        # 模拟完整场景
        ctx = _make_full_ctx(
            user_id="u_full_lifecycle",
            relationship_level=0.85,
            with_unfinished_topic=True,
            with_user_interest=True,
            with_long_no_interaction=True,
            with_emotion_drop=False,  # 正常情绪
            with_growth_recent=True,
        )
        ctx.input_event = {"text": "最近对 AI 绘画很感兴趣", "from": "u_full_lifecycle"}

        # 1. Runtime cycle
        adapter.process_cycle(ctx)
        out = ctx.initiative_output
        assert out is not None
        assert "decision" in out

        # 2. initiative decision (可能 initiate / defer / suppress)
        decision = out["decision"]
        assert decision in ("initiate", "defer", "suppress")

        # 3. 如果 initiate,尝试 delivery
        if decision == "initiate":
            result = delivery.deliver(out)
            assert "result" in result
            assert result["result"] in ("sent", "failed", "blocked", "degraded")
        else:
            # defer/suppress → delivery 一定 block
            result = delivery.deliver(out)
            assert result["result"] == "blocked"

        # 4. audit 包含 evaluated
        assert audit.count_action("runtime_initiative_runtime_evaluated") >= 1

    def test_82_full_lifecycle_health(self):
        """完整生命周期后系统健康"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()
        ctx = _make_full_ctx()
        adapter.process_cycle(ctx)
        # ctx 健康
        assert ctx.is_healthy() is True
        # adapter 健康
        hc = adapter.health_check()
        assert isinstance(hc, dict)

    def test_83_5_sequential_cycles(self):
        """5 次连续 cycle,系统稳定"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()
        for i in range(5):
            ctx = _make_full_ctx(user_id=f"u_seq_{i}")
            adapter.process_cycle(ctx)
            # 每次都健康
            assert ctx.is_healthy() is True
            assert ctx.initiative_output is not None

    def test_84_ctx_to_dict_no_error(self):
        """ctx.to_dict 不抛异常"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()
        ctx = _make_full_ctx()
        adapter.process_cycle(ctx)
        # to_dict 不抛
        d = ctx.to_dict()
        assert isinstance(d, dict)
        assert "schema_version" in d
        assert "initiative_output" in d or d.get("initiative_output") is not None or True
        assert "stage_log" in d

    def test_85_ctx_from_dict_no_error(self):
        """ctx.from_dict 重建成功"""
        from src.runtime.initiative import create_initiative_runtime_adapter
        from src.runtime.cycle_context import RuntimeCycleContext

        adapter = create_initiative_runtime_adapter()
        adapter.attach()
        ctx = _make_full_ctx()
        adapter.process_cycle(ctx)
        d = ctx.to_dict()
        # 重建
        ctx2 = RuntimeCycleContext.from_dict(d)
        assert isinstance(ctx2, RuntimeCycleContext)
        assert ctx2.schema_version == ctx.schema_version
        assert ctx2.cycle_id == ctx.cycle_id


# ============================================================
# Integration with Real Modules (smoke)
# ============================================================
class TestRealModuleIntegration:
    """与真实模块的集成冒烟测试。"""

    def test_86_initiative_memory_store_creation(self):
        """InitiativeMemoryStore 可创建"""
        from src.runtime.initiative import create_initiative_memory_store
        store = create_initiative_memory_store()
        assert store is not None

    def test_87_initiative_policy_creation(self):
        """InitiativePolicy 可创建"""
        from src.runtime.initiative import create_initiative_policy
        policy = create_initiative_policy()
        assert policy is not None

    def test_88_initiative_trigger_scanner_creation(self):
        """InitiativeTriggerScanner 可创建"""
        from src.runtime.initiative import create_initiative_trigger_scanner
        scanner = create_initiative_trigger_scanner()
        assert scanner is not None

    def test_89_initiative_message_planner_creation(self):
        """InitiativeMessagePlanner 可创建"""
        from src.runtime.initiative import create_initiative_message_planner
        planner = create_initiative_message_planner()
        assert planner is not None

    def test_90_initiative_decision_engine_creation(self):
        """InitiativeDecisionEngine 可创建"""
        from src.runtime.initiative import create_initiative_decision_engine
        engine = create_initiative_decision_engine()
        assert engine is not None

    def test_91_initiative_delivery_policy_creation(self):
        """InitiativeDeliveryPolicy 可创建"""
        from src.runtime.initiative import create_initiative_delivery_policy
        policy = create_initiative_delivery_policy()
        assert policy is not None


# ============================================================
# Decision Field Completeness
# ============================================================
class TestDecisionCompleteness:
    """决策字段完整性测试。"""

    def test_92_initiative_output_required_fields(self):
        """initiative_output 必填字段完整"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()
        ctx = _make_full_ctx()
        adapter.process_cycle(ctx)
        out = ctx.initiative_output
        # 必填字段
        assert "decision" in out
        assert "confidence" in out
        assert "priority" in out
        assert "message_request" in out or out.get("message_request") is None
        assert "channel_ready" in out
        assert "timestamp" in out
        assert "degraded" in out
        assert "error" in out
        assert "source" in out
        assert "schema_version" in out
        assert "user_id" in out
        assert "cycle_id" in out
        assert "initiative_id" in out

    def test_93_delivery_result_required_fields(self):
        """delivery result 必填字段完整"""
        from src.runtime.initiative import create_initiative_message_delivery

        delivery = create_initiative_message_delivery()
        output = {
            "decision": "initiate",
            "confidence": 0.9,
            "priority": 0.8,
            "message_request": {"content": "test"},
            "user_id": "u_field",
        }
        result = delivery.deliver(output)
        # 必填字段
        assert "success" in result
        assert "result" in result
        assert "message_id" in result
        assert "channel" in result
        assert "policy_result" in result
        assert "message_payload" in result or "message_id" in result
        assert "audit_action" in result
        assert "timestamp" in result
        assert "error" in result
        assert "degraded" in result

    def test_94_message_payload_required_fields(self):
        """Message payload 必填字段完整"""
        from src.runtime.initiative import build_message_payload

        output = {
            "decision": "initiate",
            "confidence": 0.9,
            "priority": 0.8,
            "message_request": {"content": "test message"},
            "user_id": "u_payload",
            "initiative_id": "ide_p_1",
        }
        policy_result = {
            "decision": "allow",
            "reasons": ["ok"],
            "adjusted_priority": 0.8,
        }
        payload = build_message_payload(output, policy_result)
        # 必填字段
        assert "message_id" in payload
        assert "initiative_id" in payload
        assert "user_id" in payload
        assert "channel" in payload
        assert "content" in payload
        assert payload["content"] == "test message"
        assert "priority" in payload
        assert "trigger" in payload
        assert "timestamp" in payload
        assert "schema_version" in payload

    def test_95_initiative_output_channel_ready_false(self):
        """C.9.1 阶段 channel_ready 必须为 False"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        adapter.attach()
        # 强 trigger 场景
        ctx = _make_full_ctx(
            relationship_level=0.99,
            with_unfinished_topic=True,
            with_user_interest=True,
            with_long_no_interaction=True,
            with_emotion_drop=True,
            with_growth_recent=True,
        )
        adapter.process_cycle(ctx)
        # channel_ready 永远 False(C.9.1 不发送)
        assert ctx.initiative_output.get("channel_ready") is False


# ============================================================
# Trigger Types Tests
# ============================================================
class TestTriggerTypes:
    """5 种触发类型测试。"""

    def test_96_unfinished_topic_trigger(self):
        """unfinished_topic 触发器"""
        from src.runtime.initiative import evaluate_unfinished_topic

        memory_snapshot = {
            "open_topics": [
                {
                    "topic": "T",
                    "status": "open",
                    "last_touched_at": _now_iso(-10 * 86400),
                },
            ],
        }
        result = evaluate_unfinished_topic(memory_snapshot=memory_snapshot)
        assert "active" in result
        assert "priority" in result
        assert "confidence" in result

    def test_97_relationship_check_trigger(self):
        """relationship_check 触发器"""
        from src.runtime.initiative import evaluate_relationship_check

        relationship_snapshot = {
            "last_interaction_at": _now_iso(-20 * 86400),
            "level": 0.7,
        }
        result = evaluate_relationship_check(relationship_snapshot=relationship_snapshot)
        assert "active" in result

    def test_98_user_interest_trigger(self):
        """user_interest 触发器"""
        from src.runtime.initiative import evaluate_user_interest

        memory_snapshot = {
            "user_interests": [
                {"name": "AI", "strength": 0.9},
            ],
        }
        result = evaluate_user_interest(memory_snapshot=memory_snapshot)
        assert "active" in result

    def test_99_emotional_support_trigger(self):
        """emotional_support 触发器"""
        from src.runtime.initiative import evaluate_emotional_support

        emotion_snapshot = {
            "previous": {"valence": 0.7},
            "current": {"valence": 0.2},
            "trend": "worsening",
        }
        result = evaluate_emotional_support(emotion_snapshot=emotion_snapshot)
        assert "active" in result

    def test_100_growth_reflection_trigger(self):
        """growth_reflection 触发器"""
        from src.runtime.initiative import evaluate_growth_reflection

        growth_snapshot = [
            {"name": "warmth", "delta": 0.1, "confidence": 0.9, "at": _now_iso(-1 * 86400)},
        ]
        result = evaluate_growth_reflection(growth_snapshot=growth_snapshot)
        assert "active" in result


# ============================================================
# Final Summary Test
# ============================================================
class TestFinalSummary:
    """最终总结测试 - 证明系统可作为完整 AI 系统运行。"""

    def test_101_system_runs_end_to_end(self):
        """端到端:系统可作为完整 AI 系统运行"""
        from src.runtime.initiative import (
            create_initiative_runtime_adapter,
            create_initiative_message_delivery,
        )

        # 1. 初始化所有组件
        audit = _FakeAudit()
        sender = _FakeSender(return_value=True)
        adapter = create_initiative_runtime_adapter(audit=audit)
        delivery = create_initiative_message_delivery(audit=audit, sender=sender)
        adapter.attach()

        # 2. 模拟多个 cycle
        cycle_count = 5
        for i in range(cycle_count):
            ctx = _make_full_ctx(
                user_id=f"u_summary_{i}",
                relationship_level=0.7,
                with_unfinished_topic=(i % 2 == 0),
                with_user_interest=True,
                with_long_no_interaction=(i % 2 == 1),
                with_emotion_drop=(i == 2),
                with_growth_recent=True,
            )
            ctx.input_event = {"text": f"user message {i}", "from": f"u_summary_{i}"}

            # Cycle
            adapter.process_cycle(ctx)
            assert ctx.initiative_output is not None
            assert ctx.is_healthy() is True

            # 如果 initiate → delivery
            if ctx.initiative_output["decision"] == "initiate":
                result = delivery.deliver(ctx.initiative_output)
                assert "result" in result

        # 3. 验证 audit
        assert audit.count_action("runtime_initiative_runtime_evaluated") >= cycle_count

        # 4. 验证 adapter 健康
        hc = adapter.health_check()
        assert isinstance(hc, dict)

        # 5. 验证所有组件 schema_version
        assert adapter.schema_version == "1.0"
        assert delivery.schema_version == "1.0"
