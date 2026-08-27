# -*- coding: utf-8 -*-
"""v1.5-T10: 注入台账日报脚本测试。

覆盖：三行样例聚合 / L5 比例计算 / 损坏行跳过 / 空文件。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_ROOT / "tools" / "measure" / "injection_report.py"


def _run(ledger: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--ledger", str(ledger), "--out", str(out)],
        capture_output=True, text=True,
    )


def _write_ledger(path: Path, rows: list):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _sample_rows():
    return [
        # 2026-08-24：chat_memories 300 + relationship 100，total 500
        {"ts": "2026-08-24T10:00:00", "request_id": "a",
         "sections": {"chat_memories": 300, "relationship": 100,
                      "identity": 50, "self_model": 50},
         "total": 500},
        # 2026-08-24：第二行，goal 80 + identity 20，total 100
        {"ts": "2026-08-24T11:00:00", "request_id": "b",
         "sections": {"goal": 80, "identity": 20},
         "total": 100},
        # 2026-08-25：emotion 200 + chat_memories 200，total 400
        {"ts": "2026-08-25T09:00:00", "request_id": "c",
         "sections": {"emotion": 200, "chat_memories": 200},
         "total": 400},
    ]


# ============================================================
# 三行样例聚合
# ============================================================
def test_aggregate_three_rows(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    out = tmp_path / "daily.json"
    _write_ledger(ledger, _sample_rows())
    r = _run(ledger, out)
    assert r.returncode == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert set(report.keys()) == {"2026-08-24", "2026-08-25"}
    day1 = report["2026-08-24"]
    assert day1["total_chars"] == 600  # 500 + 100
    assert day1["sections"]["chat_memories"]["chars"] == 300
    assert day1["sections"]["goal"]["chars"] == 80
    # 缺失 section 按 0
    assert day1["sections"]["temporal"]["chars"] == 0
    day2 = report["2026-08-25"]
    assert day2["total_chars"] == 400
    assert day2["sections"]["emotion"]["chars"] == 200


# ============================================================
# L5 比例计算
# ============================================================
def test_l5_pct(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    out = tmp_path / "daily.json"
    _write_ledger(ledger, _sample_rows())
    r = _run(ledger, out)
    assert r.returncode == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    day1 = report["2026-08-24"]
    # L5 sources: chat_memories 300 + relationship 100 + self_model 50 + goal 80
    #            (experience/temporal 缺失=0)
    l5 = 300 + 100 + 50 + 80
    assert day1["l5_chars"] == l5
    assert abs(day1["l5_pct"] - round(l5 * 100.0 / 600, 4)) < 1e-6
    day2 = report["2026-08-25"]
    # 仅 chat_memories 200 属 L5
    assert day2["l5_chars"] == 200
    assert abs(day2["l5_pct"] - 50.0) < 1e-6


# ============================================================
# 损坏行跳过
# ============================================================
def test_corrupt_lines_skipped(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    out = tmp_path / "daily.json"
    good = _sample_rows()
    with open(ledger, "w", encoding="utf-8") as f:
        f.write("这不是合法 json\n")
        f.write("{\"ts\": \"2026-08-25T01:00:00\"}\n")  # 无 sections → 跳过
        for row in good:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.write("{ 破损\n")
    r = _run(ledger, out)
    assert r.returncode == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    # 三行有效样例 + 无 sections 行（被忽略）正常聚合
    assert report["2026-08-24"]["total_chars"] == 600
    assert report["2026-08-25"]["total_chars"] == 400


# ============================================================
# 空文件 / 不存在
# ============================================================
def test_empty_file(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    out = tmp_path / "daily.json"
    ledger.write_text("", encoding="utf-8")
    r = _run(ledger, out)
    assert r.returncode == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report == {}
    assert "无台账数据" in r.stdout


def test_missing_file(tmp_path):
    ledger = tmp_path / "nope.jsonl"
    out = tmp_path / "daily.json"
    r = _run(ledger, out)
    assert r.returncode == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report == {}


# ============================================================
# 默认配置 L5 sources 与报告结构
# ============================================================
def test_default_l5_sources_match_spec(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    out = tmp_path / "daily.json"
    _write_ledger(ledger, [_sample_rows()[0]])
    r = _run(ledger, out)
    report = json.loads(out.read_text(encoding="utf-8"))
    day = report["2026-08-24"]
    assert set(day["l5_sources"]) == {
        "chat_memories", "relationship", "self_model", "goal", "experience", "temporal",
    }
    # stdout 含表头
    assert "l5_pct" in r.stdout
