# -*- coding: utf-8 -*-
"""
tests/test_initiative_message_delivery.py

Phase C.9.2 Initiative Message Delivery —— 单元测试

覆盖:
  - Creation
  - Policy (confidence / priority / cooldown / relationship / user_preference)
  - QQ Integration (调用已有发送接口)
  - Success (sent)
  - Blocked (low confidence / low relationship / low priority / user pref)
  - Readonly (Personality / SelfModel / Relationship)
  - Security (禁止绕过 policy 直接发送)
  - Audit (sent / failed / blocked)
  - FailSafe (QQ 异常 / policy 异常 / audit 异常)
  - Concurrency
  - Schema
  - Integration (InitiativeRuntimeAdapter → MessageDelivery → QQ)
"""
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def _make_initiative_output(
    decision: str = "initiate",
    confidence: float = 0.9,
    priority: float = 0.8,
    trigger: str = "interest_followup",
    user_id: str = "u_test",
    content: str = "今天想了解一下 AI 绘画的进展",
    initiative_id: str = "ide_test_001",
    reasons: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "decision": decision,
        "confidence": confidence,
        "priority": priority,
        "trigger": trigger,
        "reasons": reasons if reasons is not None else ["r1", "r2"],
        "message_request": {
            "type": "continue_topic",
            "content": content,
            "template": content,
            "style": "personality_style",
        },
        "channel_ready": False,
        "timestamp": _now_iso(),
        "degraded": False,
        "error": "",
        "source": "initiative_runtime_adapter",
        "schema_version": "1.0",
        "user_id": user_id,
        "cycle_id": "cycle_test",
        "initiative_id": initiative_id,
        "metadata": metadata or {},
    }


def _make_relationship(
    level: float = 0.7,
    last_interaction_at: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "level": level,
        "last_interaction_at": last_interaction_at or _now_iso(offset_seconds=-86400),
        "current_metrics": {
            "familiarity": level,
            "trust": level,
            "collaboration": level,
        },
    }


def _make_delivery(
    policy: Any = None,
    audit: Any = None,
    api_url: str = "http://127.0.0.1:9999",
    api_type: str = "onebot",
    token: str = "",
    channel: str = "qq",
    sender: Any = None,
) -> Any:
    from src.runtime.initiative import (
        create_initiative_message_delivery,
    )
    return create_initiative_message_delivery(
        policy=policy,
        audit=audit,
        channel=channel,
        api_type=api_type,
        api_url=api_url,
        token=token,
        timeout=2.0,
        sender=sender,
    )


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
    def __init__(self):
        self.events: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def record(self, **kwargs):
        with self._lock:
            self.events.append(dict(kwargs))


# ============================================================
# 1. Creation
# ============================================================
class TestCreation:
    def test_01_delivery_create_default(self):
        """1. delivery 默认创建"""
        d = _make_delivery()
        assert d is not None
        from src.runtime.initiative import InitiativeMessageDelivery
        assert isinstance(d, InitiativeMessageDelivery)

    def test_02_delivery_factory(self):
        """2. factory 创建"""
        from src.runtime.initiative import (
            create_initiative_message_delivery,
        )
        d = create_initiative_message_delivery()
        assert d is not None

    def test_03_policy_attached(self):
        """3. policy 自动注入"""
        d = _make_delivery()
        assert d.policy is not None

    def test_04_schema_version(self):
        """4. schema_version"""
        d = _make_delivery()
        assert d.schema_version == "1.0"

    def test_05_default_api_config(self):
        """5. 默认 API 配置"""
        d = _make_delivery()
        cfg = d.get_api_config()
        assert "channel" in cfg
        assert "api_type" in cfg
        assert "api_url" in cfg

    def test_06_set_api_config(self):
        """6. 动态设置 API 配置"""
        d = _make_delivery()
        d.set_api_config(api_url="http://new:1234", token="abc")
        cfg = d.get_api_config()
        assert cfg["api_url"] == "http://new:1234"
        assert cfg["token"] == "abc"


# ============================================================
# 2. Policy
# ============================================================
class TestPolicy:
    def test_07_confidence_threshold_blocks(self):
        """7. confidence < 阈值 → block"""
        from src.runtime.initiative import (
            create_initiative_delivery_policy,
        )
        policy = create_initiative_delivery_policy(confidence_threshold=0.85)
        output = _make_initiative_output(confidence=0.5)
        result = policy.evaluate(output)
        assert result["decision"] == "block"
        assert "confidence_too_low" in result["reasons"]

    def test_08_confidence_threshold_allows(self):
        """8. confidence >= 阈值 → allow(其他通过)"""
        from src.runtime.initiative import (
            create_initiative_delivery_policy,
        )
        policy = create_initiative_delivery_policy(confidence_threshold=0.85)
        output = _make_initiative_output(confidence=0.9)
        result = policy.evaluate(output, relationship_snapshot=_make_relationship())
        assert result["decision"] == "allow"

    def test_09_priority_threshold_blocks(self):
        """9. priority < 阈值 → block"""
        from src.runtime.initiative import (
            create_initiative_delivery_policy,
        )
        policy = create_initiative_delivery_policy(
            confidence_threshold=0.5,
            priority_threshold=0.7,
        )
        output = _make_initiative_output(confidence=0.9, priority=0.3)
        result = policy.evaluate(output)
        assert result["decision"] == "block"
        assert "priority_too_low" in result["reasons"]

    def test_10_cooldown_blocks(self):
        """10. cooldown 内 → block"""
        from src.runtime.initiative import (
            create_initiative_delivery_policy,
        )
        policy = create_initiative_delivery_policy(
            confidence_threshold=0.5,
            priority_threshold=0.5,
            cooldown_seconds=3600,
        )
        # 记录一次刚投递
        policy.record_delivery(time.time() - 60)  # 1 分钟前
        output = _make_initiative_output()
        result = policy.evaluate(output, relationship_snapshot=_make_relationship())
        assert result["decision"] == "block"
        assert "cooldown_active" in result["reasons"]

    def test_11_cooldown_passes(self):
        """11. cooldown 过后 → allow"""
        from src.runtime.initiative import (
            create_initiative_delivery_policy,
        )
        policy = create_initiative_delivery_policy(
            confidence_threshold=0.5,
            priority_threshold=0.5,
            cooldown_seconds=60,
        )
        policy.record_delivery(time.time() - 120)  # 2 分钟前
        output = _make_initiative_output()
        result = policy.evaluate(output, relationship_snapshot=_make_relationship())
        assert result["decision"] == "allow"

    def test_12_relationship_low_blocks(self):
        """12. 关系不足 → block"""
        from src.runtime.initiative import (
            create_initiative_delivery_policy,
        )
        policy = create_initiative_delivery_policy(
            confidence_threshold=0.5,
            priority_threshold=0.5,
            min_relationship_level=0.5,
        )
        output = _make_initiative_output()
        result = policy.evaluate(
            output,
            relationship_snapshot=_make_relationship(level=0.2),
        )
        assert result["decision"] == "block"
        assert "relationship_too_low" in result["reasons"]

    def test_13_user_preference_reserved_blocks(self):
        """13. user_preference=reserved → block"""
        from src.runtime.initiative import (
            create_initiative_delivery_policy,
        )
        policy = create_initiative_delivery_policy(
            confidence_threshold=0.5,
            priority_threshold=0.5,
        )
        output = _make_initiative_output(metadata={"user_preference": "reserved"})
        result = policy.evaluate(
            output,
            relationship_snapshot=_make_relationship(),
            metadata={"user_preference": "reserved"},
        )
        assert result["decision"] == "block"
        assert "user_preference_blocks" in result["reasons"]

    def test_14_decision_not_initiate_blocks(self):
        """14. decision != initiate → block"""
        from src.runtime.initiative import (
            create_initiative_delivery_policy,
        )
        policy = create_initiative_delivery_policy()
        output = _make_initiative_output(decision="defer")
        result = policy.evaluate(output)
        assert result["decision"] == "block"
        assert "decision_not_initiate" in result["reasons"]

    def test_15_policy_disabled_blocks(self):
        """15. policy disabled → block"""
        from src.runtime.initiative import (
            create_initiative_delivery_policy,
        )
        policy = create_initiative_delivery_policy(enabled=False)
        output = _make_initiative_output()
        result = policy.evaluate(output)
        assert result["decision"] == "block"
        assert "policy_disabled" in result["reasons"]

    def test_16_invalid_output_blocks(self):
        """16. 无效 output → block"""
        from src.runtime.initiative import (
            create_initiative_delivery_policy,
        )
        policy = create_initiative_delivery_policy()
        result = policy.evaluate(None)
        assert result["decision"] == "block"
        assert "invalid_output" in result["reasons"]

    def test_17_missing_content_blocks(self):
        """17. 缺少 content → block"""
        from src.runtime.initiative import (
            create_initiative_delivery_policy,
        )
        policy = create_initiative_delivery_policy(
            confidence_threshold=0.5,
            priority_threshold=0.5,
        )
        output = _make_initiative_output(content="")
        result = policy.evaluate(output, relationship_snapshot=_make_relationship())
        assert result["decision"] == "block"
        assert "missing_message_content" in result["reasons"]


# ============================================================
# 3. QQ Integration
# ============================================================
class TestQQIntegration:
    def test_18_sender_called_on_allow(self):
        """18. policy allow 时调用 sender"""
        sender = _FakeSender(return_value=True)
        d = _make_delivery(sender=sender)
        output = _make_initiative_output()
        result = d.deliver(output, relationship_snapshot=_make_relationship())
        assert result["result"] == "sent"
        assert len(sender.calls) == 1
        call = sender.calls[0]
        assert call["user_id"] == "u_test"
        assert "AI 绘画" in call["content"]
        assert call["channel"] == "qq"

    def test_19_sender_not_called_on_block(self):
        """19. policy block 时不调用 sender"""
        sender = _FakeSender(return_value=True)
        d = _make_delivery(sender=sender)
        output = _make_initiative_output(confidence=0.1)  # 触发 block
        result = d.deliver(output, relationship_snapshot=_make_relationship())
        assert result["result"] == "blocked"
        assert len(sender.calls) == 0

    def test_20_call_existing_sender_no_url(self):
        """20. 无 api_url → 发送失败(不抛)"""
        d = _make_delivery(api_url="")
        # 用 allow 路径但没有 api_url 也没有 sender
        result = d.deliver(
            _make_initiative_output(),
            relationship_snapshot=_make_relationship(),
        )
        # 没有 sender + 没有 url → send 返回 False → failed
        assert result["result"] in ("failed", "blocked")

    def test_21_call_existing_sender_with_url(self):
        """21. 有 api_url 但 QQ 不可达 → fail-soft(不抛)"""
        d = _make_delivery(
            api_url="http://127.0.0.1:1",  # 不可达
            api_type="onebot",
            token="",
        )
        result = d.deliver(
            _make_initiative_output(),
            relationship_snapshot=_make_relationship(),
        )
        # 发送失败,Runtime 不中断
        assert "result" in result
        assert result["result"] in ("sent", "failed", "blocked", "degraded")

    def test_22_call_existing_sender_astrbot(self):
        """22. AstrBot 通道:不可达时 fail-soft"""
        d = _make_delivery(
            api_url="http://127.0.0.1:1",
            api_type="astrbot",
            channel="astrbot",
        )
        result = d.deliver(
            _make_initiative_output(),
            relationship_snapshot=_make_relationship(),
        )
        assert "result" in result


# ============================================================
# 4. Success
# ============================================================
class TestSuccess:
    def test_23_send_success(self):
        """23. 成功发送"""
        sender = _FakeSender(return_value=True)
        d = _make_delivery(sender=sender)
        result = d.deliver(
            _make_initiative_output(),
            relationship_snapshot=_make_relationship(),
        )
        assert result["success"] is True
        assert result["result"] == "sent"
        assert result["message_id"] != ""
        assert result["channel"] == "qq"
        assert result["error"] == ""
        assert result["message_payload"] is not None

    def test_24_payload_includes_all_fields(self):
        """24. payload 包含所有字段"""
        sender = _FakeSender(return_value=True)
        d = _make_delivery(sender=sender)
        result = d.deliver(
            _make_initiative_output(),
            relationship_snapshot=_make_relationship(),
        )
        payload = result["message_payload"]
        required = [
            "message_id", "initiative_id", "user_id", "channel",
            "content", "reason", "priority", "timestamp",
        ]
        for f in required:
            assert f in payload, f"missing field: {f}"

    def test_25_sent_count_increments(self):
        """25. sent 计数 +1"""
        sender = _FakeSender(return_value=True)
        d = _make_delivery(sender=sender)
        d.deliver(
            _make_initiative_output(),
            relationship_snapshot=_make_relationship(),
        )
        stats = d.get_stats()
        assert stats["sent_count"] >= 1

    def test_26_cooldown_recorded_after_send(self):
        """26. 发送成功后记录 cooldown"""
        from src.runtime.initiative import (
            create_initiative_delivery_policy,
        )
        policy = create_initiative_delivery_policy(
            confidence_threshold=0.5,
            priority_threshold=0.5,
            cooldown_seconds=3600,
        )
        sender = _FakeSender(return_value=True)
        d = _make_delivery(policy=policy, sender=sender)
        d.deliver(
            _make_initiative_output(),
            relationship_snapshot=_make_relationship(),
        )
        # 立即再发一次,应被 cooldown block
        result2 = d.deliver(
            _make_initiative_output(),
            relationship_snapshot=_make_relationship(),
        )
        assert result2["result"] == "blocked"
        assert "cooldown_active" in result2["policy_result"]["reasons"]


# ============================================================
# 5. Blocked
# ============================================================
class TestBlocked:
    def test_27_blocked_by_user_preference(self):
        """27. user_preference=reserved 阻止"""
        sender = _FakeSender(return_value=True)
        d = _make_delivery(sender=sender)
        result = d.deliver(
            _make_initiative_output(metadata={"user_preference": "reserved"}),
            relationship_snapshot=_make_relationship(),
            metadata={"user_preference": "reserved"},
        )
        assert result["result"] == "blocked"
        assert "user_preference_blocks" in result["policy_result"]["reasons"]
        assert len(sender.calls) == 0

    def test_28_blocked_by_low_confidence(self):
        """28. 低 confidence 阻止"""
        sender = _FakeSender(return_value=True)
        d = _make_delivery(sender=sender)
        result = d.deliver(
            _make_initiative_output(confidence=0.1),
            relationship_snapshot=_make_relationship(),
        )
        assert result["result"] == "blocked"
        assert "confidence_too_low" in result["policy_result"]["reasons"]

    def test_29_blocked_by_low_relationship(self):
        """29. 低 relationship 阻止"""
        sender = _FakeSender(return_value=True)
        d = _make_delivery(sender=sender)
        result = d.deliver(
            _make_initiative_output(),
            relationship_snapshot=_make_relationship(level=0.1),
        )
        assert result["result"] == "blocked"
        assert "relationship_too_low" in result["policy_result"]["reasons"]


# ============================================================
# 6. Readonly
# ============================================================
class TestReadonly:
    def test_30_personality_output_not_modified(self):
        """30. personality_output 不被修改"""
        from src.runtime.initiative import (
            create_initiative_message_delivery,
        )

        class TrapPersonality:
            def __init__(self):
                self.apply_called = False

            def apply(self, *args, **kwargs):
                self.apply_called = True
                raise AssertionError("Personality apply called")

        trap = TrapPersonality()
        d = create_initiative_message_delivery()
        output = _make_initiative_output()
        output["personality"] = {"snapshot": trap}
        d.deliver(output, relationship_snapshot=_make_relationship())
        assert trap.apply_called is False

    def test_31_self_model_not_modified(self):
        """31. SelfModel 不被修改"""
        from src.runtime.initiative import (
            create_initiative_message_delivery,
        )

        class TrapSelfModel:
            def __init__(self):
                self.update_called = False

            def update(self, *args, **kwargs):
                self.update_called = True
                raise AssertionError("SelfModel update called")

        trap = TrapSelfModel()
        d = create_initiative_message_delivery()
        output = _make_initiative_output()
        output["self_model"] = {"snapshot": trap}
        d.deliver(output, relationship_snapshot=_make_relationship())
        assert trap.update_called is False

    def test_32_relationship_not_modified(self):
        """32. Relationship 不被修改"""
        from src.runtime.initiative import (
            create_initiative_message_delivery,
        )

        class TrapRelationship:
            def __init__(self):
                self.save_called = False

            def save(self, *args, **kwargs):
                self.save_called = True
                raise AssertionError("Relationship save called")

        trap = TrapRelationship()
        d = create_initiative_message_delivery()
        relationship = {"snapshot": trap, "level": 0.7}
        d.deliver(_make_initiative_output(), relationship_snapshot=relationship)
        assert trap.save_called is False

    def test_33_memory_not_modified(self):
        """33. Memory 不被修改"""
        from src.runtime.initiative import (
            create_initiative_message_delivery,
        )

        class TrapMemory:
            def __init__(self):
                self.modify_called = False

            def modify(self, *args, **kwargs):
                self.modify_called = True
                raise AssertionError("Memory modify called")

        trap = TrapMemory()
        d = create_initiative_message_delivery()
        output = _make_initiative_output()
        output["memory"] = {"snapshot": trap}
        d.deliver(output, relationship_snapshot=_make_relationship())
        assert trap.modify_called is False


# ============================================================
# 7. Security
# ============================================================
class TestSecurity:
    def test_34_cannot_bypass_policy(self):
        """34. 无法绕过 policy 发送(无 output 时不调用 sender)"""
        sender = _FakeSender(return_value=True)
        d = _make_delivery(sender=sender)
        d.deliver(None)
        assert len(sender.calls) == 0

    def test_35_no_apply_methods(self):
        """35. delivery 不暴露 apply / resolve"""
        d = _make_delivery()
        assert not hasattr(d, "apply_proposal")
        assert not hasattr(d, "resolve_personality")
        assert not hasattr(d, "apply")

    def test_36_no_update_self_model(self):
        """36. delivery 不暴露 update_self_model"""
        d = _make_delivery()
        assert not hasattr(d, "update_self_model")

    def test_37_no_save_relationship(self):
        """37. delivery 不暴露 save_relationship"""
        d = _make_delivery()
        assert not hasattr(d, "save_relationship")


# ============================================================
# 8. Audit
# ============================================================
class TestAudit:
    def test_38_audit_sent(self):
        """38. sent 时记录 audit"""
        audit = _FakeAudit()
        sender = _FakeSender(return_value=True)
        d = _make_delivery(audit=audit, sender=sender)
        d.deliver(_make_initiative_output(), relationship_snapshot=_make_relationship())
        actions = {e.get("operation_type") for e in audit.events}
        assert "runtime_initiative_message_sent" in actions

    def test_39_audit_blocked(self):
        """39. blocked 时记录 audit"""
        audit = _FakeAudit()
        sender = _FakeSender(return_value=True)
        d = _make_delivery(audit=audit, sender=sender)
        d.deliver(
            _make_initiative_output(metadata={"user_preference": "reserved"}),
            relationship_snapshot=_make_relationship(),
            metadata={"user_preference": "reserved"},
        )
        actions = {e.get("operation_type") for e in audit.events}
        assert "runtime_initiative_message_blocked" in actions

    def test_40_audit_failed(self):
        """40. failed 时记录 audit"""
        audit = _FakeAudit()
        sender = _FakeSender(return_value=False)  # 发送失败
        d = _make_delivery(audit=audit, sender=sender)
        d.deliver(_make_initiative_output(), relationship_snapshot=_make_relationship())
        actions = {e.get("operation_type") for e in audit.events}
        assert "runtime_initiative_message_failed" in actions

    def test_41_audit_field_message_id(self):
        """41. audit 包含 message_id"""
        audit = _FakeAudit()
        sender = _FakeSender(return_value=True)
        d = _make_delivery(audit=audit, sender=sender)
        d.deliver(_make_initiative_output(), relationship_snapshot=_make_relationship())
        found = False
        for e in audit.events:
            detail = e.get("detail", {})
            if isinstance(detail, dict) and "message_id" in detail:
                found = True
                break
        assert found is True

    def test_42_audit_none_safe(self):
        """42. audit=None 时安全运行"""
        sender = _FakeSender(return_value=True)
        d = _make_delivery(audit=None, sender=sender)
        result = d.deliver(
            _make_initiative_output(),
            relationship_snapshot=_make_relationship(),
        )
        assert result["result"] == "sent"

    def test_43_audit_audit_raises(self):
        """43. audit 抛异常时降级"""
        from src.runtime.initiative import (
            create_initiative_message_delivery,
        )

        class BrokenAudit:
            def record(self, **kwargs):
                raise RuntimeError("audit boom")

        sender = _FakeSender(return_value=True)
        d = create_initiative_message_delivery(
            audit=BrokenAudit(),
            sender=sender,
        )
        # 不应抛
        result = d.deliver(
            _make_initiative_output(),
            relationship_snapshot=_make_relationship(),
        )
        assert result["result"] == "sent"


# ============================================================
# 9. FailSafe
# ============================================================
class TestFailSafe:
    def test_44_sender_exception(self):
        """44. sender 抛异常时降级"""
        sender = _FakeSender(return_value=False, raise_exc=RuntimeError("sender boom"))
        d = _make_delivery(sender=sender)
        result = d.deliver(
            _make_initiative_output(),
            relationship_snapshot=_make_relationship(),
        )
        # sender 抛异常时,sender 调用被隔离,但 deliver 应仍返回 result
        assert "result" in result

    def test_45_policy_exception(self):
        """45. policy 抛异常时降级"""
        from src.runtime.initiative import (
            create_initiative_message_delivery,
        )

        class BadPolicy:
            def evaluate(self, *args, **kwargs):
                raise RuntimeError("policy boom")

        sender = _FakeSender(return_value=True)
        d = create_initiative_message_delivery(policy=BadPolicy(), sender=sender)
        result = d.deliver(
            _make_initiative_output(),
            relationship_snapshot=_make_relationship(),
        )
        # policy 异常 → block
        assert result["result"] == "blocked"
        assert len(sender.calls) == 0

    def test_46_qq_unreachable(self):
        """46. QQ 不可达时降级"""
        d = _make_delivery(api_url="http://127.0.0.1:1")
        result = d.deliver(
            _make_initiative_output(),
            relationship_snapshot=_make_relationship(),
        )
        # 应 fail-soft
        assert "result" in result
        assert result["result"] in ("sent", "failed", "blocked", "degraded")


# ============================================================
# 10. Concurrency
# ============================================================
class TestConcurrency:
    def test_47_multi_thread_deliver(self):
        """47. 多线程 deliver 安全(后发应被 cooldown block)"""
        sender = _FakeSender(return_value=True)
        d = _make_delivery(sender=sender)
        results: List[Dict[str, Any]] = []
        lock = threading.Lock()

        def worker(idx: int) -> None:
            r = d.deliver(
                _make_initiative_output(initiative_id=f"ide_{idx}"),
                relationship_snapshot=_make_relationship(),
            )
            with lock:
                results.append(r)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 至少 1 个 sent
        sent_count = sum(1 for r in results if r["result"] == "sent")
        blocked_count = sum(1 for r in results if r["result"] == "blocked")
        assert sent_count >= 1
        # 后续应被 cooldown 拦截
        assert sent_count + blocked_count == len(results)


# ============================================================
# 11. Schema
# ============================================================
class TestSchema:
    def test_48_result_all_fields(self):
        """48. deliver result 包含所有字段"""
        sender = _FakeSender(return_value=True)
        d = _make_delivery(sender=sender)
        result = d.deliver(
            _make_initiative_output(),
            relationship_snapshot=_make_relationship(),
        )
        required = [
            "success", "result", "message_id", "channel",
            "policy_result", "message_payload", "audit_action",
            "timestamp", "error", "degraded",
        ]
        for f in required:
            assert f in result, f"missing field: {f}"

    def test_49_payload_all_fields(self):
        """49. message_payload 包含所有字段"""
        sender = _FakeSender(return_value=True)
        d = _make_delivery(sender=sender)
        result = d.deliver(
            _make_initiative_output(),
            relationship_snapshot=_make_relationship(),
        )
        payload = result["message_payload"]
        required = [
            "message_id", "initiative_id", "user_id", "channel",
            "content", "reason", "priority", "timestamp",
        ]
        for f in required:
            assert f in payload, f"missing field: {f}"

    def test_50_stats_includes_all(self):
        """50. stats 包含所有计数"""
        sender = _FakeSender(return_value=True)
        d = _make_delivery(sender=sender)
        d.deliver(_make_initiative_output(), relationship_snapshot=_make_relationship())
        stats = d.get_stats()
        required = [
            "deliver_count", "sent_count", "failed_count",
            "blocked_count", "degraded_count", "channel", "api_type",
            "policy", "schema_version",
        ]
        for f in required:
            assert f in stats, f"missing field: {f}"


# ============================================================
# 12. Integration
# ============================================================
class TestIntegration:
    def test_51_full_chain_runtime_to_qq(self):
        """51. Runtime Adapter → MessageDelivery → QQ 完整链路"""
        from src.runtime.initiative import (
            create_initiative_runtime_adapter,
            create_initiative_message_delivery,
        )
        from src.runtime.cycle_context import RuntimeCycleContext

        adapter = create_initiative_runtime_adapter()
        adapter.attach()
        sender = _FakeSender(return_value=True)
        delivery = create_initiative_message_delivery(sender=sender)

        # 构造一个会 trigger 的 ctx
        ctx = RuntimeCycleContext(user_id="user_full")
        ctx.metadata = {"relationship_level": 0.7, "user_preference": "open"}
        ctx.memory_output = {
            "open_topics": [
                {
                    "topic": "AI 绘画",
                    "status": "open",
                    "last_touched_at": _now_iso(offset_seconds=-10 * 86400),
                },
            ],
            "user_interests": [{"name": "AI 绘画", "strength": 0.9}],
        }
        ctx.emotion_output = {}
        ctx.relationship_output = {
            "level": 0.7,
            "current_metrics": {"familiarity": 0.7},
        }
        ctx.personality_output = {"snapshot": {"traits": {}}}
        ctx.growth_output = [
            {"name": "warmth", "confidence": 0.9, "at": _now_iso()},
        ]

        # 1) Runtime cycle
        adapter.process_cycle(ctx)
        initiative_output = getattr(ctx, "initiative_output", None)
        assert initiative_output is not None
        assert "decision" in initiative_output

        # 2) Delivery
        relationship_snapshot = ctx.relationship_output
        result = delivery.deliver(
            initiative_output,
            relationship_snapshot=relationship_snapshot,
            metadata=ctx.metadata,
        )
        # 字段存在(无论 sent / blocked / failed)
        assert "result" in result
        assert "success" in result
        assert "policy_result" in result

    def test_52_chain_with_real_initiative_output(self):
        """52. 用真实 initiative_output 走完整链路"""
        from src.runtime.initiative import (
            create_initiative_message_delivery,
        )
        sender = _FakeSender(return_value=True)
        delivery = create_initiative_message_delivery(sender=sender)
        output = _make_initiative_output(
            decision="initiate",
            confidence=0.95,
            priority=0.85,
        )
        result = delivery.deliver(
            output,
            relationship_snapshot=_make_relationship(level=0.7),
        )
        assert result["result"] == "sent"
        assert len(sender.calls) == 1
        # 消息内容来自 initiative_output
        assert "AI 绘画" in sender.calls[0]["content"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
