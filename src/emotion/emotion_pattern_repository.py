"""
情绪模式仓库 (EmotionPatternRepository)
负责 EmotionPattern 的 JSON 持久化，支持增量添加。
"""
import json
from pathlib import Path
from typing import List
from src.emotion.emotion_pattern import EmotionPattern
from src.memory.atomic_write import (
    atomic_write_json,
    backup_corrupt_file,
    get_path_lock,
)


class EmotionPatternRepository:
    def __init__(self, filepath: str = "data/emotion_patterns.json"):
        self.filepath = Path(filepath)

    def load_all(self) -> List[EmotionPattern]:
        if not self.filepath.exists():
            return []
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [EmotionPattern.from_dict(d) for d in data]
        except Exception:
            # V1.1: 损坏时备份现场,返回空列表（不覆盖旧文件）
            backup_corrupt_file(self.filepath)
            return []

    def save_all(self, patterns: List[EmotionPattern]):
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        data = [p.to_dict() for p in patterns]
        # V1.1: 锁 + 原子写（替换裸 open("w") 截断写）
        with get_path_lock(str(self.filepath)):
            atomic_write_json(str(self.filepath), data)

    def append(self, pattern: EmotionPattern):
        """添加一条新模式并保存（V1.1: 读-改-写全程持锁,防并发丢失更新）"""
        with get_path_lock(str(self.filepath)):
            patterns = self.load_all()
            patterns.append(pattern)
            self.save_all(patterns)