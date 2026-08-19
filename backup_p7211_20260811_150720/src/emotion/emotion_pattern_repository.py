"""
情绪模式仓库 (EmotionPatternRepository)
负责 EmotionPattern 的 JSON 持久化，支持增量添加。
"""
import json
from pathlib import Path
from typing import List
from src.emotion.emotion_pattern import EmotionPattern


class EmotionPatternRepository:
    def __init__(self, filepath: str = "data/emotion_patterns.json"):
        self.filepath = Path(filepath)

    def load_all(self) -> List[EmotionPattern]:
        if not self.filepath.exists():
            return []
        with open(self.filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [EmotionPattern.from_dict(d) for d in data]

    def save_all(self, patterns: List[EmotionPattern]):
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        data = [p.to_dict() for p in patterns]
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def append(self, pattern: EmotionPattern):
        """添加一条新模式并保存"""
        patterns = self.load_all()
        patterns.append(pattern)
        self.save_all(patterns)