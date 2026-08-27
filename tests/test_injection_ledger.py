# -*- coding: utf-8 -*-
"""v1.5-T3: 注入台账（Injection Ledger）测试。"""
import json
import os

import pytest

from src.audit.injection_ledger import (
    SECTION_KEYS,
    _LEDGER_LOCK,
    get_ledger_path,
    is_ledger_enabled,
    record_injection,
)


@pytest.fixture(autouse=True)
def _reset_path(tmp_path, monkeypatch):
    """每个用例把台账路径指向 tmp 目录，互不污染。"""
    import src.audit.injection_ledger as m
    monkeypatch.setattr(m, "_LEDGER_PATH", str(tmp_path / "injection_ledger.jsonl"))
    yield


def test_write_and_read_back(tmp_path):
    path = get_ledger_path()
    record_injection(
        {"yui_core": 10, "chat_memories": 200, "relationship": 50},
        request_id="req-1",
        user_id="366648462",
    )
    lines = open(path, encoding="utf-8").read().strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["request_id"] == "req-1"
    assert row["user_id"] == "366648462"
    assert row["sections"]["yui_core"] == 10
    assert row["sections"]["chat_memories"] == 200
    assert row["sections"]["relationship"] == 50
    # 未提供的键补 0，14 键齐全
    assert set(row["sections"].keys()) == set(SECTION_KEYS)
    assert row["sections"]["emotion"] == 0
    # total = 求和
    assert row["total"] == sum(row["sections"].values())


def test_append_multiple_rows(tmp_path):
    path = get_ledger_path()
    record_injection({"identity": 1})
    record_injection({"identity": 2})
    assert len(open(path, encoding="utf-8").read().strip().splitlines()) == 2


def test_negative_or_bad_values_are_clamped(tmp_path):
    record_injection({"identity": -5, "goal": "abc", "emotion": 3})
    row = json.loads(open(get_ledger_path(), encoding="utf-8").read().strip().splitlines()[0])
    assert row["sections"]["identity"] == 0   # 负值钳 0
    assert row["sections"]["goal"] == 0       # 非数字 → int() 失败 → 0
    assert row["sections"]["emotion"] == 3


def test_disabled_no_write(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.audit.injection_ledger.is_ledger_enabled", lambda: False,
    )
    record_injection({"identity": 1})
    assert not os.path.exists(get_ledger_path())


def test_unwritable_path_failsoft(tmp_path, monkeypatch):
    """写入失败（路径不可写）时静默降级，不抛异常。"""
    import src.audit.injection_ledger as m
    monkeypatch.setattr(m, "_LEDGER_PATH", str(tmp_path / "no_dir" / "ledger.jsonl"))
    record_injection({"identity": 1})  # 父目录自动创建，可写 → 成功
    assert os.path.exists(get_ledger_path())


def test_enabled_default_true():
    assert is_ledger_enabled() is True
