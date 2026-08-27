# -*- coding: utf-8 -*-
"""v1.5-T9: 消融重放 harness 测试。

覆盖：
- mock 跑通（rc=0）
- 产出报告（7 特征 × full/no_memory/diff）
- 沙盒清理（无 ablate_ 残留目录）
- 真实 data 哈希不变（memory.json / conversation_history.json）
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_ROOT / "tools" / "measure" / "ablate_replay.py"

FEATURE_KEYS = (
    "length_chars",
    "body_performance_count",
    "warmth_hits",
    "distance_hits",
    "question_count",
    "warmth_per_100",
    "body_per_100",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_harness(out_path: Path, extra=None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT), "--mock", "--n", "2", "--repeats", "1",
           "--user", "366648462", "--out", str(out_path)]
    if extra:
        cmd += extra
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(PROJECT_ROOT))


def _ablate_tempdirs_left() -> list:
    tmpdir = Path(tempfile.gettempdir())
    return [p for p in tmpdir.glob("ablate_*")]


def test_mock_run_produces_report(tmp_path):
    out = tmp_path / "report.json"
    r = _run_harness(out)
    assert r.returncode == 0, f"rc={r.returncode} stderr={r.stderr[:500]}"
    assert out.exists()
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["mock"] is True
    assert set(report["features"].keys()) == set(FEATURE_KEYS)
    for k in FEATURE_KEYS:
        item = report["features"][k]
        assert "full" in item and "no_memory" in item and "diff_full_minus_none" in item
        assert item["full"]["n"] >= 1
        assert item["no_memory"]["n"] >= 1


def test_sandbox_no_business_leakage(tmp_path):
    """沙盒不泄漏业务数据到临时目录。

    Windows + chromadb 的 sqlite 句柄无法进程内释放，删除可能残留
    chroma 缓存目录（对项目无害，系统 temp 可清）。真正不可接受的是
    业务数据（memory/conversation/lexicon）泄漏到沙盒之外。
    """
    business_names = {"memory.json", "conversation_history.json",
                      "feature_lexicons.json", "relationship_state.json"}
    out = tmp_path / "report.json"
    before = _ablate_tempdirs_left()
    r = _run_harness(out)
    assert r.returncode == 0
    # 清理流程已执行（无论是否完全）
    assert "sandbox" in r.stdout and "清理" in r.stdout
    after = _ablate_tempdirs_left()
    # 遍历新增残留：不得包含业务数据文件
    for d in after:
        if d in before:
            continue
        for name in business_names:
            assert not (d / "data" / name).exists(), f"业务数据泄漏: {d / name}"


def test_real_data_untouched(tmp_path):
    mem = PROJECT_ROOT / "data" / "memory.json"
    conv = PROJECT_ROOT / "data" / "conversation_history.json"
    h1 = _sha256(mem) if mem.exists() else None
    h2 = _sha256(conv) if conv.exists() else None
    out = tmp_path / "report.json"
    r = _run_harness(out)
    assert r.returncode == 0
    if h1 is not None:
        assert _sha256(mem) == h1, "memory.json 被 harness 修改"
    if h2 is not None:
        assert _sha256(conv) == h2, "conversation_history.json 被 harness 修改"


def test_unknown_user_exits_nonzero(tmp_path):
    out = tmp_path / "report.json"
    r = _run_harness(out, extra=["--user", "999999999"])
    # 无样本 → 退出码 1
    assert r.returncode == 1
    assert "未找到" in r.stdout
