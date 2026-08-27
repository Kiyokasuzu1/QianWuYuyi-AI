# -*- coding: utf-8 -*-
"""
src/temporal/timeline_view.py

Phase B1c.2 — Narrative Timeline View（纯展示层）

边界（冻结）：
    ✅ 只读取传入的 snapshots 数据，生成结构化时间线文本（纯派生视图）
    ✅ 时间解析一律经 temporal_core.normalize_time（禁止自行解析时间）
    ✅ 分桶/排序/上限一律经 temporal_core.build_timeline（B1a 冻结接口）
    ❌ 不 import 任何 src 业务模块（memory/growth/narrative/llm/config/storage）
    ❌ 不读配置、不写文件、不修改任何输入数据
    ❌ 禁止：总结 / 改写 / LLM 生成 / 推测意义 / "时间未知" / fallback 故事

内容规则（冻结）：
    - 标签优先取 major_changes 逐条原文；无则取 narrative_text 第一行（≤60 字符）
    - 非法 timestamp → 跳过该条；空数据 → 空字符串
    - 输出硬截断 max_chars
"""
from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple

from src.temporal.temporal_core import build_timeline, normalize_time

DEFAULT_MAX_ITEMS: int = 10
DEFAULT_MAX_CHARS: int = 600
_FALLBACK_LINE_LIMIT: int = 60


def _snap_field(snap: Any, name: str) -> Any:
    """dict 或属性访问兼容读取。"""
    if isinstance(snap, dict):
        return snap.get(name)
    return getattr(snap, name, None)


def _extract_labels(snap: Any) -> List[str]:
    """标签来源：major_changes 原文逐行；无则 narrative_text 第一行 ≤60 字符。"""
    changes = _snap_field(snap, "major_changes")
    labels: List[str] = []
    if isinstance(changes, (list, tuple)):
        for item in changes:
            if isinstance(item, str) and item.strip():
                labels.append(item.strip())
    if labels:
        return labels
    narrative = _snap_field(snap, "narrative_text")
    if isinstance(narrative, str) and narrative.strip():
        first_line = narrative.strip().splitlines()[0][:_FALLBACK_LINE_LIMIT]
        if first_line:
            labels.append(first_line)
    return labels


def build_narrative_timeline_view(
    snapshots: Sequence[Any],
    *,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """把叙事快照列表渲染为按月分桶的时间线文本（纯派生，不修改任何输入）。

    输入兼容：
        dict: {"timestamp": ..., "version": ..., "major_changes": [...], "narrative_text": ...}
        对象: 属性访问同上

    时间支持（经 normalize_time，禁止自行解析）：
        ISO naive / UTC Z / epoch int/float / aware datetime

    输出示例：
        2026-07
        - 建立羽依人格模型

        2026-08
        - 完成 Memory System

    约束：
        - 按用户时区月份分桶、时间升序、max_items 上限（build_timeline 冻结语义）
        - 非法 timestamp → 跳过该条；空数据 → 空字符串
        - 超出 max_chars → 硬截断
        - 禁止输出"时间未知"/推测文本/fallback 故事

    Args:
        snapshots: 快照列表（dict 或对象）
        max_items: 时间线条目上限（默认 10）
        max_chars: 输出字符硬上限（默认 600）

    Returns:
        时间线文本；空输入/全非法 → ""
    """
    if not snapshots:
        return ""

    entries: List[Tuple[Any, str, str]] = []
    for snap in snapshots:
        if snap is None:
            continue
        timestamp = _snap_field(snap, "timestamp")
        dt = normalize_time(timestamp)
        if dt is None:
            continue  # 非法 timestamp：跳过该条
        version = _snap_field(snap, "version")
        version_str = str(version) if version is not None else ""
        labels = _extract_labels(snap)
        for label in labels:
            entries.append((dt, label, version_str))

    timeline = build_timeline(entries, max_items=max_items)
    if not timeline:
        return ""

    # 按月分组（build_timeline 已按时间升序输出）
    groups: List[Tuple[str, List[str]]] = []
    for entry in timeline:
        if groups and groups[-1][0] == entry.group_label:
            groups[-1][1].append(entry.label)
        else:
            groups.append((entry.group_label, [entry.label]))

    text = "\n\n".join(
        f"{group_label}\n" + "\n".join(f"- {label}" for label in labels)
        for group_label, labels in groups
    )
    return text[:max_chars]
