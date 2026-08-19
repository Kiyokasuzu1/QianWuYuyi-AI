"""
MemoryVerifier v4.3.2 单元测试

覆盖:
- 普通用户消息分类
- Extractor 候选采纳与降级
- identity 严格性
- 事件提取要求动作词
- relationship 识别
- instruction 检测（软指令 + 记忆请求不误判）
- assistant 零信任
- 输入防御
- 接口方法
"""

import pytest
from src.memory.memory_verifier import MemoryVerifier


# ============================================================
# 基础分类测试
# ============================================================

def test_preference_detection():
    """用户偏好识别"""
    v = MemoryVerifier()
    res = v.verify({"role": "user", "content": "我喜欢AI绘画"})
    assert res["memory_class"] == "preference"
    assert res["truth"] == 0.85
    assert "growth" in res["usage"]


def test_identity_by_name():
    """我叫... 身份识别"""
    v = MemoryVerifier()
    res = v.verify({"role": "user", "content": "我叫清夏铃"})
    assert res["memory_class"] == "identity"
    assert res["truth"] == 1.0


def test_identity_by_profession():
    """我是... 身份识别（需要身份提示词）"""
    v = MemoryVerifier()
    res = v.verify({"role": "user", "content": "我是程序员"})
    assert res["memory_class"] == "identity"


def test_identity_reject_relation():
    """我是你的朋友 → relationship 而不是 identity"""
    v = MemoryVerifier()
    res = v.verify({"role": "user", "content": "我是羽依的朋友"})
    assert res["memory_class"] == "relationship"
    assert res["truth"] == 0.85


def test_identity_reject_opinion():
    """观点不能成为身份"""
    v = MemoryVerifier()
    res = v.verify({"role": "user", "content": "我是觉得今天很累"})
    assert res["memory_class"] == "user_statement"
    assert res["truth"] == 0.6


def test_event_with_time_and_action():
    """时间+动作 → 事件"""
    v = MemoryVerifier()
    res = v.verify({"role": "user", "content": "昨天完成了AI项目"})
    assert res["memory_class"] == "event"
    assert res["truth"] == 0.8


def test_event_no_action():
    """仅时间无动作 → 用户陈述"""
    v = MemoryVerifier()
    res = v.verify({"role": "user", "content": "昨天太阳很好"})
    assert res["memory_class"] == "user_statement"


def test_relationship_extraction():
    """关系识别"""
    v = MemoryVerifier()
    res = v.verify({"role": "user", "content": "以后叫我哥哥"})
    assert res["memory_class"] == "relationship"


def test_instruction_detection():
    """行为指令识别（软指令）"""
    v = MemoryVerifier()
    res = v.verify({"role": "user", "content": "羽依以后回答的时候温柔一点"})
    assert res["memory_class"] == "instruction"
    assert "persona" in res["usage"]


def test_memory_request_not_instruction():
    """记住用户信息不应成为人格指令"""
    v = MemoryVerifier()
    res = v.verify({"role": "user", "content": "羽依以后记住我是喜欢AI绘画的人"})
    assert res["memory_class"] != "instruction"


def test_user_statement_fallback():
    """普通聊天 → 用户陈述"""
    v = MemoryVerifier()
    res = v.verify({"role": "user", "content": "今天天气不错"})
    assert res["memory_class"] == "user_statement"


def test_long_source_document():
    """长文本 → source_document，parse_status=pending"""
    v = MemoryVerifier()
    long_text = "今天" + "很" * 800   # 长度超过 800
    res = v.verify({"role": "user", "content": long_text})
    assert res["memory_class"] == "source_document"
    assert res["parse_status"] == "pending"


# ============================================================
# Extractor 联动测试
# ============================================================

def test_extractor_candidate_accepted():
    """Extractor 候选被采纳，且 confidence 保留"""
    v = MemoryVerifier()
    memory = {
        "role": "user",
        "content": "我喜欢AI绘画",
        "memory_class": "preference",
        "confidence": 0.72
    }
    res = v.verify(memory)
    assert res["memory_class"] == "preference"
    assert res["confidence"] == 0.72
    assert res["truth"] == 0.85


def test_extractor_fake_identity_rejected():
    """Extractor 标记 identity 但内容不符 → 降级"""
    v = MemoryVerifier()
    memory = {
        "role": "user",
        "content": "我是一个孤独的人",
        "memory_class": "identity"
    }
    res = v.verify(memory)
    assert res["memory_class"] == "user_statement"
    assert res["truth"] == 0.6


def test_extractor_event_rejected():
    """Extractor 标记 event 但无动作词 → 降级"""
    v = MemoryVerifier()
    memory = {
        "role": "user",
        "content": "昨天天气真好",
        "memory_class": "event"
    }
    res = v.verify(memory)
    assert res["memory_class"] == "user_statement"


# ============================================================
# 安全测试
# ============================================================

def test_assistant_zero_trust():
    """AI 回复零信任"""
    v = MemoryVerifier()
    res = v.verify({"role": "assistant", "content": "你喜欢AI绘画"})
    assert res["memory_class"] == "assistant_output"
    assert res["truth"] == 0.0
    assert "growth" not in res["usage"]


def test_legacy_string_input():
    """纯文本输入被标记为 unknown"""
    v = MemoryVerifier()
    res = v.verify("我是一段纯文本")
    assert res["memory_class"] == "unknown"


def test_invalid_dict_input():
    """非标准格式安全"""
    v = MemoryVerifier()
    res = v.verify(123)
    assert res["memory_class"] == "unknown"


def test_evidence_type_protection():
    """非列表 evidence 被强制清空"""
    v = MemoryVerifier()
    memory = {
        "role": "user",
        "content": "我喜欢AI绘画",
        "evidence": "非法证据"
    }
    res = v.verify(memory)
    assert res["evidence"] == []


def test_confidence_default():
    """无 confidence 时默认等于 truth"""
    v = MemoryVerifier()
    memory = {
        "role": "user",
        "content": "我喜欢AI绘画",
        "memory_class": "preference"
    }
    res = v.verify(memory)
    assert res["confidence"] == 0.85


# ============================================================
# 接口测试
# ============================================================

def test_verify_all():
    """批量审查"""
    v = MemoryVerifier()
    msgs = [
        {"role": "user", "content": "我喜欢AI绘画"},
        {"role": "assistant", "content": "好的"}
    ]
    results = v.verify_all(msgs)
    assert len(results) == 2
    assert results[0]["memory_class"] == "preference"
    assert results[1]["memory_class"] == "assistant_output"


def test_is_usable_for():
    """检查用途权限"""
    v = MemoryVerifier()
    mem = v.verify({"role": "user", "content": "我喜欢AI绘画"})
    assert v.is_usable_for(mem, "growth") is True
    # user_statement 不能用于 growth
    mem2 = v.verify({"role": "user", "content": "今天天气不错"})
    assert v.is_usable_for(mem2, "growth") is False


def test_get_truth():
    """获取可信度"""
    v = MemoryVerifier()
    truth = v.get_truth({"role": "user", "content": "我喜欢AI绘画"})
    assert truth == 0.85