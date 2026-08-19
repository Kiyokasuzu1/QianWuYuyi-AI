"""
反思记录 (ReflectionRecord) v1.1

职责：
定义一次人格反思的结果结构，记录分析的成长记录、发现的新模式、
候选升级特质，以及生成的自我总结。

v1.1 修正：
- upgraded_traits 改为 trait_candidates（仅候选，不直接修改）
- 增加 source_dimensions 用于追溯影响范围
"""

from typing import TypedDict, List


class ReflectionRecord(TypedDict, total=False):
    """人格反思记录"""

    # 标识
    record_id: str                  # 反思记录唯一ID
    timestamp: str                  # 反思发生时间

    # 输入
    analyzed_records: List[str]     # 本次分析过的 PersonalityGrowthRecord ID
    source_dimensions: List[str]    # 本次分析涉及的人格维度

    # 发现
    discovered_patterns: List[str]  # 发现的成长模式，如“创造力持续提升”
    trait_candidates: List[str]     # 达到升级条件的维度（需 Resolver 最终决定）

    # 输出
    self_summary: str               # 第一人称总结：“我发现自己越来越...”
    confidence: float               # 对本次反思结果的整体可信度

    # 元数据
    reflection_level: str           # short_term / long_term