# -*- coding: utf-8 -*-
"""MC-0.2 Contract Tests —— /mc/events 端点矩阵 + normalizer 契约 + frontend 判定。

依据：docs/YUI_MC0_INCIDENT_RECOVERY_RECORD.md（事故恢复固化）。
契约来源：api_server.py v1.5.5-MC1 代码 + src/audit/mc_event_normalizer.py（与生产
sha256 一致：818f1b09fd48e68ad75549fa…）。

事件名契约注记：端点/normalizer 实际事件表为 join/leave/craft/collect/
achievement/death（"collect" 即拾取；无 "player_join"/"pickup" 键——按实证契约测试）。
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src.audit.mc_event_normalizer import (  # noqa: E402
    MC_USER_ID, _EVENT_TABLE, normalize_mc_event,
)


# ============================================================
# A. normalizer 契约（纯函数，无 IO / 无 LLM / 无状态）
# ============================================================

@pytest.mark.parametrize("event,expect_class,expect_self,expect_authored,expect_emo", [
    ("join",         "interaction", "agent",   True,  "calm"),
    ("leave",        "interaction", "agent",   True,  "calm"),
    ("craft",        "interaction", "agent",   True,  "calm"),
    ("collect",      "interaction", "agent",   True,  "calm"),
    ("achievement",  "world",       "patient", False, "calm"),
    ("death",        "world",       "patient", False, "fear"),
])
def test_normalizer_six_events(event, expect_class, expect_self, expect_authored, expect_emo):
    record, err = normalize_mc_event(event, {"player": "Kiyoka_suzu"}, "2026-08-26T02:00:00")
    assert err is None
    md = record["metadata"]
    assert md["event_class"] == expect_class
    assert md["self_involvement"] == expect_self
    assert md["authored"] is expect_authored
    assert record["emotion_tag"] == expect_emo
    # 记录契约：role=user（PollutionGuard 白名单）、user_id 固定、来源标记
    assert record["role"] == "user"
    assert record["user_id"] == MC_USER_ID == "366648462"
    assert md["source"] == "mc_events"
    assert md["memory_type"] == "user_experience"
    assert md["origin"] == "system"


def test_normalizer_content_templates():
    rec_death, _ = normalize_mc_event("death", {"x": 1, "y": 2, "z": 3, "cause": "僵尸"},
                                      "2026-08-26T02:00:00")
    assert "被 僵尸 击杀" in rec_death["content"] and "(1.0,2.0,3.0)" in rec_death["content"]
    rec_join, _ = normalize_mc_event("join", {"player": "Kiyoka_suzu"}, "")
    assert "Kiyoka_suzu 上线了" in rec_join["content"]
    rec_join2, _ = normalize_mc_event("join", {}, "")
    assert "进入了这个世界" in rec_join2["content"], "无玩家名降级文案"
    rec_craft, _ = normalize_mc_event("craft", {"item": "oak_planks", "count": 4}, "")
    assert "合成了 4 个 oak_planks" in rec_craft["content"]
    rec_ach, _ = normalize_mc_event("achievement", {"name": "钻石!"}, "")
    assert "成就：钻石!" in rec_ach["content"]


def test_normalizer_unknown_event_rejected():
    record, err = normalize_mc_event("explosion", {}, "")
    assert record is None and err, "未知事件必须拒绝（不猜测）"
    record2, err2 = normalize_mc_event("player_join", {}, "")
    assert record2 is None, "契约外事件名（player_join）同样拒绝"
    record3, err3 = normalize_mc_event(None, {}, "")
    assert record3 is None and err3


def test_normalizer_ts_fallback_and_bad_detail():
    rec, err = normalize_mc_event("join", None, "")  # detail 非 dict 容错
    assert err is None
    rec2, err2 = normalize_mc_event("join", {}, "not-a-time")  # 非法 ts 容错
    assert err2 is None and rec2["timestamp"], "非法 ts 降级为当前时间"


def test_normalizer_pure_no_io():
    import inspect
    import src.audit.mc_event_normalizer as m
    src = inspect.getsource(m)
    for banned in ("requests", "urllib", "open(", "subprocess", "http"):
        assert banned not in src, f"normalizer 必须纯函数（禁止 {banned}）"
    assert set(_EVENT_TABLE.keys()) == {"death", "join", "leave", "craft",
                                        "collect", "achievement"}, "事件表冻结"


# ============================================================
# B. /mc/events 端点矩阵（Flask test client + 接缝打桩，零真实写入）
# ============================================================

@pytest.fixture()
def api(monkeypatch):
    import api_server as api_mod
    api_mod._mc_seen_ids.clear()  # 隔离进程内幂等集合
    return api_mod


def _post(api_mod, payload, token="__unset__"):
    client = api_mod.app.test_client()
    headers = {} if token == "__unset__" else {"X-MC-Token": token}
    return client.post("/mc/events", json=payload, headers=headers)


def test_endpoint_disabled_when_token_empty(api, monkeypatch):
    monkeypatch.setattr(api, "_mc_events_enabled_token", lambda: "")
    resp = _post(api, {"event": "join"})
    assert resp.status_code == 503, "空 token 配置 = 端点关闭"


def test_endpoint_wrong_token_403(api, monkeypatch):
    monkeypatch.setattr(api, "_mc_events_enabled_token", lambda: "TEST_MC_TOKEN_NOT_REAL")
    resp = _post(api, {"event": "join"}, token="WRONG")
    assert resp.status_code == 403
    resp2 = _post(api, {"event": "join"})  # 无 header
    assert resp2.status_code == 403


def test_endpoint_correct_token_200_and_writes(api, monkeypatch):
    wrote = []
    monkeypatch.setattr(api, "_mc_events_enabled_token", lambda: "TEST_MC_TOKEN_NOT_REAL")
    monkeypatch.setattr(api, "_mc_write_event_record", lambda r: wrote.append(r) or True)
    monkeypatch.setattr(api, "_mc_event_exists", lambda eid: False)
    resp = _post(api, {"event": "join", "event_id": "test_mc_001",
                       "detail": {"player": "Kiyoka_suzu"}}, token="TEST_MC_TOKEN_NOT_REAL")
    assert resp.status_code == 200 and resp.get_json().get("ok") is True
    assert len(wrote) == 1
    rec = wrote[0]
    assert rec["source_event_id"] == "test_mc_001"
    assert rec["id"].startswith("mc_")
    assert rec["metadata"]["source"] == "mc_events"


def test_endpoint_duplicate_event_id_idempotent(api, monkeypatch):
    calls = []
    monkeypatch.setattr(api, "_mc_events_enabled_token", lambda: "TEST_MC_TOKEN_NOT_REAL")
    monkeypatch.setattr(api, "_mc_write_event_record", lambda r: calls.append(r) or True)
    monkeypatch.setattr(api, "_mc_event_exists", lambda eid: False)
    payload = {"event": "leave", "event_id": "test_mc_dup", "detail": {}}
    r1 = _post(api, payload, token="TEST_MC_TOKEN_NOT_REAL")
    r2 = _post(api, payload, token="TEST_MC_TOKEN_NOT_REAL")
    assert r1.get_json().get("ok") is True
    assert r2.get_json().get("duplicate") is True, "重复 event_id 必须幂等"
    assert len(calls) == 1, "重复事件不得二次写入"


def test_endpoint_unknown_event_rejected_without_write(api, monkeypatch):
    wrote = []
    monkeypatch.setattr(api, "_mc_events_enabled_token", lambda: "TEST_MC_TOKEN_NOT_REAL")
    monkeypatch.setattr(api, "_mc_write_event_record", lambda r: wrote.append(r) or True)
    monkeypatch.setattr(api, "_mc_event_exists", lambda eid: False)
    resp = _post(api, {"event": "explosion", "event_id": "test_mc_bad"}, token="TEST_MC_TOKEN_NOT_REAL")
    assert resp.status_code == 200 and resp.get_json().get("ok") is False, "fail-soft：拒绝但 200"
    assert wrote == [], "未知事件不落库"


def test_endpoint_persistent_dedup_via_store(api, monkeypatch):
    """进程内 set 未命中时，走 store 持久幂等（_mc_event_exists）。"""
    calls = []
    monkeypatch.setattr(api, "_mc_events_enabled_token", lambda: "TEST_MC_TOKEN_NOT_REAL")
    monkeypatch.setattr(api, "_mc_write_event_record", lambda r: calls.append(r) or True)
    monkeypatch.setattr(api, "_mc_event_exists", lambda eid: eid == "test_mc_persist")
    r = _post(api, {"event": "craft", "event_id": "test_mc_persist",
                    "detail": {"item": "x"}}, token="TEST_MC_TOKEN_NOT_REAL")
    assert r.get_json().get("duplicate") is True and calls == []


def test_endpoint_event_id_autogenerated(api, monkeypatch):
    wrote = []
    monkeypatch.setattr(api, "_mc_events_enabled_token", lambda: "TEST_MC_TOKEN_NOT_REAL")
    monkeypatch.setattr(api, "_mc_write_event_record", lambda r: wrote.append(r) or True)
    monkeypatch.setattr(api, "_mc_event_exists", lambda eid: False)
    r = _post(api, {"event": "join", "detail": {}}, token="TEST_MC_TOKEN_NOT_REAL")
    assert r.get_json().get("ok") is True
    assert wrote[0]["source_event_id"].startswith("mc_evt_"), "缺 event_id 自动生成"


# ============================================================
# C. frontend 判定（IP 段契约）
# ============================================================

@pytest.mark.parametrize("ip,expected", [
    ("100.114.143.47", "mc"),    # Tailscale CGNAT 段内
    ("100.64.0.1", "mc"),
    ("100.127.255.255", "mc"),
    ("100.63.1.1", "qq"),        # 段外（<64）
    ("100.128.0.1", "qq"),       # 段外（>127）
    ("192.168.1.5", "qq"),       # 普通 IP
    ("", "unknown"),
    ("unknown", "unknown"),
    ("not-an-ip", "qq"),         # IP 级判定不校验格式：非空且非 Tailscale 段 → qq
])
def test_detect_frontend_ip_matrix(api, monkeypatch, ip, expected):
    monkeypatch.setattr(api, "_client_ip", lambda: ip)
    assert api._detect_frontend() == expected


def test_detect_frontend_injected_into_pipeline_inputs(api, monkeypatch):
    """v1.5.5 C1：/v1 pipeline 输入携带 frontend 字段（打桩验证注入点存在）。"""
    captured = {}
    monkeypatch.setattr(api, "_client_ip", lambda: "100.114.1.1")

    class FakePipeline:
        def run(self, inputs):
            captured.update(inputs)
            return {"reply": "ok"}

    class FakeLock:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(api, "_pipeline", FakePipeline())
    monkeypatch.setattr(api, "_pipeline_lock", FakeLock())
    out = api._process_via_pipeline("你好", "366648462")
    assert captured.get("frontend") == "mc", "pipeline inputs 必须含 frontend=mc"
    # 返回为 OpenAI completion 包装结构（不因打桩 pipeline 而崩）
    assert isinstance(out, dict) and out.get("object") == "chat.completion"
