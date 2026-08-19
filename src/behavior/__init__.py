"""Behavior Layer —— 自我模型 → 行为策略的转换层。

Phase 3.7.2: 将 SelfModel 的 traits/preferences/core_values
翻译为可注入 Prompt 的行为指导，使羽依的回复风格反映其人格特质。

Phase 3.7.6: 新增响应风格监控（ResponseStyleMonitor）和
人格漂移检测（PersonalityDriftDetector），用于长期运行观察。

Phase 3.7.7: 升级观测系统
  - 新增 TopicAnalyzer：话题分类器（启发式，无 LLM 依赖）
  - StyleSnapshot 新增 topic_context + core_personality_vector
  - PersonalityDriftDetector 新增 check_same_topic_drift() + check_core_personality_drift()
"""
from .behavior_guidance import build_behavior_guidance, format_behavior_guidance_block
from .response_style_monitor import ResponseStyleMonitor, analyze_reply_style, StyleSnapshot
from .personality_drift_detector import PersonalityDriftDetector, DriftReport, DriftAlert
from .style_comparator import StyleComparator, ComparisonReport, MetricChange
from .topic_analyzer import TopicContext, analyze_topic, normalize_style_by_topic, DOMAIN_STYLE_BASELINE

__all__ = [
    "build_behavior_guidance",
    "format_behavior_guidance_block",
    "ResponseStyleMonitor",
    "analyze_reply_style",
    "StyleSnapshot",
    "PersonalityDriftDetector",
    "DriftReport",
    "DriftAlert",
    "StyleComparator",
    "ComparisonReport",
    "MetricChange",
    "TopicContext",
    "analyze_topic",
    "normalize_style_by_topic",
    "DOMAIN_STYLE_BASELINE",
]