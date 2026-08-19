"""
成长记录 (GrowthRecord) v1.0

职责：
定义经历产生的成长影响数据结构。
GrowthRecord 是 Event 和 PersonalityResolver 之间的"中间事实层"。

设计原则：
- Event 是经历，GrowthRecord 是经历产生的成长影响
- PersonalityResolver 是最终人格计算器，读取所有 GrowthRecord 计算当前人格
- 关系事件永远不产生 GrowthRecord（在 Evaluator 阶段已被拦截）
"""

from typing import Dict, TypedDict, List, Optional


class GrowthRecord(TypedDict, total=False):
    """成长记录数据结构"""

    # ---- 标识 ----
    record_id: str                          # 记录唯一标识
    source_event_id: str                    # 来源事件ID

    # ---- 成长信号 ----
    growth_signal: str                      # 成长信号（如 creative_activity_interest）
    source_type: str                        # 来源类型（creation / preference / identity / milestone）
    growth_level: str                       # 成长层级（context / preference / trait）

    # ---- 影响 ----
    affected_dimensions: Dict[str, float]   # 影响的人格维度及变化量 {"creativity": 0.003}
    confidence: float                       # 评估可信度

    # ---- 追溯 ----
    reason: str                             # 为什么产生这个成长（可调试）
    created_at: str                         # 创建时间

    # ---- 状态 ----
    applied: bool                           # 是否已被 PersonalityResolver 应用（默认 False）
    applied_at: Optional[str]               # 应用时间
    decay_rate: Optional[float]             # 衰减率（未来预留，暂时不使用）


# ============================================================
# 辅助函数
# ============================================================

def create_growth_record(
    record_id: str,
    source_event_id: str,
    growth_signal: str,
    source_type: str,
    growth_level: str,
    affected_dimensions: Dict[str, float],
    confidence: float,
    reason: str,
    created_at: str,
) -> GrowthRecord:
    """创建一条新的成长记录（未应用状态）"""
    return {
        "record_id": record_id,
        "source_event_id": source_event_id,
        "growth_signal": growth_signal,
        "source_type": source_type,
        "growth_level": growth_level,
        "affected_dimensions": affected_dimensions,
        "confidence": confidence,
        "reason": reason,
        "created_at": created_at,
        "applied": False,
        "applied_at": None,
        "decay_rate": None,
    }