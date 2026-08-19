"""
情绪系统 (Emotion System)
Phase 9.0A：EmotionState + EmotionDelta 数据协议
"""

from src.emotion.emotion_state import EmotionState
from src.emotion.emotion_event import EmotionEvent
from src.emotion.emotion_manager import EmotionManager
from src.emotion.emotion_repository import EmotionRepository
from src.emotion.emotion_trace_repository import EmotionTraceRepository
from src.emotion.emotion_dynamics_engine import EmotionDynamicsEngine

__all__ = [
    "EmotionState",
    "EmotionEvent",
    "EmotionManager",
    "EmotionRepository",
    "EmotionTraceRepository",
    "EmotionDynamicsEngine",
]
