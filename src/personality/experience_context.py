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

import re
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


# ============================================================
# P4.4-C：Prompt 注入治理（两链共用）
# ============================================================
# 背景（P4.4-A 审计）：恢复出的 experience summary/evidence 内嵌原始记忆头
# （"User ID: 366648462, Nickname: 清夏铃 Current datetime: ... --- BEGIN
# HISTORICAL MEMORY REFERENCE ---"），40 条 × 双份造成 Historical Experience
# 占 Orchestrator Prompt 89% token，且「清夏铃」出现 66 次淹没称呼信号。
# 治理只发生在 Prompt 注入前：数量上限 + 元数据清洗 + evidence 截断。
# 不修改原始 experience 数据、不修改 formatter 通用行为。

PROMPT_MAX_ITEMS = 10
PROMPT_EVIDENCE_MAX_LEN = 80

# 原始记忆元数据标记：命中任一处即视为元数据尾开始，其前内容保留为事件文本
_RAW_MEMORY_META_MARKERS = re.compile(
    r"(?:User\s*ID|Nickname|Current\s+datetime|Weekday)\s*:"
    r"|[-–—]{2,}\s*BEGIN\s+HISTORICAL\s+MEMORY\s+REFERENCE",
    re.IGNORECASE,
)


def _strip_raw_memory_metadata(text: str) -> str:
    """移除原始记忆头/元数据尾（User ID / Nickname / Current datetime /
    Weekday / BEGIN HISTORICAL MEMORY REFERENCE）。

    仅做格式清理：保留元数据标记之前的事件内容，不生成、不删除事实。
    """
    if not isinstance(text, str):
        return text
    match = _RAW_MEMORY_META_MARKERS.search(text)
    if match:
        text = text[: match.start()]
        # 仅在命中元数据时清理尾部残留分隔符（干净文本保持原样）
        return text.rstrip(" ,，:：;；\t\n\r")
    return text


def _safe_importance(value: Any) -> float:
    """importance 缺失/非法时按 0.0 参与排序（不影响其他条目）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def build_experience_context(
    experiences: Optional[List[Dict[str, Any]]],
    *,
    max_items: int = PROMPT_MAX_ITEMS,
    evidence_max_len: int = PROMPT_EVIDENCE_MAX_LEN,
) -> str:
    """P4.4-C：两条 Prompt 链共用的 experience 注入管道（含块头）。

    治理规则：
    1. 数量：仅取 importance 最高的 max_items 条；稳定排序，同 importance 时
       保持原有相对顺序（原有排序语义不变）
    2. 清洗：summary / evidence 移除原始记忆元数据（不删除事件内容）
    3. 截断：evidence 每条最多 evidence_max_len 字符（summary 维持 formatter 默认）
    4. 只读：对传入 dict 浅拷贝后清洗，不修改原始 Experience 数据
    5. 内部字段（_source_memory_id / confidence 等）依旧被白名单剔除

    Returns:
        str: 完整块文本（【Historical Experience Context】块头 + 内容）；
             无有效内容时返回空字符串。
    """
    if not experiences:
        return ""

    candidates: List[Dict[str, Any]] = []
    for exp in experiences:
        if not isinstance(exp, dict):
            continue
        item = {k: v for k, v in exp.items() if k in _ALLOWED_FIELDS}
        if item.get("summary"):
            candidates.append(item)

    if not candidates:
        return ""

    # 稳定排序：importance 降序；同值保持原相对顺序
    candidates.sort(
        key=lambda item: _safe_importance(item.get("importance")),
        reverse=True,
    )
    candidates = candidates[:max_items]

    for item in candidates:
        item["summary"] = _strip_raw_memory_metadata(item["summary"])
        if item.get("evidence"):
            item["evidence"] = _strip_raw_memory_metadata(item["evidence"])

    text = ExperienceContextFormatter(evidence_max_len=evidence_max_len).format(candidates)
    if not text:
        return ""
    return f"【Historical Experience Context】\n{text}"
