"""
Phase 10.3：关系事件提取器测试（修正外部关系测试）
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.relationship.relationship_event_extractor import RelationshipEventExtractor


def test_ordinary_chat_returns_none():
    extractor = RelationshipEventExtractor()
    assert extractor.extract("今天吃什么") is None
    assert extractor.extract("天气不错") is None
    assert extractor.extract("好") is None


def test_collaboration_detected():
    extractor = RelationshipEventExtractor()
    event = extractor.extract("我们一起开发这个项目已经半年了，合作很愉快", evidence_id="mem_001")
    assert event is not None
    assert event.event_type == "collaboration"
    assert "collaboration" in event.potential_dimensions
    assert "mem_001" in event.evidence_ids


def test_trust_building_detected():
    extractor = RelationshipEventExtractor()
    event = extractor.extract("我很信任你的判断", evidence_id="mem_002")
    assert event is not None
    assert event.event_type == "trust_building"
    assert "trust" in event.potential_dimensions


def test_boundary_respect_detected():
    extractor = RelationshipEventExtractor()
    event = extractor.extract("我尊重你的选择，不勉强你", evidence_id="mem_003")
    assert event is not None
    assert event.event_type == "boundary_respect"


def test_short_message_ignored():
    extractor = RelationshipEventExtractor()
    assert extractor.extract("好") is None
    assert extractor.extract("嗯") is None


def test_event_does_not_contain_delta():
    extractor = RelationshipEventExtractor()
    event = extractor.extract("我们一起合作很久了", evidence_id="mem_004")
    assert event is not None
    assert isinstance(event.potential_dimensions, set)
    assert all(isinstance(d, str) for d in event.potential_dimensions)


def test_emotional_claim_not_misinterpreted():
    extractor = RelationshipEventExtractor()
    event = extractor.extract("你是我最重要的人")
    assert event is None


def test_external_relationship_not_detected():
    """非羽依相关的关系不应触发事件——消息不含主体词（你/羽依/我们/一起/我帮你）"""
    extractor = RelationshipEventExtractor()
    # “他们”不在主体词列表中，且不含“你/羽依/我们/一起/我帮你”
    event = extractor.extract("他们合作开发项目")
    assert event is None


def test_event_id_stable():
    extractor = RelationshipEventExtractor()
    a = extractor.extract("我们一起开发羽依项目")
    b = extractor.extract("我们一起开发羽依项目")
    assert a is not None
    assert b is not None
    assert a.event_id == b.event_id


if __name__ == "__main__":
    test_ordinary_chat_returns_none()
    print("✅ 1/9 普通聊天过滤")
    test_collaboration_detected()
    print("✅ 2/9 合作事件识别")
    test_trust_building_detected()
    print("✅ 3/9 信任事件识别")
    test_boundary_respect_detected()
    print("✅ 4/9 边界尊重识别")
    test_short_message_ignored()
    print("✅ 5/9 短消息过滤")
    test_event_does_not_contain_delta()
    print("✅ 6/9 事件不包含变化量")
    test_emotional_claim_not_misinterpreted()
    print("✅ 7/9 情感表达不误判")
    test_external_relationship_not_detected()
    print("✅ 8/9 非羽依关系过滤")
    test_event_id_stable()
    print("✅ 9/9 稳定 event_id")
    print("\n🎉 Phase 10.3 全部通过")