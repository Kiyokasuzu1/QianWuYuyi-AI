# -*- coding: utf-8 -*-
"""InteractionRecorder Continuity 专项测试（最少覆盖）。

结论背景：服务器落盘实证——成功请求的 metadata.reply 完整存在
（recorder 链路无数据丢失）；历史"REPLY=None"为 memory-search API
投影缺陷所致。本测试覆盖：
T1 recorder 将 final reply 写入 memory.metadata.reply
T2 空 reply（failure 路径）不伪造内容
T3 memory-search API 投影包含 reply（观测修复）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeStore:
    def __init__(self):
        self.saved = []

    def add(self, record):
        self.saved.append(record)
        return record


class _Ctx:
    def __init__(self, user_message, reply, user_id="366648462"):
        self.inputs = {"user_id": user_id, "frontend": "unknown"}
        self.outputs = {"snapshot": {
            "user_message": user_message,
            "reply": reply,
            "orchestrator": "RuntimePipeline",
        }}


# ---------- T1：正常请求 reply 进入 memory ----------
def test_t1_recorder_writes_final_reply(monkeypatch):
    from src.runtime.interaction_recorder import InteractionRecorder

    store = _FakeStore()
    rec = InteractionRecorder()
    rec._resolve_store = lambda: store
    rec._resolve_vector = lambda: None
    rec._memory_provider = None

    ctx = _Ctx("昨晚你哄我睡觉了吗", "（轻轻笑）记得呀，那时候总要说很多遍晚安才肯睡。")
    rec.record(ctx)
    assert len(store.saved) == 1
    rec_ = store.saved[0]
    assert rec_["metadata"]["memory_type"] == "user_experience"
    assert rec_["metadata"]["source"] == "runtime_pipeline"
    assert rec_["metadata"]["reply"] == "（轻轻笑）记得呀，那时候总要说很多遍晚安才肯睡。"
    assert rec_["metadata"]["frontend"] == "unknown"


# ---------- T2：failure 路径（空 reply）不伪造 ----------
def test_t2_empty_reply_not_fabricated(monkeypatch):
    from src.runtime.interaction_recorder import InteractionRecorder

    store = _FakeStore()
    rec = InteractionRecorder()
    rec._resolve_store = lambda: store
    rec._resolve_vector = lambda: None
    rec._memory_provider = None

    ctx = _Ctx("今晚有点困", "")  # failure 路径：无 assistant reply
    rec.record(ctx)
    assert len(store.saved) == 1
    rec_ = store.saved[0]
    assert rec_["metadata"]["reply"] == ""  # 空，不伪造内容
    assert rec_["content"] == "今晚有点困"


# ---------- T3：memory-search API 投影包含 reply ----------
def test_t3_memory_search_projects_reply(monkeypatch):
    os.environ.pop("YUYI_ADMIN_TOKEN", None)
    from flask import Flask
    from src.admin.api.governance_routes import gov_bp

    class _MemStore:
        def load(self):
            return [{
                "id": "mem_test_001", "content": "用户消息", "timestamp": "2026-08-27T00:00:00",
                "metadata": {"frontend": "unknown", "source": "runtime_pipeline",
                             "memory_type": "user_experience", "reply": "羽依的完整回复"},
            }]

    app = Flask(__name__)
    app.register_blueprint(gov_bp, url_prefix="/admin/api/governance")
    app.testing = True
    c = app.test_client()

    import src.admin.api.governance_routes as gr  # noqa: F401  (路由注册)
    monkeypatch.setattr("src.memory.memory_provider.MemoryProvider.get_store",
                        classmethod(lambda cls: _MemStore()))

    r = c.get("/admin/api/governance/memory-search?q=mem_test_001",
              environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200
    d = r.get_json()
    results = d.get("results", [])
    assert len(results) == 1
    md = results[0]["metadata"]
    assert md.get("reply") == "羽依的完整回复", f"投影缺 reply: {md}"
    assert md.get("frontend") == "unknown"
