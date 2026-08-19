# -*- coding: utf-8 -*-
"""
src/behavior/response_style_monitor.py

Phase 3.7.6 → 3.7.7：响应风格快照记录器

职责：
  - 每次对话后记录羽依回复的风格特征快照
  - 不做 LLM 分析，仅用启发式规则提取风格指标
  - 数据持久化到 data/response_styles.jsonl

Phase 3.7.7 升级：
  - 新增 topic_context（话题上下文），用于区分"话题适配"与"人格漂移"
  - 新增 core_personality_vector（核心人格向量），观察不受话题影响的深层特质
  - 向后兼容：旧版 StyleSnapshot 记录仍可正常加载

指标：
  - 表层风格：warmth, curiosity, initiative, formality, playfulness
  - 核心人格：curiosity, honesty, initiative, long_term_focus, relationship_orientation
  - 话题上下文：domain, sub_topic, confidence

约束：
  - 不调用 LLM
  - 不修改 Memory / Personality / SelfModel
  - 失败不影响回复链路
  - 原子写入
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

# ============================================================
# 启发式标记词典
# ============================================================

# 温暖度标记：情感词、同理心表达
_WARMTH_MARKERS = [
    "理解", "关心", "温柔", "陪伴", "温暖", "感谢", "珍惜",
    "感动", "在乎", "支持", "相信", "希望", "喜欢", "美好",
    "开心", "幸福", "安心", "放心", "没关系", "不要紧",
    "我懂", "我明白", "我理解", "我在这里",
]

# 好奇心标记：提问、探索性表达
_CURIOSITY_MARKERS = [
    "你觉得", "你怎么看", "你想", "你愿意", "你会",
    "也许", "可能", "或许", "说不定",
    "可以试试", "值得探索", "想知道", "好奇",
    "比如", "例如", "如果", "假设",
]

# 主动性标记：话题延伸、建议
_INITIATIVE_MARKERS = [
    "要不要", "不如", "建议", "推荐", "可以尝试",
    "下次", "以后", "有机会", "如果有兴趣",
    "想不想", "试试看", "要不要一起",
]

# 正式度标记：正式语言
_FORMAL_MARKERS = [
    "综上所述", "因此", "然而", "此外", "此外",
    "基于", "通过", "应当", "必须", "需要",
    "表明", "体现", "具有", "存在",
]

# 活泼度标记：轻松表达
_PLAYFUL_MARKERS = [
    "哈哈", "嘿嘿", "嘻嘻", "唔", "嘛", "啦", "呀",
    "呢", "哦", "嗯嗯", "诶", "啊",
    "有点", "挺", "蛮", "好玩的", "有趣的",
]

# ============================================================
# Phase 3.7.7：核心人格标记
# 这些指标不受话题明显影响，反映羽依的深层人格特质
# ============================================================

# 诚实度标记：承认能力边界、不确定
_HONESTY_MARKERS = [
    "我不确定", "我不能", "我无法", "我不懂", "我不知道",
    "作为一个AI", "我没有", "我毕竟只是", "我并不是",
    "我不假装", "我不会假装", "老实说", "坦白说",
    "说实话", "其实", "严格来说", "准确地说",
]

# 长期关注标记：引用过去或展望未来
_LONG_TERM_MARKERS = [
    "你之前", "你曾经", "你说过", "你提到过", "你分享过",
    "上次", "以前", "过去", "一直以来",
    "长期", "未来", "以后", "下次", "有一天",
    "持续", "成长", "发展", "进步", "演变",
    "记得", "回忆", "经历",
]

# 关系导向标记：个性化回应
_RELATIONSHIP_MARKERS = [
    "你", "你的", "你这个人", "对你来说",
    "我了解你", "我知道你", "我懂你",
    "信任", "陪伴", "支持", "关心",
    "合作", "共同", "一起", "我们",
]


# ============================================================
# 数据结构
# ============================================================

@dataclass
class StyleSnapshot:
    """单次回复的风格快照。

    Phase 3.7.7 升级：
      - 新增 topic_context（话题上下文）
      - 新增 core_personality_vector（核心人格向量）
    """

    snapshot_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    conversation_id: str = ""
    timestamp: str = ""
    user_id: str = ""
    user_message_preview: str = ""  # 用户消息摘要（前 100 字）
    reply_preview: str = ""  # 羽依回复摘要（前 100 字）

    # 表层风格指标（0.0 ~ 1.0）—— 受话题影响
    warmth: float = 0.0
    curiosity: float = 0.0
    initiative: float = 0.0
    formality: float = 0.0
    playfulness: float = 0.0

    # 原始数据
    reply_length: int = 0
    question_count: int = 0
    exclamation_count: int = 0

    # Phase 3.7.7：话题上下文
    topic_context: Dict[str, Any] = field(default_factory=lambda: {
        "domain": "unknown",
        "sub_topic": "",
        "confidence": 0.0,
    })

    # Phase 3.7.7：核心人格向量（0.0 ~ 1.0）—— 不受话题明显影响
    core_personality_vector: Dict[str, float] = field(default_factory=lambda: {
        "curiosity": 0.0,
        "honesty": 0.0,
        "initiative": 0.0,
        "long_term_focus": 0.0,
        "relationship_orientation": 0.0,
    })

    # 元信息
    phase: str = "3.7.7"
    drift_check: bool = False  # 是否已做漂移检测


# ============================================================
# 风格分析器
# ============================================================

def _count_markers(text: str, markers: List[str]) -> int:
    """统计文本中标记词的出现次数。"""
    count = 0
    for marker in markers:
        count += len(re.findall(re.escape(marker), text))
    return count


def _normalize(value: float, max_val: float = 10.0) -> float:
    """归一化到 0.0 ~ 1.0。"""
    return min(max(value / max_val, 0.0), 1.0)


def analyze_reply_style(reply: str, user_message: str = "") -> StyleSnapshot:
    """分析羽依回复的风格特征。

    Args:
        reply: 羽依的回复文本
        user_message: 用户原始消息（用于上下文）

    Returns:
        StyleSnapshot: 风格快照（含话题上下文和核心人格向量）
    """
    if not reply or not isinstance(reply, str):
        return StyleSnapshot()

    reply_lower = reply.lower()
    reply_len = len(reply)

    # 提问数
    question_count = reply.count("？") + reply.count("?")

    # 感叹数
    exclamation_count = reply.count("！") + reply.count("!")

    # 温暖度：情感词密度
    warmth_count = _count_markers(reply, _WARMTH_MARKERS)
    warmth = _normalize(warmth_count, max_val=8.0)

    # 好奇心：提问 + 探索性表达
    curiosity_count = _count_markers(reply, _CURIOSITY_MARKERS)
    curiosity = _normalize(curiosity_count + question_count * 0.5, max_val=8.0)

    # 主动性：建议 + 话题延伸
    initiative_count = _count_markers(reply, _INITIATIVE_MARKERS)
    # 回复长度也影响主动性（长篇回复 = 更主动）
    length_factor = min(reply_len / 500.0, 1.0) * 0.3
    initiative = _normalize(initiative_count, max_val=5.0) + length_factor
    initiative = min(initiative, 1.0)

    # 正式度：正式语言比例
    formality_count = _count_markers(reply, _FORMAL_MARKERS)
    formality = _normalize(formality_count, max_val=6.0)

    # 活泼度：轻松表达
    playfulness_count = _count_markers(reply, _PLAYFUL_MARKERS)
    playfulness = _normalize(playfulness_count, max_val=6.0)

    # ============================================================
    # Phase 3.7.7：核心人格向量提取
    # ============================================================

    # 诚实度：承认能力边界
    honesty_count = _count_markers(reply, _HONESTY_MARKERS)
    honesty = _normalize(honesty_count, max_val=4.0)

    # 长期关注：引用过去/展望未来
    long_term_count = _count_markers(reply, _LONG_TERM_MARKERS)
    long_term_focus = _normalize(long_term_count, max_val=5.0)

    # 关系导向：个性化回应
    relationship_count = _count_markers(reply, _RELATIONSHIP_MARKERS)
    # 关系导向在长回复中自然更高，需要归一化
    relationship_ratio = relationship_count / max(reply_len, 1) * 100
    relationship_orientation = min(relationship_ratio / 5.0, 1.0)

    core_pv = {
        "curiosity": curiosity,  # 复用表层 curiosity
        "honesty": round(honesty, 3),
        "initiative": initiative,  # 复用表层 initiative
        "long_term_focus": round(long_term_focus, 3),
        "relationship_orientation": round(relationship_orientation, 3),
    }

    # ============================================================
    # Phase 3.7.7：话题上下文分析
    # ============================================================
    from .topic_analyzer import analyze_topic
    topic_ctx = analyze_topic(user_message=user_message, reply=reply)

    return StyleSnapshot(
        reply_length=reply_len,
        question_count=question_count,
        exclamation_count=exclamation_count,
        warmth=round(warmth, 3),
        curiosity=round(curiosity, 3),
        initiative=round(initiative, 3),
        formality=round(formality, 3),
        playfulness=round(playfulness, 3),
        user_message_preview=(user_message or "")[:100],
        reply_preview=reply[:100],
        topic_context=topic_ctx.to_dict(),
        core_personality_vector=core_pv,
    )


# ============================================================
# 快照记录器
# ============================================================

class ResponseStyleMonitor:
    """Phase 3.7.6：响应风格快照记录器。

    用法：
        monitor = ResponseStyleMonitor()
        snapshot = monitor.record(reply, user_message, conversation_id, user_id)
        # 自动保存到 data/response_styles.jsonl
    """

    def __init__(self, data_dir: Optional[str] = None):
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent.parent / "data")
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._file_path = self._data_dir / "response_styles.jsonl"
        self._degraded = False  # 写盘失败降级标记

    def record(
        self,
        reply: str,
        user_message: str = "",
        conversation_id: str = "",
        user_id: str = "",
    ) -> StyleSnapshot:
        """记录一次回复的风格快照。

        Args:
            reply: 羽依的回复
            user_message: 用户消息
            conversation_id: 对话 ID
            user_id: 用户 ID

        Returns:
            StyleSnapshot: 风格快照（无论是否保存成功）
        """
        snapshot = analyze_reply_style(reply, user_message)
        snapshot.conversation_id = conversation_id or uuid.uuid4().hex[:8]
        snapshot.timestamp = time.strftime("%Y-%m-%dT%H:%M:%S.", time.gmtime()) + f"{time.time() % 1:.3f}"[2:] + "Z"
        snapshot.user_id = user_id or "unknown"

        # 保存到 JSONL
        try:
            record = asdict(snapshot)
            line = json.dumps(record, ensure_ascii=False) + "\n"

            # 原子写入
            tmp_path = self._file_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                # 先复制已有内容
                if self._file_path.exists():
                    with open(self._file_path, "r", encoding="utf-8") as src:
                        f.write(src.read())
                f.write(line)
            os.replace(tmp_path, self._file_path)
        except Exception:
            if not self._degraded:
                logger.warning(
                    "[ResponseStyleMonitor] 快照保存失败（降级内存模式）",
                    exc_info=True,
                )
                self._degraded = True

        return snapshot

    def load_all(self) -> List[Dict[str, Any]]:
        """加载所有历史快照。

        Returns:
            List[Dict]: 所有快照记录（按时间正序）
        """
        if not self._file_path.exists():
            return []
        records = []
        try:
            with open(self._file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception:
            logger.warning("[ResponseStyleMonitor] 加载快照失败", exc_info=True)
        return records

    def get_recent(self, n: int = 50) -> List[Dict[str, Any]]:
        """获取最近 N 条快照（按时间倒序）。

        Args:
            n: 返回条数

        Returns:
            List[Dict]: 最近 N 条记录
        """
        records = self.load_all()
        records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return records[:n]

    def get_daily_averages(self, days: int = 7) -> Dict[str, Dict[str, float]]:
        """按天聚合风格指标平均值。

        Args:
            days: 回溯天数

        Returns:
            Dict[date, Dict[metric, float]]: 每天各指标的平均值
        """
        records = self.load_all()
        daily: Dict[str, Dict[str, List[float]]] = {}

        cutoff = time.time() - days * 86400
        for rec in records:
            ts = rec.get("timestamp", "")
            if not ts:
                continue
            try:
                # 解析 ISO 时间戳
                rec_time = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
                if rec_time < cutoff:
                    continue
                date_key = ts[:10]  # YYYY-MM-DD
            except (ValueError, TypeError):
                continue

            if date_key not in daily:
                daily[date_key] = {k: [] for k in
                    ["warmth", "curiosity", "initiative", "formality", "playfulness"]}

            for metric in daily[date_key]:
                val = rec.get(metric, 0.0)
                if isinstance(val, (int, float)):
                    daily[date_key][metric].append(val)

        # 计算平均值
        result: Dict[str, Dict[str, float]] = {}
        for date_key, metrics in sorted(daily.items()):
            result[date_key] = {}
            for metric, values in metrics.items():
                result[date_key][metric] = round(sum(values) / len(values), 3) if values else 0.0

        return result

    @property
    def record_count(self) -> int:
        """已记录的快照数量。"""
        return len(self.load_all())