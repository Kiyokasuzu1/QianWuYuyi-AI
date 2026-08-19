# -*- coding: utf-8 -*-
"""
src/runtime/perception/perception_guard.py

Phase 3.8.x: PerceptionGuard —— 回复前的来源审计

职责：
- 收集一次回复中使用的全部 Fact。
- 若发现任意 Fact 的来源 = INFERENCE：
    * 强制降低确定语气（避免把推测包装成事实）
    * 注入不确定表达
    * 或直接拒绝"假装知道"
- 不得修改 Schema，不得修改 RuntimeContext 字段。

对外接口：
- PerceptionGuard.check(facts) -> GuardReport
- PerceptionGuard.wrap_reply(facts, draft) -> str
    在 draft 文本前缀加入 hedge / disclaimer
- PerceptionGuard.is_grounded_only(facts) -> bool
    全部 Fact 都是 grounded（非 INFERENCE）时为 True
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from src.runtime.perception.fact import Fact
from src.runtime.perception.fact_source import FactSource


# 默认 hedge 前缀 / 模板
DEFAULT_HEDGE_PREFIX_ZH = "（以下为基于推测的回答）"
DEFAULT_HEDGE_SUFFIX_ZH = "（该信息为模型推理结果，可能不准确）"
DEFAULT_REFUSAL_ZH = "我没有足够依据回答这个问题。"

DEFAULT_HEDGE_PREFIX_EN = "(The following answer is based on inference.)"
DEFAULT_HEDGE_SUFFIX_EN = "(This information is inferred and may be inaccurate.)"
DEFAULT_REFUSAL_EN = "I don't have sufficient basis to answer this question."


@dataclass
class GuardReport:
    """一次审计的结果。"""

    total: int = 0
    inference_count: int = 0
    grounded_count: int = 0
    inference_facts: List[Fact] = field(default_factory=list)
    grounded_only: bool = True
    needs_hedge: bool = False
    needs_refusal: bool = False
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "inference_count": self.inference_count,
            "grounded_count": self.grounded_count,
            "grounded_only": self.grounded_only,
            "needs_hedge": self.needs_hedge,
            "needs_refusal": self.needs_refusal,
            "notes": list(self.notes),
        }


class PerceptionGuard:
    """回复前来源审计器。

    参数：
    - hedge_prefix:   INFERENCE 存在时附加的前缀（提示读者）
    - hedge_suffix:   INFERENCE 存在时附加的后缀
    - refusal_text:   当所有 Fact 均为 INFERENCE 且无 grounding 时使用的拒绝语
    - min_grounded_ratio:  当 grounded_facts / total < 该阈值时，强制 refusal
    - language:       "zh" / "en"，决定默认模板
    """

    def __init__(
        self,
        hedge_prefix: Optional[str] = None,
        hedge_suffix: Optional[str] = None,
        refusal_text: Optional[str] = None,
        min_grounded_ratio: float = 0.0,
        language: str = "zh",
    ) -> None:
        if language == "en":
            self.hedge_prefix = hedge_prefix or DEFAULT_HEDGE_PREFIX_EN
            self.hedge_suffix = hedge_suffix or DEFAULT_HEDGE_SUFFIX_EN
            self.refusal_text = refusal_text or DEFAULT_REFUSAL_EN
        else:
            self.hedge_prefix = hedge_prefix or DEFAULT_HEDGE_PREFIX_ZH
            self.hedge_suffix = hedge_suffix or DEFAULT_HEDGE_SUFFIX_ZH
            self.refusal_text = refusal_text or DEFAULT_REFUSAL_ZH
        self.min_grounded_ratio = float(min_grounded_ratio)
        self.language = language

    # ---------------------------------------------------------
    # 核心：审计
    # ---------------------------------------------------------
    def check(self, facts: Iterable[Fact]) -> GuardReport:
        """审计一组 Fact，输出 GuardReport。"""
        fact_list = list(facts)
        report = GuardReport(total=len(fact_list))
        for f in fact_list:
            if f.is_inference:
                report.inference_count += 1
                report.inference_facts.append(f)
            else:
                report.grounded_count += 1

        report.grounded_only = report.inference_count == 0

        if report.inference_count > 0:
            report.needs_hedge = True
            report.notes.append(
                f"{report.inference_count} inference fact(s) detected; "
                "lower certainty required."
            )

        if report.total > 0:
            ratio = report.grounded_count / report.total
            if ratio < self.min_grounded_ratio:
                report.needs_refusal = True
                report.notes.append(
                    f"grounded_ratio={ratio:.2f} < "
                    f"min_grounded_ratio={self.min_grounded_ratio:.2f}; "
                    "refusal triggered."
                )

        if report.total == 0:
            report.notes.append("no facts provided; nothing to audit.")

        return report

    # ---------------------------------------------------------
    # 工具
    # ---------------------------------------------------------
    def is_grounded_only(self, facts: Iterable[Fact]) -> bool:
        """全部 Fact 是否均为 grounded。"""
        return all(not f.is_inference for f in facts)

    def has_inference(self, facts: Iterable[Fact]) -> bool:
        """是否存在 INFERENCE 类型的 Fact。"""
        return any(f.is_inference for f in facts)

    def grounded_facts(self, facts: Iterable[Fact]) -> List[Fact]:
        return [f for f in facts if not f.is_inference]

    def inference_facts(self, facts: Iterable[Fact]) -> List[Fact]:
        return [f for f in facts if f.is_inference]

    # ---------------------------------------------------------
    # 包装回复
    # ---------------------------------------------------------
    def wrap_reply(
        self,
        facts: Iterable[Fact],
        draft: str,
    ) -> str:
        """根据 Fact 来源审计结果，对 draft 文本做包装。

        规则：
        - 若 needs_refusal=True：直接返回 refusal_text。
        - 若 needs_hedge=True：在 draft 前后分别加入 hedge 前缀 / 后缀。
        - 否则：原样返回 draft。
        """
        report = self.check(facts)
        if report.needs_refusal:
            return self.refusal_text
        if report.needs_hedge:
            return f"{self.hedge_prefix}{draft}{self.hedge_suffix}"
        return draft

    # ---------------------------------------------------------
    # 显式断言
    # ---------------------------------------------------------
    def assert_no_speculation(self, facts: Iterable[Fact]) -> None:
        """强约束：禁止 INFERENCE 混入。违规则抛 ValueError。"""
        offenders = [f for f in facts if f.is_inference]
        if offenders:
            ids = ", ".join(f.id for f in offenders)
            raise ValueError(
                f"Speculation is forbidden in this context; "
                f"inference fact(s) detected: {ids}"
            )


__all__ = [
    "PerceptionGuard",
    "GuardReport",
    "DEFAULT_HEDGE_PREFIX_ZH",
    "DEFAULT_HEDGE_SUFFIX_ZH",
    "DEFAULT_REFUSAL_ZH",
    "DEFAULT_HEDGE_PREFIX_EN",
    "DEFAULT_HEDGE_SUFFIX_EN",
    "DEFAULT_REFUSAL_EN",
]
