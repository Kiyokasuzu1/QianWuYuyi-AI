"""
相处模式学习器。

负责：
- 从对话历史中学习用户的表达模式
- 存储模式库（JSON 持久化）
- 匹配当前输入与已知模式
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class ExpressionPattern:
    """表达模式数据结构"""
    pattern_id: str = field(default_factory=lambda: f"pat_{uuid.uuid4().hex[:8]}")
    user_id: str = ""
    surface_expression: str = ""        # 表面表达（如 "累了"）
    implied_meaning: str = ""           # 言外之意（如 "我还想再撑一下"）
    emotional_tone: str = ""            # 情感基调（如 "疲惫但坚持"）
    underlying_need: str = ""           # 潜在需求（如 "需要鼓励"）
    trigger_keywords: List[str] = field(default_factory=list)  # 触发关键词
    confidence: float = 0.0             # 置信度 0~1
    learned_at: str = field(default_factory=lambda: datetime.now().isoformat())
    usage_count: int = 0                # 使用次数

    def to_dict(self) -> Dict:
        return {
            "pattern_id": self.pattern_id,
            "user_id": self.user_id,
            "surface_expression": self.surface_expression,
            "implied_meaning": self.implied_meaning,
            "emotional_tone": self.emotional_tone,
            "underlying_need": self.underlying_need,
            "trigger_keywords": self.trigger_keywords,
            "confidence": self.confidence,
            "learned_at": self.learned_at,
            "usage_count": self.usage_count,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ExpressionPattern":
        return cls(
            pattern_id=data.get("pattern_id", ""),
            user_id=data.get("user_id", ""),
            surface_expression=data.get("surface_expression", ""),
            implied_meaning=data.get("implied_meaning", ""),
            emotional_tone=data.get("emotional_tone", ""),
            underlying_need=data.get("underlying_need", ""),
            trigger_keywords=data.get("trigger_keywords", []),
            confidence=data.get("confidence", 0.0),
            learned_at=data.get("learned_at", ""),
            usage_count=data.get("usage_count", 0),
        )


class PatternLearner:
    """
    相处模式学习器。

    从对话中学习用户的表达模式，并持久化存储。
    """

    def __init__(self, data_dir: str = "data/understanding"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.pattern_file = self.data_dir / "patterns.json"

        self._patterns: List[ExpressionPattern] = []
        self._load_patterns()

    def _load_patterns(self):
        """从文件加载模式库"""
        if not self.pattern_file.exists():
            self._save_patterns()
            return

        try:
            with open(self.pattern_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._patterns = [
                ExpressionPattern.from_dict(p)
                for p in data.get("patterns", [])
            ]
        except Exception:
            self._patterns = []

    def _save_patterns(self):
        """保存模式库到文件"""
        data = {
            "version": "1.0",
            "patterns": [p.to_dict() for p in self._patterns],
        }
        try:
            with open(self.pattern_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[PatternLearner] 保存失败: {e}")

    def learn(
        self,
        surface: str,
        implied: str,
        emotion: str = "",
        need: str = "",
        user_id: str = "",
        keywords: List[str] = None,
        confidence: float = 0.5,
    ) -> ExpressionPattern:
        """
        学习新的表达模式。

        如果已存在相似模式，更新置信度和使用次数。

        Args:
            surface: 表面表达
            implied: 言外之意
            emotion: 情感基调
            need: 潜在需求
            user_id: 用户 ID
            keywords: 触发关键词
            confidence: 置信度

        Returns:
            创建或更新的模式对象
        """
        # 检查是否已存在相似模式
        existing = self._find_similar(surface, user_id)
        if existing is not None:
            # 更新已有模式
            existing.usage_count += 1
            existing.confidence = min(1.0, existing.confidence + 0.05)
            if implied and not existing.implied_meaning:
                existing.implied_meaning = implied
            if emotion and not existing.emotional_tone:
                existing.emotional_tone = emotion
            if need and not existing.underlying_need:
                existing.underlying_need = need
            self._save_patterns()
            return existing

        # 创建新模式
        pattern = ExpressionPattern(
            user_id=user_id,
            surface_expression=surface,
            implied_meaning=implied,
            emotional_tone=emotion,
            underlying_need=need,
            trigger_keywords=keywords or self._extract_keywords(surface),
            confidence=confidence,
        )
        self._patterns.append(pattern)
        self._save_patterns()
        return pattern

    def match(self, text: str, user_id: str = "") -> Optional[ExpressionPattern]:
        """
        匹配当前输入与已知模式。

        Args:
            text: 用户输入文本
            user_id: 用户 ID

        Returns:
            匹配的模式，无匹配返回 None
        """
        if not self._patterns:
            return None

        # 按用户过滤
        patterns = self._patterns
        if user_id:
            user_patterns = [p for p in self._patterns if p.user_id == user_id]
            if user_patterns:
                patterns = user_patterns

        # 关键词匹配
        best_match = None
        best_score = 0.0

        for pattern in patterns:
            score = self._calculate_match_score(text, pattern)
            if score > best_score:
                best_score = score
                best_match = pattern

        # 阈值：匹配分数 > 0.3
        if best_match is not None and best_score > 0.3:
            best_match.usage_count += 1
            self._save_patterns()
            return best_match

        return None

    def get_patterns(self, user_id: str = "") -> List[ExpressionPattern]:
        """获取模式列表"""
        if user_id:
            return [p for p in self._patterns if p.user_id == user_id]
        return self._patterns

    def get_pattern_count(self) -> int:
        """获取模式数量"""
        return len(self._patterns)

    def _find_similar(
        self,
        surface: str,
        user_id: str
    ) -> Optional[ExpressionPattern]:
        """查找相似模式"""
        for pattern in self._patterns:
            if pattern.user_id != user_id:
                continue
            if pattern.surface_expression == surface:
                return pattern
            # 简单相似度：包含关系
            if surface in pattern.surface_expression or pattern.surface_expression in surface:
                return pattern
        return None

    def _calculate_match_score(self, text: str, pattern: ExpressionPattern) -> float:
        """计算匹配分数"""
        score = 0.0

        # 1. 表面表达完全匹配
        if pattern.surface_expression in text:
            score += 0.5

        # 2. 关键词匹配
        for keyword in pattern.trigger_keywords:
            if keyword in text:
                score += 0.15

        # 3. 情感基调匹配
        if pattern.emotional_tone:
            tone_keywords = pattern.emotional_tone.split()
            for kw in tone_keywords:
                if kw in text:
                    score += 0.1

        return min(score, 1.0)

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        """从文本中提取关键词（简单分词）"""
        # 简单实现：按空格和标点分词，取长度 > 1 的词
        import re
        words = re.split(r'[，。！？\s,.!?;；]+', text)
        return [w for w in words if len(w) > 1][:5]