# -*- coding: utf-8 -*-
"""v1.5.5-MC1: /mc/events 端点测试（离线，mock store 到 tmp，不碰真实 data/）。"""
import json
from unittest.mock import MagicMock

import pytest


class TestMcEventsEndpoint:
    TOKEN = "test-mc-token-1234567890abcdef1234567890abcdef"

    @pytest.fixture()
    def _client(self, monkeypatch, tmp_path):
        import api_server as api_mod

        # 1) 端点开关：token 配置
        monkeypatch.setattr(
            api_mod, "_mc_events_enabled_token", lambda: self.TOKEN,
        )
        # 2) store 指向 tmp：替换 Provider 单例（api_server 经 _mc_write_event_record 取）
        from src.memory.memory_store import MemoryStore
        import src.memory.memory_provider as mp
        store = MemoryStore(str(tmp_path / "memory.json"))
        monkeypatch.setattr(mp.MemoryProvider, "_instance", store)
        # 3) 幂等去重状态隔离
        api_mod._mc_seen_ids.clear()
        # 4) 事件发布打桩（离线无 EventBus 依赖）
        monkeypatch.setattr(
            "src.events.bus.publish_event", lambda *a, **k: None,
        )
        return api_mod.app.test_client()

    def _post(self, client, payload=None, token=None):
        headers = {}
        if token is not None:
            headers["X-MC-Token"] = token
        return client.post(
            "/mc/events",
            data=json.dumps(payload or {}),
            content_type="application/json",
            headers=headers,
        )

    # ---- 用例 1：无 token → 403 ----
    def test_missing_token_returns_403(self, _client):
        r = self._post(_client, {"event": "death"}, token=None)
        assert r.status_code == 403

    # ---- 用例 1b：错误 token → 403 ----
    def test_wrong_token_returns_403(self, _client):
        r = self._post(_client, {"event": "death"}, token="wrong")
        assert r.status_code == 403

    # ---- 用例 2：合法 death → 200 ok + 落库 + 四字段 ----
    def test_valid_death_writes_record(self, _client):
        payload = {
            "event": "death",
            "event_id": "evt-death-001",
            "detail": {"cause": "zombie", "x": -44.5, "y": 72.0, "z": -13.5},
            "ts": "2026-08-26T00:30:00+08:00",
        }
        r = self._post(_client, payload, token=self.TOKEN)
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

        from src.memory.memory_provider import MemoryProvider
        recs = MemoryProvider.get_store().load()
        assert len(recs) == 1
        rec = recs[0]
        assert rec["role"] == "user"  # PollutionGuard 白名单；来源靠 metadata 区分
        assert rec["user_id"] == "366648462"
        assert "被 zombie 击杀" in rec["content"]
        md = rec["metadata"]
        assert md["event_class"] == "world"
        assert md["self_involvement"] == "patient"
        assert md["authored"] is False
        assert md["origin"] == "system"
        assert md["source"] == "mc_events"
        assert md["memory_type"] == "user_experience"
        assert rec["source_event_id"] == "evt-death-001"

    # ---- 用例 3：同 event_id 幂等 ----
    def test_duplicate_event_id_skipped(self, _client):
        payload = {"event": "collect", "event_id": "evt-dup-1", "detail": {"item": "oak_log", "count": 3}}
        r1 = self._post(_client, payload, token=self.TOKEN)
        r2 = self._post(_client, payload, token=self.TOKEN)
        assert r1.get_json()["ok"] is True
        assert r2.get_json().get("duplicate") is True

        from src.memory.memory_provider import MemoryProvider
        assert len(MemoryProvider.get_store().load()) == 1

    # ---- 用例 4：非法 payload（event 缺失/非字符串）→ 200 + ok:false 不写入 ----
    def test_invalid_payload_rejected(self, _client):
        # 空 body：event 缺失
        r = self._post(_client, {}, token=self.TOKEN)
        assert r.status_code == 200
        assert r.get_json()["ok"] is False
        # event 非字符串
        r2 = self._post(_client, {"event": 123, "event_id": "evt-bad-2"}, token=self.TOKEN)
        assert r2.get_json()["ok"] is False

        from src.memory.memory_provider import MemoryProvider
        assert MemoryProvider.get_store().load() == []

    # ---- 用例 5：未知事件拒绝 ----
    def test_unknown_event_rejected(self, _client):
        r = self._post(_client, {"event": "teleport_hack", "event_id": "evt-unknown-1"}, token=self.TOKEN)
        assert r.status_code == 200
        assert r.get_json()["ok"] is False
        assert "未知事件" in r.get_json()["error"]

    # ---- 附加：mc_events 记录可被 creator 召回（跨前端记忆闭环的前提） ----
    def test_mc_events_recallable_by_creator(self, _client):
        self._post(_client, {"event": "join", "event_id": "evt-join-1"}, token=self.TOKEN)
        try:
            from src.memory.memory_scope import collect_allowed_records
            from src.memory.memory_provider import MemoryProvider
            recs = collect_allowed_records(MemoryProvider.get_store(), "366648462", owner_limit=None)
            mc_recs = [r for r in recs if isinstance(r, dict) and r.get("metadata", {}).get("source") == "mc_events"]
            assert len(mc_recs) >= 1  # 游戏经历应进入 creator 可召回范围
        except ImportError:
            pytest.skip("memory_scope 不可用，跳过 scope 断言")

    # ---- 归一化内容表抽查（craft / achievement） ----
    def test_normalizer_content_variants(self):
        from src.audit.mc_event_normalizer import normalize_mc_event
        rec, err = normalize_mc_event("craft", {"item": "white_bed", "count": 1}, "2026-08-26T00:31:00+08:00")
        assert err is None
        assert "合成了 1 个 white_bed" in rec["content"]
        assert rec["metadata"]["event_class"] == "interaction"
        assert rec["metadata"]["self_involvement"] == "agent"

        rec2, err2 = normalize_mc_event("achievement", {"name": "钻石！"}, "")
        assert err2 is None
        assert "达成" in rec2["content"]
        assert rec2["metadata"]["authored"] is False

        _, err3 = normalize_mc_event("nonsense", {}, "")
        assert err3 is not None
