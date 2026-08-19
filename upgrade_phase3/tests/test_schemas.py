from src.growth.schemas import Evidence, NormalizedEvent


def test_normalized_event_clamp_and_defaults():
    e = Evidence(text="测试", role="user", source_index=0, memory_id="mem_123")
    ne = NormalizedEvent(topic="给羽依命名", event_type="identity", evidence=[e], source_ids=["mem_123"])
    assert ne.event_id.startswith("evt_")
    assert 0.0 <= ne.importance <= 1.0
    assert 0.0 <= ne.confidence <= 1.0
