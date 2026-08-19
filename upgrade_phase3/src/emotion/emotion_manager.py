"""
情绪管理器 (EmotionManager) — Phase 9.7 v2 最终版
集成情绪轨迹持久化、记忆桥接、分析计数器持久化。
"""
import json
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from src.emotion.emotion_state import EmotionState
from src.emotion.emotion_decay import EmotionDecay
from src.emotion.emotion_context_provider import EmotionContextProvider
from src.emotion.emotion_context import EmotionContext
from src.emotion.emotion_event import EmotionEvent
from src.emotion.emotion_engine import EmotionEngine
from src.emotion.emotion_repository import EmotionRepository
from src.emotion.emotional_trace import EmotionalTrace
from src.emotion.emotion_trace_repository import EmotionTraceRepository
from src.emotion.emotion_memory_bridge import EmotionMemoryBridge


class EmotionManager:
    def __init__(
        self,
        repository: EmotionRepository = None,
        trace_repository: EmotionTraceRepository = None,
        counter_file: str = "data/emotion_analysis_counter.json",
    ):
        self.repository = repository or EmotionRepository()
        self.trace_repository = trace_repository or EmotionTraceRepository()
        self.state = self.repository.load()
        self.decay = EmotionDecay()
        self.engine = EmotionEngine()
        self.provider = EmotionContextProvider()
        self.bridge = EmotionMemoryBridge()

        # 分析计数器持久化
        self._counter_file = Path(counter_file)
        self._analysis_counter: int = self._load_counter()

    # ----------------- 情绪事件 -----------------
    def process_event(self, event: EmotionEvent, memory_id: Optional[str] = None):
        """处理情绪事件，更新内部状态并持久化轨迹"""
        delta = self.engine.process(event)
        self.state = self.state.apply_delta(delta)
        self.repository.save(self.state)

        trace = self.bridge.bind(event, memory_id=memory_id)
        self.trace_repository.append(trace)

    def update(self):
        """应用时间衰减"""
        if self.state.updated_at:
            try:
                last = datetime.fromisoformat(self.state.updated_at)
                now = datetime.now()
                seconds = (now - last).total_seconds()
                if seconds > 0:
                    self.state = self.decay.apply(self.state, seconds)
                    self.repository.save(self.state)
            except (ValueError, TypeError):
                pass

    def get_context(self, influence: float = 0.3) -> EmotionContext:
        """获取当前情绪上下文，influence 控制情绪表达强度"""
        return self.provider.build(self.state, influence=influence)

    def get_recent_traces(self, limit: int = 5) -> List[EmotionalTrace]:
        return self.trace_repository.get_recent(limit)

    # ----------------- 分析计数器 -----------------
    @property
    def analysis_counter(self) -> int:
        return self._analysis_counter

    def increment_analysis_counter(self):
        self._analysis_counter += 1
        self._save_counter()

    def reset_analysis_counter(self):
        self._analysis_counter = 0
        self._save_counter()

    def _load_counter(self) -> int:
        if self._counter_file.exists():
            with open(self._counter_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("count", 0)
        return 0

    def _save_counter(self):
        self._counter_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._counter_file, "w", encoding="utf-8") as f:
            json.dump({"count": self._analysis_counter}, f)