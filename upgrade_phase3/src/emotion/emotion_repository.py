"""
情绪状态仓库 (EmotionRepository)
负责 EmotionState 的 JSON 持久化。
"""
import json
from pathlib import Path
from src.emotion.emotion_state import EmotionState


class EmotionRepository:
    def __init__(self, filepath: str = "data/emotion_state.json"):
        self.filepath = Path(filepath)

    def save(self, state: EmotionState):
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)

    def load(self) -> EmotionState:
        if not self.filepath.exists():
            return EmotionState()
        with open(self.filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return EmotionState.from_dict(data)