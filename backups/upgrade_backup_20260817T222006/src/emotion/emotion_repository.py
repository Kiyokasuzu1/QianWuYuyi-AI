"""
情绪状态仓库 (EmotionRepository)
负责 EmotionState 的 JSON 持久化。
R2.7.6-YUYI: 原子写保护——进程崩溃不会留下半截 JSON。
"""
import json
import os
from pathlib import Path
from src.emotion.emotion_state import EmotionState


class EmotionRepository:
    def __init__(self, filepath: str = "data/emotion_state.json"):
        self.filepath = Path(filepath)

    def save(self, state: EmotionState):
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        # R2.7.6-YUYI: 原子写——写临时文件 → fsync → rename
        tmp_path = self.filepath.with_suffix(self.filepath.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        tmp_path.replace(self.filepath)

    def load(self) -> EmotionState:
        if not self.filepath.exists():
            return EmotionState()
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return EmotionState.from_dict(data)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            # R2.7.6-YUYI: 损坏文件降级为空状态，不崩溃
            import logging
            logging.getLogger(__name__).warning(
                "emotion_state.json 损坏，降级为空状态"
            )
            return EmotionState()