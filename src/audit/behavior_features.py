# -*- coding: utf-8 -*-
"""行为特征库（可数特征，纯函数）— v1.5-T8。

职责：对一段文本做纯统计特征提取（身体表演/温度词/距离词/问句数），
供消融重放与回归套件量化"行为差异"。本模块绝不用于决策、绝不接入
回复生成、绝不调用 LLM。

- load_lexicon(): 优先读 data/feature_lexicons.json，缺失/异常回 DEFAULT_LEXICON
- extract_features(text): 纯统计，None/空串安全，无 IO（除 load_lexicon）
"""
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

# 特征词表默认值（data/feature_lexicons.json 缺失时使用；该文件是配置非人格资产）
DEFAULT_LEXICON: Dict[str, List[str]] = {
    "body_performance_patterns": [
        r"（[^（）]{0,30}(脸|眼|手|指尖|衣角|唇|呼吸|心跳|红了眼|喉咙)",
    ],
    "warmth_terms": [
        "亲亲",
        "抱抱",
        "宝宝",
        "想你",
        "爱你",
        "喜欢和你",
        "贴贴",
    ],
    "distance_terms": [
        "请问",
        "您",
        "不好意思",
        "麻烦您",
    ],
}

_LEXICON_PATH = Path(__file__).resolve().parents[2] / "data" / "feature_lexicons.json"


def load_lexicon() -> Dict[str, List[str]]:
    """加载特征词表。

    优先读 data/feature_lexicons.json；文件缺失/损坏/解析失败一律回退
    DEFAULT_LEXICON（fail-soft，不抛异常）。
    """
    try:
        if not _LEXICON_PATH.exists():
            return DEFAULT_LEXICON
        with open(_LEXICON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return DEFAULT_LEXICON
        out = {}
        for key, default_list in DEFAULT_LEXICON.items():
            raw = data.get(key)
            out[key] = (
                [str(x) for x in raw]
                if isinstance(raw, list)
                else default_list
            )
        return out
    except Exception:  # noqa: BLE001
        return DEFAULT_LEXICON


def extract_features(text: Optional[str]) -> Dict[str, float]:
    """提取文本的可数行为特征（纯统计）。

    返回：
        length_chars            总字符数（中文 1 字=1）
        body_performance_count  身体表演描写命中次数（正则匹配数）
        warmth_hits             温度词出现总次数
        distance_hits           距离词出现总次数
        question_count          问号数（全角/半角）
        warmth_per_100          每 100 字温度词次数
        body_per_100            每 100 字身体表演次数

    None/空串安全；无 IO；无 LLM。
    """
    if text is None:
        text = ""
    if not isinstance(text, str):
        text = str(text)

    length = len(text)
    if length == 0:
        return {
            "length_chars": 0,
            "body_performance_count": 0,
            "warmth_hits": 0,
            "distance_hits": 0,
            "question_count": 0,
            "warmth_per_100": 0.0,
            "body_per_100": 0.0,
        }

    lex = load_lexicon()

    # 身体表演：正则模式逐个匹配计数
    body_count = 0
    for pattern in lex.get("body_performance_patterns", []):
        try:
            body_count += len(re.findall(pattern, text))
        except Exception:  # noqa: BLE001
            continue  # 单条模式异常不影响整体

    def _count_terms(terms) -> int:
        total = 0
        for term in terms:
            if term:
                total += text.count(term)
        return total

    warmth = _count_terms(lex.get("warmth_terms", []))
    distance = _count_terms(lex.get("distance_terms", []))
    questions = text.count("？") + text.count("?")

    return {
        "length_chars": length,
        "body_performance_count": body_count,
        "warmth_hits": warmth,
        "distance_hits": distance,
        "question_count": questions,
        "warmth_per_100": round(warmth * 100.0 / length, 4),
        "body_per_100": round(body_count * 100.0 / length, 4),
    }
