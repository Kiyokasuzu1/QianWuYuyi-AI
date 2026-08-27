# -*- coding: utf-8 -*-
"""v1.5-T8: 行为特征库测试。"""
import json
from pathlib import Path

import pytest

from src.audit.behavior_features import (
    DEFAULT_LEXICON,
    _LEXICON_PATH,
    extract_features,
    load_lexicon,
)


# ============================================================
# 正例：身体表演 + 温度词 + 问句
# ============================================================
def test_positive_text_stats():
    text = "（脸腾地热起来，指尖绞了绞衣角）亲亲宝宝，你想我了吗？"
    f = extract_features(text)
    assert f["body_performance_count"] >= 1      # 一段括号身体描写命中 1 次
    assert f["warmth_hits"] >= 2                 # 亲亲 / 宝宝
    assert f["question_count"] >= 1              # ？/ ?
    assert f["length_chars"] == len(text)
    assert f["warmth_per_100"] > 0
    assert f["body_per_100"] > 0


def test_positive_exact_counts():
    text = "亲亲，抱抱，贴贴！"
    f = extract_features(text)
    assert f["warmth_hits"] == 3
    assert f["distance_hits"] == 0
    assert f["body_performance_count"] == 0


# ============================================================
# 反例：距离词多、无身体表演
# ============================================================
def test_negative_text_stats():
    text = "请问，您有什么需要帮忙的吗？不好意思打扰了，麻烦您稍等。"
    f = extract_features(text)
    assert f["distance_hits"] >= 4               # 请问/您/不好意思/麻烦您
    assert f["warmth_hits"] == 0
    assert f["body_performance_count"] == 0
    assert f["question_count"] >= 1


# ============================================================
# 空 / None 安全
# ============================================================
def test_empty_string():
    f = extract_features("")
    assert f["length_chars"] == 0
    assert f["body_performance_count"] == 0
    assert f["warmth_hits"] == 0
    assert f["distance_hits"] == 0
    assert f["question_count"] == 0
    assert f["warmth_per_100"] == 0
    assert f["body_per_100"] == 0


def test_none_safe():
    f = extract_features(None)
    assert f["length_chars"] == 0
    assert f["warmth_per_100"] == 0


# ============================================================
# lexicon 加载：文件缺失回退 / 文件存在优先
# ============================================================
def test_load_lexicon_defaults_on_missing(monkeypatch):
    # 指向不存在的路径
    monkeypatch.setattr(
        "src.audit.behavior_features._LEXICON_PATH",
        Path("C:/definitely/not/exists/feature_lexicons.json"),
    )
    lex = load_lexicon()
    assert lex == DEFAULT_LEXICON
    assert "亲亲" in lex["warmth_terms"]


def test_load_lexicon_uses_file(monkeypatch, tmp_path):
    custom = tmp_path / "feature_lexicons.json"
    custom.write_text(json.dumps({
        "warmth_terms": ["自定义词"],
        "distance_terms": [],
        "body_performance_patterns": [],
    }), encoding="utf-8")
    monkeypatch.setattr(
        "src.audit.behavior_features._LEXICON_PATH", custom,
    )
    lex = load_lexicon()
    assert lex["warmth_terms"] == ["自定义词"]


def test_load_lexicon_corrupt_falls_back(monkeypatch, tmp_path):
    bad = tmp_path / "feature_lexicons.json"
    bad.write_text("{ 这不是合法 json", encoding="utf-8")
    monkeypatch.setattr(
        "src.audit.behavior_features._LEXICON_PATH", bad,
    )
    lex = load_lexicon()
    assert lex == DEFAULT_LEXICON


def test_real_lexicon_file_exists_and_valid():
    """真实 data/feature_lexicons.json 存在且可解析（生产数据完整性）。"""
    assert _LEXICON_PATH.exists()
    data = json.loads(_LEXICON_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "warmth_terms" in data
    assert "亲亲" in data["warmth_terms"]
