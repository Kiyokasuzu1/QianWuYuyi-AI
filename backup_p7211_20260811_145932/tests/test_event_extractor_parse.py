# tests/test_event_extractor_parse.py
import os
import json
from src.growth.event_extractor import EventExtractor

def make_memories():
    # minimal memory structure expected by EventExtractor
    return [
        {"id": "mem_0", "role": "user", "content": "用户说: 我喜欢羽依"},
        {"id": "mem_1", "role": "assistant", "content": "羽依回复: 谢谢"}
    ]

def test_parse_standard_json(tmp_path):
    extractor = EventExtractor()
    memories = make_memories()
    result = json.dumps({
        "events": [
            {
                "event": "第一次情感表达",
                "topic": "用户表达爱意",
                "event_type": "relationship",
                "evidence": [
                    {"text": "用户说: 我喜欢羽依", "role": "user", "source_index": 0}
                ]
            }
        ]
    }, ensure_ascii=False)
    events = extractor.parse_result(result, memories, prompt="p")
    assert isinstance(events, list)
    assert len(events) == 1
    ev = events[0]
    assert ev.get("topic") == "用户表达爱意"
    assert "evidence" in ev
    assert ev["evidence"][0]["text"].startswith("用户说")

def test_parse_embedded_json(tmp_path):
    extractor = EventExtractor()
    memories = make_memories()
    json_payload = json.dumps({
        "events": [
            {
                "event": "第一次情感表达",
                "topic": "用户表达爱意",
                "event_type": "relationship",
                "evidence": [
                    {"text": "用户说: 我喜欢羽依", "role": "user", "source_index": 0}
                ]
            }
        ]
    }, ensure_ascii=False)
    # prepend/append explanation text to simulate verbose LLM output
    result = "LLM说明: 以下是提取结果:\n" + json_payload + "\n以上是结束。"
    events = extractor.parse_result(result, memories, prompt="p")
    assert isinstance(events, list)
    assert len(events) == 1

def test_non_json_writes_failure_and_returns_empty(tmp_path):
    extractor = EventExtractor()
    memories = make_memories()
    result = "这是无法解析的文本：没有JSON结构"
    # ensure failures file absent initially
    fail_dir = os.path.join("data", "llm_failures")
    os.makedirs(fail_dir, exist_ok=True)
    fail_file = os.path.join(fail_dir, "failures.jsonl")
    # clean up before test
    if os.path.exists(fail_file):
        os.remove(fail_file)
    events = extractor.parse_result(result, memories, prompt="p")
    assert events == []  # safe empty return
    # failures file should be created with at least one line
    assert os.path.exists(fail_file)
    with open(fail_file, "r", encoding="utf-8") as f:
        lines = [l for l in f.readlines() if l.strip()]
    assert len(lines) >= 1
    # check that the recorded entry contains a timestamp and response snippet
    rec = json.loads(lines[-1])
    assert "timestamp" in rec and "response" in rec
