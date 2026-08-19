"""
羽依 MemoryExtractor v1.2.1 验收测试

覆盖:
- 基础安全
- 偏好提取
- 身份隔离
- 事件提取
- 关系提取
- 情绪候选
- 多候选输出
- 去重
"""

from src.memory.memory_extractor import MemoryExtractor


# ============================================================
# 基础安全
# ============================================================

def test_assistant_not_extracted():
    """assistant 永不产生候选"""
    extractor = MemoryExtractor()

    result = extractor.extract([
        {
            "role": "assistant",
            "content": "你喜欢AI绘画"
        }
    ])

    assert result == []


def test_empty_message_safe():
    """空消息安全"""
    extractor = MemoryExtractor()

    result = extractor.extract([
        {
            "role": "user",
            "content": ""
        }
    ])

    assert result == []


def test_invalid_message_safe():
    """缺少content安全"""
    extractor = MemoryExtractor()

    result = extractor.extract([
        {
            "role": "user"
        }
    ])

    assert result == []


# ============================================================
# Preference
# ============================================================

def test_preference_detection():
    """喜欢类偏好提取"""
    extractor = MemoryExtractor()

    result = extractor.extract([
        {
            "role": "user",
            "content": "我喜欢AI绘画"
        }
    ])

    assert len(result) == 1

    memory = result[0]

    assert memory.memory_class == "preference"
    assert memory.metadata["target"] == "AI绘画"


def test_preference_filter():
    """普通生活行为不进入长期偏好"""
    extractor = MemoryExtractor()

    result = extractor.extract([
        {
            "role": "user",
            "content": "我喜欢吃饭"
        }
    ])

    assert result == []


# ============================================================
# Identity
# ============================================================

def test_identity_name():
    """名字识别"""
    extractor = MemoryExtractor()

    result = extractor.extract([
        {
            "role": "user",
            "content": "我叫清夏铃"
        }
    ])

    assert len(result) == 1
    assert result[0].memory_class == "identity"


def test_identity_profession():
    """身份职业识别"""
    extractor = MemoryExtractor()

    result = extractor.extract([
        {
            "role": "user",
            "content": "我是程序员"
        }
    ])

    assert len(result) == 1
    assert result[0].memory_class == "identity"


def test_identity_reject_relationship():
    """关系不能进入身份（但可产生 relationship 候选）"""
    extractor = MemoryExtractor()

    result = extractor.extract([
        {
            "role": "user",
            "content": "我是羽依的朋友"
        }
    ])

    # 重点是确保没有 identity 候选
    classes = {r.memory_class for r in result}
    assert "identity" not in classes


def test_identity_reject_opinion():
    """观点不是身份"""
    extractor = MemoryExtractor()

    result = extractor.extract([
        {
            "role": "user",
            "content": "我是觉得今天很累"
        }
    ])

    assert result == []


# ============================================================
# Event
# ============================================================

def test_event_with_time():
    """时间+动作事件"""
    extractor = MemoryExtractor()

    result = extractor.extract([
        {
            "role": "user",
            "content": "昨天完成了AI项目"
        }
    ])

    assert len(result) == 1

    memory = result[0]

    assert memory.memory_class == "event"
    assert memory.metadata["time_hint"] == "昨天"


def test_event_state_change():
    """时间+情绪状态事件"""
    extractor = MemoryExtractor()

    result = extractor.extract([
        {
            "role": "user",
            "content": "昨天很难受"
        }
    ])

    assert len(result) == 1
    assert result[0].memory_class == "event"


# ============================================================
# Relationship
# ============================================================

def test_relationship_nickname():
    """称呼关系"""
    extractor = MemoryExtractor()

    result = extractor.extract([
        {
            "role": "user",
            "content": "以后叫我哥哥"
        }
    ])

    assert len(result) == 1

    assert result[0].memory_class == "relationship"


def test_relationship_false_positive():
    """普通动作不能误判关系"""
    extractor = MemoryExtractor()

    result = extractor.extract([
        {
            "role": "user",
            "content": "叫我去吃饭"
        }
    ])

    assert result == []


# ============================================================
# Emotion
# ============================================================

def test_emotion_candidate():
    """情绪候选"""
    extractor = MemoryExtractor()

    result = extractor.extract([
        {
            "role": "user",
            "content": "我特别开心"
        }
    ])

    assert len(result) == 1

    memory = result[0]

    assert memory.memory_class == "emotion_candidate"

    assert memory.metadata["emotion"] == "开心"
    assert memory.metadata["intensity"] == "high"


# ============================================================
# Multiple candidates
# ============================================================

def test_multiple_candidates_same_message():
    """
    一句话产生多个候选
    """
    extractor = MemoryExtractor()

    result = extractor.extract([
        {
            "role": "user",
            "content": "我喜欢AI绘画，昨天完成了作品，感觉特别开心"
        }
    ])

    classes = {
        item.memory_class
        for item in result
    }

    assert "preference" in classes
    assert "event" in classes
    assert "emotion_candidate" in classes


# ============================================================
# Deduplicate
# ============================================================

def test_deduplicate():
    """
    同内容同类型只保留一个
    """
    extractor = MemoryExtractor()

    result = extractor.extract([
        {
            "role": "user",
            "content": "我喜欢AI绘画"
        },
        {
            "role": "user",
            "content": "我喜欢AI绘画"
        }
    ])

    assert len(result) == 1