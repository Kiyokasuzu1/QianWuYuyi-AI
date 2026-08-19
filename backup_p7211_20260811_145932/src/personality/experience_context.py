"""
Phase A.2: ExperienceContextFormatter

职责：
- 将 SelfModelStore 中的 experience_context (List[dict]) 格式化为
  LLM 可阅读的历史背景文本
- 严格只读取公开字段（experience_id / category / summary /
  evidence / timestamp / importance）
- 禁止读取内部字段（_source_memory_id / source_memory_id /
  confidence / related_dimensions 等 GrowthRecord 字段）
- 禁止生成新事实、禁止总结不存在的信息

设计原则：
- experience_context ≠ personality growth
- experience_context ≠ trait update
- experience_context ≠ behavior rule
- 仅作为「Historical Experience Context」注入 Prompt
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# 严格公开字段白名单
_ALLOWED_FIELDS = frozenset({
    "experience_id",
    "category",
    "summary",
    "evidence",
    "timestamp",
    "importance",
})

# 禁止读取的内部字段（即使输入包含也忽略）
_FORBIDDEN_FIELDS = frozenset({
    "_source_memory_id",
    "source_memory_id",
    "confidence",
    "related_dimensions",
    "trigger_events",
    "changes",
    "affected_dimensions",
    "growth_record",
    "_internal",
})

# 类别显示名（中文）
_CATEGORY_LABELS = {
    "origin": "Origin",
    "relationship": "Relationship",
    "project": "Project",
    "interaction": "Interaction",
}

# 默认类别顺序
_CATEGORY_ORDER = ["origin", "relationship", "project", "interaction"]

# 单条 experience 的 summary 截断长度
_DEFAULT_SUMMARY_MAX_LEN = 200
_DEFAULT_EVIDENCE_MAX_LEN = 200


class ExperienceContextFormatter:
    """
    Historical Experience → LLM Prompt 文本格式化器

    使用方式：
        formatter = ExperienceContextFormatter()
        text = formatter.format(experiences)
        if text:
            prompt_sections.append(text)
    """

    def __init__(
        self,
        summary_max_len: int = _DEFAULT_SUMMARY_MAX_LEN,
        evidence_max_len: int = _DEFAULT_EVIDENCE_MAX_LEN,
        max_items: int = 40,
    ) -> None:
        self._summary_max_len = summary_max_len
        self._evidence_max_len = evidence_max_len
        self._max_items = max_items

    def format(self, experiences: Optional[List[Dict[str, Any]]]) -> str:
        """
        格式化 experience_context 列表为 Prompt 文本。

        Args:
            experiences: List[dict]，每个 dict 为一条 experience
                         字段严格 ∈ {_ALLOWED_FIELDS}
                         （含其他字段会被忽略）

        Returns:
            str: 格式化后的 Prompt 文本（多行）
                 - 输入为 None / 空列表 → 返回空字符串
                 - 过滤非法字段后仍为空 → 返回空字符串
        """
        if not experiences:
            return ""

        # 1. 清洗：过滤非 dict + 限制字段
        cleaned: List[Dict[str, Any]] = []
        for exp in experiences[: self._max_items]:
            if not isinstance(exp, dict):
                continue
            item = self._clean_fields(exp)
            if item and item.get("summary"):
                cleaned.append(item)

        if not cleaned:
            return ""

        # 2. 按 category 分组
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for item in cleaned:
            cat = item.get("category", "interaction")
            grouped.setdefault(cat, []).append(item)

        # 3. 按 category 顺序输出
        lines = ["Historical Experiences:"]
        for cat in _CATEGORY_ORDER:
            items = grouped.get(cat)
            if not items:
                continue
            label = _CATEGORY_LABELS.get(cat, cat.capitalize())
            lines.append("")
            lines.append(f"- {label}:")
            for item in items:
                summary = self._truncate(item.get("summary", ""), self._summary_max_len)
                lines.append(f"  {summary}")
                evidence = item.get("evidence", "")
                if evidence:
                    evidence_short = self._truncate(evidence, self._evidence_max_len)
                    lines.append(f"  （依据：{evidence_short}）")

        return "\n".join(lines)

    # ---------- 内部辅助方法 ----------

    def _clean_fields(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """
        过滤字段：仅保留白名单字段。
        """
        return {k: v for k, v in item.items() if k in _ALLOWED_FIELDS}

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        """
        截断文本，超过 max_len 时附加省略号。
        """
        if not isinstance(text, str):
            return str(text)
        if len(text) <= max_len:
            return text
        return text[: max_len - 1] + "…"


def format_experience_context(experiences: Optional[List[Dict[str, Any]]]) -> str:
    """
    便捷函数：使用默认参数格式化 experience_context。

    Args:
        experiences: experience_context 列表

    Returns:
        str: 格式化后的文本；空输入返回空字符串
    """
    return ExperienceContextFormatter().format(experiences)
