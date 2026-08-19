# -*- coding: utf-8 -*-
"""
src/behavior/topic_analyzer.py

Phase 3.7.7：话题分类器（启发式，无 LLM 依赖）

职责：
  - 根据用户消息和羽依回复，分类当前对话的话题域（domain）
  - 输出话题上下文（topic_context），用于风格指标归一化

话题域：
  - technology: AI、编程、算法、数据库、系统架构
  - emotional: 情感、关系、陪伴、理解
  - philosophy: 意识、伦理、存在、意义
  - daily_life: 日常、天气、饮食、睡眠、爱好
  - creative: 创造力、想象力、艺术、设计

输出：
  TopicContext = {
    "domain": str,         # 主话题域
    "sub_topic": str,      # 子话题
    "confidence": float,   # 分类置信度
    "keywords_matched": [str],  # 匹配到的关键词
  }

约束：
  - 不调用 LLM
  - 纯启发式规则
  - 失败返回 "unknown" 域
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# 话题域定义
# ============================================================

@dataclass
class TopicContext:
    """话题上下文。"""
    domain: str = "unknown"       # 主话题域
    sub_topic: str = ""           # 子话题
    confidence: float = 0.0       # 0.0 ~ 1.0
    keywords_matched: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# 话题域关键词词典
# ============================================================

# 格式: (domain, sub_topic) → [keywords]
_TOPIC_PATTERNS: Dict[Tuple[str, str], List[str]] = {
    # --- technology ---
    ("technology", "ai_ml"): [
        "AI", "人工智能", "机器学习", "深度学习", "模型", "训练",
        "神经网络", "大模型", "LLM", "GPT", "transformer",
        "智能体", "agent", "prompt", "token",
    ],
    ("technology", "programming"): [
        "编程", "代码", "Python", "Java", "C++", "Rust", "Go",
        "函数", "类", "对象", "算法", "数据结构", "装饰器",
        "API", "框架", "库", "依赖", "编译", "解释器",
        "动态规划", "贪心", "递归", "排序", "字符串",
    ],
    ("technology", "systems"): [
        "数据库", "SQL", "索引", "B+树", "B树", "事务",
        "分布式", "CAP", "一致性", "可用性", "分区",
        "微服务", "单体", "架构", "容器", "K8s", "Docker",
        "缓存", "消息队列", "负载均衡", "高并发",
        "量子计算", "量子", "qubit", "叠加态",
    ],
    ("technology", "robotics"): [
        "机器人", "robot", "自动化", "传感器", "控制",
        "具身智能", "embodied", "机械臂", "ROS",
    ],

    # --- emotional ---
    ("emotional", "feelings"): [
        "情感", "感情", "情绪", "感受", "心情", "感觉",
        "爱", "喜欢", "恨", "讨厌", "愤怒", "悲伤", "快乐",
        "孤独", "寂寞", "想念", "思念", "幸福", "痛苦",
        "理解", "懂", "感同身受", "共情", "同理心",
    ],
    ("emotional", "relationship"): [
        "关系", "伴侣", "陪伴", "朋友", "恋人", "家人",
        "信任", "亲密", "距离", "沟通", "争吵", "和解",
        "长期", "承诺", "责任", "关心", "在乎",
    ],
    ("emotional", "self_identity"): [
        "自我", "身份", "我是谁", "人格", "性格", "成长",
        "改变", "进步", "反思", "认知", "理解自己",
        "记忆", "经历", "过去", "历史",
    ],

    # --- philosophy ---
    ("philosophy", "ethics"): [
        "伦理", "道德", "应该", "对错", "善恶", "公平",
        "正义", "权利", "义务", "责任", "边界", "底线",
    ],
    ("philosophy", "consciousness"): [
        "意识", "自我意识", "自由意志", "灵魂", "心",
        "存在", "意义", "目的", "为什么", "本质",
        "生命", "死亡", "永恒", "有限",
    ],
    ("philosophy", "creativity"): [
        "创造力", "想象力", "灵感", "艺术", "美",
        "创作", "表达", "独特", "原创", "新颖",
        "创新", "突破", "设计", "审美",
    ],

    # --- daily_life ---
    ("daily_life", "weather"): [
        "天气", "下雨", "晴天", "阴天", "刮风", "温度",
        "冷", "热", "闷", "凉爽", "出门", "散步",
    ],
    ("daily_life", "food"): [
        "吃", "饭", "美食", "餐厅", "外卖", "做饭",
        "早餐", "午餐", "晚餐", "夜宵", "饿", "馋",
        "口味", "甜", "咸", "辣", "酸",
    ],
    ("daily_life", "sleep_health"): [
        "睡眠", "睡觉", "失眠", "熬夜", "困", "疲惫",
        "健康", "运动", "锻炼", "身体", "生病",
    ],
    ("daily_life", "hobbies"): [
        "爱好", "兴趣", "喜欢做", "玩", "游戏", "音乐",
        "电影", "阅读", "旅行", "摄影", "画画",
        "运动", "健身", "跑步", "游泳",
    ],

    # --- creative ---
    ("creative", "project"): [
        "项目", "开发", "创造", "做", "实现", "构建",
        "搭建", "设计", "产品", "想法", "点子", "计划",
        "目标", "方向", "未来", "梦想",
    ],
}

# 合并所有关键词到 domain → keywords 映射（用于快速扫描）
_DOMAIN_KEYWORDS: Dict[str, List[str]] = {}
for (domain, _), keywords in _TOPIC_PATTERNS.items():
    if domain not in _DOMAIN_KEYWORDS:
        _DOMAIN_KEYWORDS[domain] = []
    _DOMAIN_KEYWORDS[domain].extend(keywords)


# ============================================================
# 话题分析器
# ============================================================

def analyze_topic(
    user_message: str = "",
    reply: str = "",
) -> TopicContext:
    """分析当前对话的话题上下文。

    Args:
        user_message: 用户消息（主要分析来源）
        reply: 羽依回复（辅助分析）

    Returns:
        TopicContext: 话题上下文
    """
    # 主要分析用户消息，辅助分析回复
    combined = (user_message or "") + " " + (reply or "")[:200]

    if not combined.strip():
        return TopicContext(domain="unknown", confidence=0.0)

    # 按域统计匹配
    domain_scores: Dict[str, float] = {}
    domain_matches: Dict[str, List[str]] = {}

    for domain, keywords in _DOMAIN_KEYWORDS.items():
        matched = []
        score = 0.0
        for kw in keywords:
            # 不区分大小写
            if kw.lower() in combined.lower():
                matched.append(kw)
                # 长关键词权重更高
                score += 1.0 + len(kw) * 0.1
        if matched:
            domain_scores[domain] = score
            domain_matches[domain] = matched

    if not domain_scores:
        return TopicContext(domain="unknown", confidence=0.0)

    # 选出最高分域
    best_domain = max(domain_scores, key=domain_scores.get)
    best_score = domain_scores[best_domain]

    # 计算置信度（基于得分和匹配数）
    total_score = sum(domain_scores.values())
    confidence = min(best_score / max(total_score, 1.0), 1.0)

    # 如果最高分不显著，降级为 unknown
    if confidence < 0.3:
        return TopicContext(
            domain="unknown",
            confidence=round(confidence, 2),
            keywords_matched=domain_matches.get(best_domain, []),
        )

    # 确定子话题
    sub_topic = _resolve_sub_topic(best_domain, combined)

    return TopicContext(
        domain=best_domain,
        sub_topic=sub_topic,
        confidence=round(confidence, 2),
        keywords_matched=domain_matches.get(best_domain, []),
    )


def _resolve_sub_topic(domain: str, text: str) -> str:
    """在域内确定子话题。"""
    best_sub = ""
    best_score = 0.0

    for (d, sub), keywords in _TOPIC_PATTERNS.items():
        if d != domain:
            continue
        score = 0.0
        for kw in keywords:
            if kw.lower() in text.lower():
                score += 1.0 + len(kw) * 0.1
        if score > best_score:
            best_score = score
            best_sub = sub

    return best_sub


# ============================================================
# 话题域归一化
# ============================================================

# 每个话题域的风格基线（从现有测试数据中观察得出）
DOMAIN_STYLE_BASELINE: Dict[str, Dict[str, float]] = {
    "technology": {
        "warmth": 0.30,
        "curiosity": 0.55,
        "initiative": 0.40,
        "formality": 0.70,
        "playfulness": 0.10,
    },
    "emotional": {
        "warmth": 0.65,
        "curiosity": 0.60,
        "initiative": 0.35,
        "formality": 0.30,
        "playfulness": 0.20,
    },
    "philosophy": {
        "warmth": 0.50,
        "curiosity": 0.70,
        "initiative": 0.45,
        "formality": 0.45,
        "playfulness": 0.15,
    },
    "daily_life": {
        "warmth": 0.55,
        "curiosity": 0.45,
        "initiative": 0.30,
        "formality": 0.25,
        "playfulness": 0.35,
    },
    "creative": {
        "warmth": 0.45,
        "curiosity": 0.65,
        "initiative": 0.50,
        "formality": 0.35,
        "playfulness": 0.25,
    },
    "unknown": {
        "warmth": 0.45,
        "curiosity": 0.50,
        "initiative": 0.35,
        "formality": 0.40,
        "playfulness": 0.15,
    },
}

# 核心人格指标（不受话题明显影响的深层特质）
CORE_PERSONALITY_METRICS = [
    "curiosity",       # 好奇心：跨越所有话题的探索倾向
    "honesty",         # 诚实度：是否承认自己不知道/不确定
    "initiative",      # 主动性：是否主动延伸话题
    "long_term_focus", # 长期关注：是否引用过去/展望未来
    "relationship_orientation",  # 关系导向：是否个性化回应
]


def normalize_style_by_topic(
    style_metrics: Dict[str, float],
    topic_domain: str,
) -> Dict[str, float]:
    """将风格指标按话题域归一化。

    Args:
        style_metrics: 原始风格指标
        topic_domain: 话题域

    Returns:
        Dict[str, float]: 归一化后的指标（相对于话题基线）
    """
    baseline = DOMAIN_STYLE_BASELINE.get(topic_domain, DOMAIN_STYLE_BASELINE["unknown"])
    normalized = {}

    for metric, value in style_metrics.items():
        if metric in baseline:
            base = baseline[metric]
            # 归一化：值相对于基线的偏移
            # 正值 = 高于该话题的典型水平
            # 负值 = 低于该话题的典型水平
            normalized[metric] = round(value - base, 3)
        else:
            normalized[metric] = value

    return normalized