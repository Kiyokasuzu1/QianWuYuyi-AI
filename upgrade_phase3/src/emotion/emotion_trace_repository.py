"""
情绪轨迹仓库 (EmotionTraceRepository)
负责 EmotionalTrace 的 JSON 持久化，支持增量添加和最近查询。
"""
import json
from pathlib import Path
from typing import List
from src.emotion.emotional_trace import EmotionalTrace


class EmotionTraceRepository:
    def __init__(self, filepath: str = "data/emotional_traces.json"):
        self.filepath = Path(filepath)

    def load_all(self) -> List[EmotionalTrace]:
        if not self.filepath.exists():
            return []
        with open(self.filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [EmotionalTrace.from_dict(d) for d in data]

    def save_all(self, traces: List[EmotionalTrace]):
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        data = [t.to_dict() for t in traces]
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def append(self, trace: EmotionalTrace):
        """添加一条新轨迹并保存"""
        traces = self.load_all()
        traces.append(trace)
        self.save_all(traces)

    def get_recent(self, limit: int = 5) -> List[EmotionalTrace]:
        """获取最近若干条轨迹"""
        traces = self.load_all()
        return traces[-limit:]