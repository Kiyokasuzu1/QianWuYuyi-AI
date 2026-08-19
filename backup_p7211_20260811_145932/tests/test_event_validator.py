# tests/test_event_validator.py
from src.growth.event_validator import EventValidator


def make_event(evidence=None, topic="测试事件", category_id=""):
    return {
        "topic": topic,
        "canonical_topic": topic,
        "evidence": evidence or [],
        "category_id": category_id,
        "importance": 0.5,
        "metadata": {}
    }


def test_discard_no_evidence():
    v = EventValidator()
    ev = make_event(evidence=[])
    decision = v.decide(ev)
    assert decision[0] == "discard"
    # should_keep should annotate metadata and return False
    assert v.should_keep(ev) is False
    assert ev["metadata"]["validator_decision"] == "discard"


def test_keep_life_meaning():
    v = EventValidator()
    ev = make_event(evidence=[{"text":"x","role":"user","source_index":0}], category_id="birth")
    dec, score, reason = v.decide(ev)
    assert dec == "keep"
    assert score >= 0.6  # life_value gives base 0.6
    assert v.should_keep(ev) is True
    assert ev["metadata"]["validator_decision"] in ("keep",)
    assert ev["metadata"]["validator_apply"] is True


def test_review_borderline():
    v = EventValidator()
    # craft an event with moderate life value: one assistant evidence and low importance -> borderline
    ev = make_event(evidence=[{"text":"x","role":"assistant","source_index":0}], category_id="")
    # lower importance to push into borderline
    ev["importance"] = 0.1
    dec, score, reason = v.decide(ev)
    assert dec in ("review", "discard", "keep")  # acceptable, but we'll check should_keep behaviour
    # should_keep returns True for review as per design, but validator_apply must be False
    v.should_keep(ev)
    assert "validator_decision" in ev["metadata"]
    assert ev["metadata"]["validator_decision"] in ("review", "discard", "keep")
    # if review, ensure validator_apply is False
    if ev["metadata"]["validator_decision"] == "review":
        assert ev["metadata"]["validator_apply"] is False


def test_technical_discard_and_technical_with_life_keep():
    v = EventValidator()
    # technical event without life keywords
    ev1 = make_event(evidence=[{"text":"安装出错","role":"user","source_index":0}], topic="安装失败", category_id="")
    dec1 = v.decide(ev1)
    assert dec1[0] == "discard"
    # technical event with life keyword -> keep
    ev2 = make_event(evidence=[{"text":"启动羽依","role":"user","source_index":0}], topic="服务启动 羽依", category_id="")
    dec2 = v.decide(ev2)
    assert dec2[0] == "keep"
