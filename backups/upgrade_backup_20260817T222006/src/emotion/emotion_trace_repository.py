"""
情绪轨迹仓库 (EmotionTraceRepository)
负责 EmotionalTrace 的 JSON 持久化，支持增量添加和最近查询。
R2.7.6-YUYI: 原子写保护 + 损坏降级。
"""
import json
import os
import logging
from pathlib import Path
from typing import List
from src.emotion.emotional_trace import EmotionalTrace

_logger = logging.getLogger(__name__)


class EmotionTraceRepository:
    def __init__(self, filepath: str = "data/emotional_traces.json"):
        self.filepath = Path(filepath)

    def load_all(self) -> List[EmotionalTrace]:
        if not self.filepath.exists():
            return []
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [EmotionalTrace.from_dict(d) for d in data]
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            _logger.warning("emotional_traces.json 损坏，降级为空列表")
            return []

    def save_all(self, traces: List[EmotionalTrace]):
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        # R2.7.6-YUYI: 原子写
        data = [t.to_dict() for t in traces]
        tmp_path = self.filepath.with_suffix(self.filepath.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        tmp_path.replace(self.filepath)

    def append(self, trace: EmotionalTrace):
        """添加一条新轨迹并保存"""
        traces = self.load_all()
        traces.append(trace)
        self.save_all(traces)

    def get_recent(self, limit: int = 5) -> List[EmotionalTrace]:
        """获取最近若干条轨迹"""
        traces = self.load_all()
        return traces[-limit:]