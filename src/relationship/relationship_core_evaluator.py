# -*- coding: utf-8 -*-
"""Phase 2.5-C 阶段3:RelationshipCoreEvaluator —— 记忆 → 关系核心候选的评估器。

职责边界(AGENTS.md Rule 2/3):
- 本模块只做「候选识别与打分」,输出 category + score + reason;
- 绝不写 RelationshipCore、绝不写白名单、绝不修改人格/情绪/关系状态;
- 候选进入治理流程的唯一入口是 RelationshipProposal(人工审核);
- 禁止「LLM 认为重要 → 自动成为核心关系」。

四维度打分(纯规则、可解释、确定性,不调 LLM):
1. 事实性     —— 记录是否有证据链(user_id + timestamp)
2. 长期影响   —— 长期/约束性语言(永远/一直/约定/不要...),扣分瞬时时态(今天/刚才...)
3. 稳定性     —— 证据 + 长期标记叠加(以证据为硬前提)
4. 关系意义   —— 是否指向「羽依与某人的关系」(我们/彼此/羽依/你和我...)
"""
from __future__ import annotations

from typing import Any, Dict

CATEGORY_CANDIDATE = "relationship_core_candidate"
CATEGORY_NOT_CANDIDATE = "not_candidate"

# 瞬时时态词:显著削弱「长期关系核心」的判断
_TRANSIENT_WORDS = ("今天", "刚才", "刚刚", "暂时", "昨晚", "昨天", "中午", "刚才")
# 长期性标记
_LONG_TERM_WORDS = ("永远", "一直", "以后", "长期", "总是", "从不", "约定", "承诺", "规则")
# 约束性语言(对羽依行为的长期约束)
_CONSTRAINT_WORDS = ("不要", "别", "禁止", "不许", "必须", "要记得", "记住")
# 关系指向(羽依与某人的关系,而非泛泛的「我」)
_RELATION_WORDS = ("我们", "我们之间", "彼此", "羽依", "你和我", "咱们")


def _has_evidence(record: Dict[str, Any]) -> bool:
    user_id = record.get("user_id")
    timestamp = record.get("timestamp")
    return bool(user_id) and bool(timestamp)


def _count_hits(content: str, words: tuple) -> int:
    return sum(1 for w in words if w in content)


class RelationshipCoreEvaluator:
    """记忆 → 关系核心候选评估器(只读、纯函数)。"""

    def evaluate(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """评估一条记忆,返回 {category, score, reason}。"""
        try:
            record = record if isinstance(record, dict) else {}
            content = str(record.get("content") or "")
            evidence = 1.0 if _has_evidence(record) else 0.0

            # 维度2:长期影响
            long_hits = _count_hits(content, _LONG_TERM_WORDS)
            transient_hits = _count_hits(content, _TRANSIENT_WORDS)
            long_term = min(1.0, long_hits * 0.25) - min(0.7, transient_hits * 0.35)
            long_term = max(0.0, long_term)

            # 维度3:稳定性 —— 以证据为硬前提(无证据 = 0)
            stability = evidence if (long_hits > 0 or _count_hits(content, _CONSTRAINT_WORDS) > 0) else 0.0

            # 维度4:关系意义
            significance = 1.0 if (
                _count_hits(content, _RELATION_WORDS) > 0
                or _count_hits(content, _CONSTRAINT_WORDS) > 0
            ) else 0.0

            score = 0.25 * evidence + 0.4 * long_term + 0.15 * stability + 0.2 * significance
            # 无证据链硬上限:再强的语言也无法成为候选(不可追溯)
            if evidence == 0.0:
                score = min(score, 0.4)
            score = round(max(0.0, min(1.0, score)), 4)

            if score >= 0.5:
                return {
                    "category": CATEGORY_CANDIDATE,
                    "score": score,
                    "reason": "长期约束性语言命中且有证据链,建议进入关系核心治理流程",
                }
            if transient_hits and long_hits == 0:
                return {
                    "category": CATEGORY_NOT_CANDIDATE,
                    "score": score,
                    "reason": "瞬时时态为主,缺少长期关系指向",
                }
            if evidence == 0.0:
                return {
                    "category": CATEGORY_NOT_CANDIDATE,
                    "score": score,
                    "reason": "缺少证据链(user_id/timestamp),不可追溯",
                }
            return {
                "category": CATEGORY_NOT_CANDIDATE,
                "score": score,
                "reason": "长期影响与关系意义不足",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "category": CATEGORY_NOT_CANDIDATE,
                "score": 0.0,
                "reason": f"评估失败(已降级): {exc}",
            }


__all__ = [
    "CATEGORY_CANDIDATE",
    "CATEGORY_NOT_CANDIDATE",
    "RelationshipCoreEvaluator",
]
