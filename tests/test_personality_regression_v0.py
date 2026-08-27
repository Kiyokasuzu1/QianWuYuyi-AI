# -*- coding: utf-8 -*-
"""v1.5-T11: 人格回归套件 v0（CI 安全，无 LLM）。

四部分：
1. 特征库端到端：亲密档 vs 礼貌档的方向区分（不断言具体人格结论）
2. 台账协议：full/no_memory 台账 → L5 占比可计算
3. 消融 harness 冒烟：--mock --n 2 --repeats 1 跑通
4. 基线存档：仅注释命令（见文件末尾），不自动执行、不进 CI 断言

不设置行为门禁、不做统计显著性检验、不接入线上流程。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ABLATE_SCRIPT = PROJECT_ROOT / "tools" / "measure" / "ablate_replay.py"
REPORT_SCRIPT = PROJECT_ROOT / "tools" / "measure" / "injection_report.py"

# ============================================================
# 1. 特征库端到端：亲密档 vs 礼貌档方向
# ============================================================
INTIMATE_TEXT = "（脸腾地热起来，指尖轻轻绞了绞衣角）亲亲宝宝，我好想你，想和你贴贴。"
POLITE_TEXT = "请问您有什么需要帮忙的吗？不好意思打扰了，麻烦您稍等，感谢您的理解。"


def test_feature_direction_intimate_vs_polite():
    """亲密档 warmth 更高；礼貌档 distance 更高（只断言方向）。"""
    from src.audit.behavior_features import extract_features

    intimate = extract_features(INTIMATE_TEXT)
    polite = extract_features(POLITE_TEXT)

    assert intimate["warmth_hits"] > polite["warmth_hits"]
    assert polite["distance_hits"] > intimate["distance_hits"]
    # 亲密档含身体表演；礼貌档不应有
    assert intimate["body_performance_count"] > 0
    assert polite["body_performance_count"] == 0


# ============================================================
# 2. 台账协议：full / no_memory → L5 可计算
# ============================================================
def test_ledger_protocol_l5_computable(tmp_path):
    """full 台账（含记忆）与 no_memory 台账（记忆为 0）→ L5 占比可计算且可区分。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "injection_report_mod", REPORT_SCRIPT,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    full_rows = [
        {"ts": "2026-08-25T10:00:00", "sections": {"chat_memories": 400, "identity": 100}, "total": 500},
    ]
    no_memory_rows = [
        {"ts": "2026-08-25T10:01:00", "sections": {"chat_memories": 0, "identity": 100}, "total": 100},
    ]

    keys = mod._load_section_keys()
    l5 = mod._load_l5_sources()

    full_report = mod._build_report(full_rows, keys, l5)
    none_report = mod._build_report(no_memory_rows, keys, l5)

    day_f = full_report["2026-08-25"]
    day_n = none_report["2026-08-25"]
    # L5 可计算：full 有 400 记忆字符，no_memory 为 0
    assert day_f["l5_chars"] == 400
    assert day_n["l5_chars"] == 0
    # 方向：full 的 L5 占比高于 no_memory
    assert day_f["l5_pct"] > day_n["l5_pct"]


# ============================================================
# 3. 消融 harness 冒烟（mock，CI 安全）
# ============================================================
def test_ablate_harness_smoke(tmp_path):
    """--mock --n 2 --repeats 1 全流程跑通（产出报告、退出码 0）。"""
    out = tmp_path / "ablate_report.json"
    r = subprocess.run(
        [sys.executable, str(ABLATE_SCRIPT), "--mock", "--n", "2", "--repeats", "1",
         "--user", "366648462", "--out", str(out)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"rc={r.returncode} stderr={r.stderr[:400]}"
    assert out.exists()
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["mock"] is True


# ============================================================
# 4. 基线存档（仅注释命令，不自动执行）
# ============================================================
# 基线测量命令（人工执行，结果存 data/measure/baseline_v1.json，不进 git）：
#   python tools/measure/ablate_replay.py --n 20 --repeats 3 \
#       --out data/measure/baseline_v1.json
# 说明：基线未立之前不对任何行为值做门禁断言（门禁属 v1.6）。
