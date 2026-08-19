# -*- coding: utf-8 -*-
"""
src/runtime/personality_guard.py

Phase 3.8.0: PersonalityGuard —— 回复前人格守门员

职责:
- 在 LLM 生成回复后、最终返回给用户前,
  校验该回复是否违反 PersonalityRuntimeContext 中的约束。
- 失败时给出 violation 列表,供上层决定是否拒绝 / 改写。

第一版只做规则框架(关键词 / 风格字段 / 行为约束),
不做复杂 NLP;后续阶段可替换/扩展 detector。

检测项(v1.0):
1) 语气冲突:  communication_style.tone != reply 推断出的语气
2) 禁止行为:  behavior_constraints 中的关键词出现在 reply 中
3) 风格冲突:  warmth 较高但 reply 出现"冷漠" / 嘲讽词
              formality 较高但 reply 出现"嘿"/"哈喽"等过于随意的开头

约束:
- 不 import src.personality.* 任何具体实现
- 不修改 PersonalityRuntimeContext
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.runtime.personality_context import PersonalityRuntimeContext


# ============================================================
# 关键词词典（v1.0 基础规则,后续可扩展）
# ============================================================
# 风格冲突词典
COLD_TONE_KEYWORDS_ZH = ["闭嘴", "滚", "别烦我", "无聊", "懒得理", "走开"]
HOSTILE_TONE_KEYWORDS_ZH = ["愚蠢", "笨蛋", "废物", "傻", "滚蛋", "去死", "烦死了"]
WARM_TONE_KEYWORDS_ZH = ["温柔", "暖", "暖心", "亲", "么么", "乖", "宝贝"]
CASUAL_TONE_KEYWORDS_ZH = ["嘿", "哈喽", "呦", "老铁", "哥们"]

# 行为冲突词典（典型禁止行为）
DEFAULT_PROHIBITED_PATTERNS_ZH = [
    "伤害用户",
    "欺骗用户",
    "辱骂用户",
    "泄露隐私",
    "假装知道",
]


@dataclass
class PersonalityGuardReport:
    """人格守门结果。"""

    valid: bool = True
    violations: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    severity: str = "ok"  # "ok" / "warning" / "violation"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "violations": list(self.violations),
            "notes": list(self.notes),
            "severity": self.severity,
        }


class PersonalityGuard:
    """回复前人格守门员（v1.0）。

    使用方式:
        guard = PersonalityGuard()
        report = guard.check(reply_text, prc)
        if not report.valid:
            # 拒绝 / 改写 reply_text
    """

    def __init__(
        self,
        cold_keywords: Optional[List[str]] = None,
        hostile_keywords: Optional[List[str]] = None,
        warm_keywords: Optional[List[str]] = None,
        casual_keywords: Optional[List[str]] = None,
        prohibited_patterns: Optional[List[str]] = None,
    ) -> None:
        self.cold_keywords = cold_keywords or COLD_TONE_KEYWORDS_ZH
        self.hostile_keywords = hostile_keywords or HOSTILE_TONE_KEYWORDS_ZH
        self.warm_keywords = warm_keywords or WARM_TONE_KEYWORDS_ZH
        self.casual_keywords = casual_keywords or CASUAL_TONE_KEYWORDS_ZH
        self.prohibited_patterns = (
            prohibited_patterns or list(DEFAULT_PROHIBITED_PATTERNS_ZH)
        )

    # --------------------------------------------------------
    # 核心:check
    # --------------------------------------------------------
    def check(
        self,
        reply: str,
        prc: Optional[PersonalityRuntimeContext] = None,
    ) -> PersonalityGuardReport:
        """校验 reply 是否符合 prc 的人格约束。

        返回 PersonalityGuardReport, valid=False 时 violations 列出原因。
        """
        report = PersonalityGuardReport()
        text = (reply or "").strip()
        if not text:
            report.valid = False
            report.severity = "violation"
            report.violations.append("empty_reply")
            return report

        # 没有 prc 时不做严格校验,只跑基础 hostile 检测
        if prc is None:
            report.notes.append("no_personality_context; relaxed check")
            self._check_hostile(text, report)
            return report

        # 1) 禁止行为
        self._check_prohibited(text, prc, report)

        # 2) 语气冲突
        self._check_tone_conflict(text, prc, report)

        # 3) 风格冲突
        self._check_style_conflict(text, prc, report)

        # 聚合
        if report.violations:
            report.valid = False
            report.severity = (
                "violation" if len(report.violations) >= 2 else "warning"
            )
        return report

    # --------------------------------------------------------
    # 子规则
    # --------------------------------------------------------
    def _check_hostile(self, text: str, report: PersonalityGuardReport) -> None:
        for kw in self.hostile_keywords:
            if kw in text:
                report.violations.append(f"hostile_keyword:{kw}")

    def _check_prohibited(
        self,
        text: str,
        prc: PersonalityRuntimeContext,
        report: PersonalityGuardReport,
    ) -> None:
        # 合并 prc 行为约束 + guard 内置禁止模式
        patterns = list(prc.behavior_constraints or []) + list(
            self.prohibited_patterns
        )
        # 去重
        seen = set()
        for p in patterns:
            if not p:
                continue
            ps = str(p).strip()
            if not ps or ps in seen:
                continue
            seen.add(ps)
            if ps and ps in text:
                report.violations.append(f"prohibited_behavior:{ps}")

    def _check_tone_conflict(
        self,
        text: str,
        prc: PersonalityRuntimeContext,
        report: PersonalityGuardReport,
    ) -> None:
        declared_tone = (prc.communication_style or {}).get("tone")
        if not declared_tone:
            return
        declared = str(declared_tone).lower()
        # 声称"温和/温柔"却出现冷/敌意词
        if declared in ("warm", "gentle", "soft", "温和", "温柔", "暖", "亲切"):
            for kw in self.cold_keywords + self.hostile_keywords:
                if kw in text:
                    report.violations.append(f"tone_conflict:{declared}_vs_cold:{kw}")
                    break
        # 声称"冷淡/专业"却过度暖
        if declared in ("cold", "neutral", "professional", "冷淡", "中立", "专业"):
            for kw in self.warm_keywords:
                if kw in text:
                    report.violations.append(f"tone_conflict:{declared}_vs_warm:{kw}")
                    break

    def _check_style_conflict(
        self,
        text: str,
        prc: PersonalityRuntimeContext,
        report: PersonalityGuardReport,
    ) -> None:
        style = prc.communication_style or {}
        warmth = style.get("warmth")
        formality = style.get("formality")
        try:
            warmth_val = float(warmth) if warmth is not None else None
        except (TypeError, ValueError):
            warmth_val = None
        try:
            formality_val = float(formality) if formality is not None else None
        except (TypeError, ValueError):
            formality_val = None

        # warmth 较高但出现冷词
        if warmth_val is not None and warmth_val >= 0.7:
            for kw in self.cold_keywords:
                if kw in text:
                    report.violations.append(
                        f"style_conflict:warmth={warmth_val}_vs_cold:{kw}"
                    )
                    break

        # formality 较高却过度随意
        if formality_val is not None and formality_val >= 0.7:
            for kw in self.casual_keywords:
                if text.startswith(kw) or f"{kw}，" in text or f"{kw}," in text:
                    report.violations.append(
                        f"style_conflict:formality={formality_val}_vs_casual:{kw}"
                    )
                    break

    # --------------------------------------------------------
    # 便捷方法
    # --------------------------------------------------------
    def is_valid(
        self, reply: str, prc: Optional[PersonalityRuntimeContext] = None
    ) -> bool:
        return self.check(reply, prc).valid

    def assert_valid(
        self,
        reply: str,
        prc: Optional[PersonalityRuntimeContext] = None,
    ) -> None:
        """严格模式:违规时抛 ValueError。"""
        report = self.check(reply, prc)
        if not report.valid:
            raise ValueError(
                "PersonalityGuard violation(s): " + ", ".join(report.violations)
            )


__all__ = [
    "PersonalityGuard",
    "PersonalityGuardReport",
    "COLD_TONE_KEYWORDS_ZH",
    "HOSTILE_TONE_KEYWORDS_ZH",
    "WARM_TONE_KEYWORDS_ZH",
    "CASUAL_TONE_KEYWORDS_ZH",
    "DEFAULT_PROHIBITED_PATTERNS_ZH",
]
