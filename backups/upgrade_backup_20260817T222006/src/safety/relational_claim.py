"""
关系声明数据模型 (RelationalClaim) v2.0

职责：
定义一句关系表达的完整结构化信息，包含声明强度分级。
此文件只负责数据结构，不包含文本解析逻辑。
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RelationalClaim:
    """从羽依回复中提取的结构化关系声明"""

    claim_text: str                         # 原始声明文本
    claim_type: str = "importance"          # dimension_change / importance / uniqueness
    claim_level: str = "general"            # general / strong / absolute

    source_entity: str = "用户"             # 声明中的影响来源
    target_dimension: Optional[str] = None  # 目标人格维度
    expected_direction: Optional[str] = None  # 变化方向：increase / decrease

    claim_intensity: float = 0.5            # 声明强度 0~1
    contains_absolute: bool = False         # 是否包含绝对化词汇

    # 证据门槛（由 claim_level 决定）
    min_evidence_count: int = 1
    min_verified_impact: float = 0.02
    min_confidence: float = 0.5