# -*- coding: utf-8 -*-
"""
Relationship Facts Renderer — P4.2-IMPL-C6C

职责：
将 RelationshipSnapshot 中经过筛选的关系事实渲染为中文定性描述，
作为 Prompt【用户关系】块的唯一正式数据源。

设计边界（红线）：
1. 只消费 RelationshipSnapshot（contracts 对象，属性访问），不读任何 RelationshipState；
2. 只输出定性档位（中文标签），绝不输出原始数值；
3. 禁止任何合成公式（trust*0.7 / max(bond, collaboration) / 数值平均）；
4. current 与 long_term 不混用：
   - current 只取 relationship_stage（阶段字符串，本就是 v3.5.27 的标签字段）；
   - 长期档位全部来自 long_term（v0.6 权威）；
   - 绝不读取 current.trust / current.familiarity 参与长期档位判断；
5. bond_strength 不渲染——其表达语义已由 CommunicationStyle（addressing /
   intimacy_bond）承载，这里重复渲染会造成同一事实双重注入；
6. 输出是"事实参考"，不是"行为指令"（无应该/必须类措辞）；
7. snapshot 为 None → 返回空字符串（调用方走 legacy fallback）。
"""

from typing import Optional

from src.contracts.relationship_snapshot import RelationshipSnapshot


# 阶段标签映射（v3.5.27 current.relationship_stage → 中文）
_STAGE_LABELS = {
    "initial": "初步接触",
    "developing": "正在发展",
    "stable": "稳定互动",
    "deep_collaboration": "深度协作",
}


def _familiarity_band(v: float) -> str:
    if v < 0.3:
        return "初识不久"
    if v < 0.6:
        return "逐渐熟悉"
    if v < 0.8:
        return "相当熟悉"
    return "非常熟悉"


def _trust_band(v: float) -> str:
    if v < 0.3:
        return "信任尚浅"
    if v < 0.6:
        return "信任正在建立"
    if v < 0.8:
        return "信任稳固"
    return "深度信任"


def _shared_history_band(v: float) -> str:
    if v < 0.1:
        return "共同经历尚少"
    if v < 0.4:
        return "有一些共同经历"
    return "积累了不少共同经历"


def render_relationship_facts(snapshot: Optional[RelationshipSnapshot]) -> str:
    """渲染筛选后的关系事实（不含【用户关系】块头，由 Prompt 层添加）。

    Args:
        snapshot: contracts RelationshipSnapshot（可为 None）

    Returns:
        中文事实行文本；snapshot 缺失或读取失败返回 ""（调用方走 legacy fallback）。
    """
    if snapshot is None:
        return ""
    try:
        lt = snapshot.long_term
        cur = snapshot.current

        lines = []

        stage = getattr(cur, "relationship_stage", "")
        stage_label = _STAGE_LABELS.get(str(stage or ""), "")
        if stage_label:
            lines.append(f"关系阶段：{stage_label}")

        familiarity = float(getattr(lt, "familiarity", 0.0))
        lines.append(f"长期熟悉度：{_familiarity_band(familiarity)}")

        trust = float(getattr(lt, "trust", 0.0))
        lines.append(f"长期信任：{_trust_band(trust)}")

        shared_history = float(getattr(lt, "shared_history", 0.0))
        lines.append(f"共同经历：{_shared_history_band(shared_history)}")

        if not lines:
            return ""
        return "\n".join(lines)
    except Exception:
        return ""
