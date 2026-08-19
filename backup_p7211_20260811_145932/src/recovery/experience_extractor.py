"""
Phase A.1: ExperienceExtractor

职责：
读取 data/memory.json，归一化文本，剥离 XML 标签，按关键词分类，计算 importance，
最终输出 List[HistoricalExperience]。

输入：
    memory_path: data/memory.json 路径（list[dict]）

输出：
    List[dict]，每个 dict 包含：
        _source_memory_id: str  # 内部字段，仅 Extractor→Loader 之间使用
        experience_id: str       # exp_<uuid8>
        category: str            # origin | relationship | project | interaction
        summary: str             # ≤ 60 字
        evidence: str            # ≤ 200 字（已剥离 XML）
        timestamp: str           # ISO 8601
        importance: float        # 0.0 ~ 1.0

约束：
- 只读 memory.json，不修改任何数据文件
- 不 import personality_growth_record / growth / growth_state
- 严格 if/elif/else 分类：origin > relationship > project > interaction
- importance 排序 DESC，timestamp 次排序 DESC
- 最大 40 条输出
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union


# ---------- 关键词定义 ----------

ORIGIN_KEYWORDS: Set[str] = {
    "羽依", "浅雾羽依", "创建", "诞生", "身份", "名字",
    "为什么存在", "是谁", "你是谁", "你的名字", "你的来历",
    "第一次创建", "第一次见面", "初始设定", "核心", "存在意义",
}

RELATIONSHIP_KEYWORDS: Set[str] = {
    "清夏铃", "陪伴", "一起", "记住", "不要忘记",
    "回来", "重新回来", "一直", "信任",
}

PROJECT_KEYWORDS: Set[str] = {
    "项目", "代码", "架构", "Phase", "系统",
    "Memory", "Growth", "模块", "版本", "仓库",
}

CONTINUITY_KEYWORDS: Set[str] = {
    "以后", "一直", "第一次", "重新", "回来",
}

# 纯问候黑名单（丢弃）
GREETING_BLACKLIST: Set[str] = {
    "你好", "您好", "hi", "HI", "Hi", "hello", "Hello",
    "晚安", "早安", "午安", "在吗", "测试", "test", "Test",
}

# XML 标签剥离（外层包装，保留内部真实内容）
XML_TAG_PATTERN = re.compile(
    r"</?(?:system_reminder|RAG-Faiss-Memory|extra_instruction)>",
    re.IGNORECASE,
)

# summary 截断长度
SUMMARY_MAX_LEN = 60
EVIDENCE_MAX_LEN = 200

# 最大输出条数
MAX_EXPERIENCES = 40

# importance 计算常量
IMPORTANCE_BASE = 0.3
IMPORTANCE_ORIGIN = 0.25
IMPORTANCE_RELATIONSHIP = 0.25
IMPORTANCE_PROJECT = 0.10
IMPORTANCE_CONTINUITY = 0.15
IMPORTANCE_SOURCE = 0.05
IMPORTANCE_MAX = 1.0


class ExperienceExtractor:
    """从 memory.json 提取 HistoricalExperience"""

    def __init__(self) -> None:
        # 预编译正则（性能优化）
        self._xml_pattern = XML_TAG_PATTERN

    # ---------- 公开方法 ----------

    def extract(self, memory_path: Union[str, Path]) -> List[Dict[str, Any]]:
        """
        主入口：读取 memory.json，输出 HistoricalExperience 列表（内部含 _source_memory_id）

        Args:
            memory_path: data/memory.json 路径

        Returns:
            List[dict]，按 importance DESC, timestamp DESC 排序，最多 40 条
        """
        memories = self._load_memory(memory_path)
        candidates: List[Dict[str, Any]] = []

        for mem in memories:
            exp = self._extract_single(mem)
            if exp is not None:
                candidates.append(exp)

        # 排序：importance DESC, timestamp DESC
        candidates.sort(
            key=lambda e: (e["importance"], e["timestamp"]),
            reverse=True,
        )

        # 截断
        return candidates[:MAX_EXPERIENCES]

    # ---------- 内部方法：加载 ----------

    def _load_memory(self, memory_path: Union[str, Path]) -> List[Dict[str, Any]]:
        """读取 memory.json，失败返回空列表（不抛错）"""
        try:
            path = Path(memory_path)
            if not path.exists():
                return []
            import json
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return []
            return [m for m in data if isinstance(m, dict)]
        except Exception:
            return []

    # ---------- 内部方法：单条提取 ----------

    def _extract_single(self, mem: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        提取单条 memory 为 HistoricalExperience

        Returns:
            合法 dict（含 _source_memory_id）或 None（应丢弃）
        """
        source_id = mem.get("id", "")
        if not source_id:
            return None  # 无 id 视为非法

        # 1. 文本归一化
        raw_text = self._normalize_content(mem.get("content", ""))

        # 2. 剥离 XML 标签
        text = self._strip_xml_tags(raw_text)

        # 3. 长度过滤（< 15 字符丢弃）
        if len(text.strip()) < 15:
            return None

        # 4. 纯问候过滤
        if self._is_greeting(text):
            return None

        # 5. 严格 if/elif/else 分类
        category, matched = self._classify(text)
        if category == "interaction" and not matched:
            # 无任何关键词命中 → 丢弃
            return None

        # 6. importance 计算
        source_importance = float(mem.get("importance", 0.0) or 0.0)
        importance = self._calc_importance(category, matched, source_importance)
        importance = min(importance, IMPORTANCE_MAX)

        # 7. 截断 summary / evidence
        summary = self._make_summary(text)[:SUMMARY_MAX_LEN]
        evidence = self._make_evidence(text)[:EVIDENCE_MAX_LEN]

        # 8. timestamp
        timestamp = str(mem.get("timestamp", ""))

        return {
            "_source_memory_id": source_id,
            "experience_id": f"exp_{uuid.uuid4().hex[:8]}",
            "category": category,
            "summary": summary,
            "evidence": evidence,
            "timestamp": timestamp,
            "importance": round(importance, 4),
        }

    # ---------- 内部方法：文本处理 ----------

    def _normalize_content(self, content: Any) -> str:
        """
        归一化 content 字段，支持 str 与 list[dict] 两种形态

        list[dict] 形态：仅拼接 {"type": "text", "text": "..."} 的 text 字段
        """
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text" and isinstance(item.get("text"), str):
                        parts.append(item["text"])
                    elif "text" in item and isinstance(item["text"], str):
                        # 兜底：含 text 字段即提取
                        parts.append(item["text"])
            return "\n".join(parts)
        return str(content) if content is not None else ""

    def _strip_xml_tags(self, text: str) -> str:
        """
        剥离外层 XML 标签，保留内部真实文本

        处理：
        - <system_reminder>...</system_reminder>
        - <RAG-Faiss-Memory>...</RAG-Faiss-Memory>
        - <extra_instruction>...</extra_instruction>
        """
        if not text:
            return ""
        # 一次性剥离所有匹配的外层标签
        return self._xml_pattern.sub("", text)

    def _is_greeting(self, text: str) -> bool:
        """检测是否为纯问候（无意义短消息）"""
        stripped = text.strip()
        if stripped in GREETING_BLACKLIST:
            return True
        # 短文本且无中文（视为测试消息）
        if len(stripped) <= 5 and not any("\u4e00" <= c <= "\u9fff" for c in stripped):
            return True
        return False

    # ---------- 内部方法：分类 ----------

    def _classify(self, text: str) -> Tuple[str, Set[str]]:
        """
        严格 if/elif/else 分类

        优先级：origin > relationship > project > interaction

        Returns:
            (category, matched_keywords_set)
        """
        if self._has_any(text, ORIGIN_KEYWORDS):
            return "origin", self._matched(text, ORIGIN_KEYWORDS)
        if self._has_any(text, RELATIONSHIP_KEYWORDS):
            return "relationship", self._matched(text, RELATIONSHIP_KEYWORDS)
        if self._has_any(text, PROJECT_KEYWORDS):
            return "project", self._matched(text, PROJECT_KEYWORDS)
        return "interaction", self._matched(text, ORIGIN_KEYWORDS | RELATIONSHIP_KEYWORDS | PROJECT_KEYWORDS)

    def _has_any(self, text: str, keywords: Set[str]) -> bool:
        return any(kw in text for kw in keywords)

    def _matched(self, text: str, keywords: Set[str]) -> Set[str]:
        return {kw for kw in keywords if kw in text}

    # ---------- 内部方法：importance ----------

    def _calc_importance(
        self,
        category: str,
        matched: Set[str],
        source_importance: float,
    ) -> float:
        """
        计算 importance（0.0 ~ 1.0）

        规则：
        - base = 0.3
        - origin 命中：+0.25
        - relationship 命中：+0.25
        - project 命中：+0.10
        - continuity 关键词命中（去重）：+0.15
        - source importance >= 0.7：+0.05
        - 最终 min(value, 1.0)

        continuity 关键词命中 set 去重：同一 memory 即使命中多个 continuity 词只算一次
        """
        value = IMPORTANCE_BASE

        if category == "origin":
            value += IMPORTANCE_ORIGIN
        elif category == "relationship":
            value += IMPORTANCE_RELATIONSHIP
        elif category == "project":
            value += IMPORTANCE_PROJECT
        # interaction 命中 ORIGIN/RELATIONSHIP/PROJECT 关键词时
        # 按命中最高类别加分（保持 origin 优先）
        elif category == "interaction" and matched:
            # 简单判断：若 matched 含 origin 关键词则按 origin
            if matched & ORIGIN_KEYWORDS:
                value += IMPORTANCE_ORIGIN
            elif matched & RELATIONSHIP_KEYWORDS:
                value += IMPORTANCE_RELATIONSHIP
            elif matched & PROJECT_KEYWORDS:
                value += IMPORTANCE_PROJECT

        # continuity 加权（去重）
        continuity_hit = matched & CONTINUITY_KEYWORDS
        if continuity_hit:
            value += IMPORTANCE_CONTINUITY

        # source importance 加权
        if source_importance >= 0.7:
            value += IMPORTANCE_SOURCE

        return value

    # ---------- 内部方法：summary / evidence 生成 ----------

    def _make_summary(self, text: str) -> str:
        """生成一句话 summary（≤ 60 字）"""
        # 清理多余空白
        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned:
            return ""
        # 取首句
        for sep in ["。", "！", "？", ".", "!", "?"]:
            if sep in cleaned:
                first = cleaned.split(sep)[0].strip()
                if 10 <= len(first) <= SUMMARY_MAX_LEN:
                    return first + sep
        return cleaned[:SUMMARY_MAX_LEN]

    def _make_evidence(self, text: str) -> str:
        """生成 evidence（≤ 200 字）"""
        cleaned = re.sub(r"\s+", " ", text).strip()
        return cleaned[:EVIDENCE_MAX_LEN]
