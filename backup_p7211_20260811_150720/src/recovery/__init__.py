"""
Phase A.1: Historical Experience Recovery 包

职责：
从 data/memory.json 中恢复羽依的历史经历上下文，建立 SelfModel.experience_context。

架构原则：
- HistoricalExperience ≠ GrowthRecord
- experience_context ≠ growth_narratives
- 本包不引用任何 src/growth/* 或 personality_growth_record 模块
- 本包不修改 data/memory.json / data/growth_state.json / data/relationship_state.json

公开 API（按 Step 进展逐步加入）：
- Step 1: ExperienceExtractor
- Step 2: RecoveryMarker
- Step 3: ExperienceCache
- Step 4: ExperienceLoader
"""

# 延迟导入：避免 Step 1 时因依赖未实现模块而 ImportError
def __getattr__(name):
    if name == "ExperienceExtractor":
        from src.recovery.experience_extractor import ExperienceExtractor
        return ExperienceExtractor
    if name == "RecoveryMarker":
        from src.recovery.recovery_marker import RecoveryMarker
        return RecoveryMarker
    if name == "ExperienceCache":
        from src.recovery.experience_cache import ExperienceCache
        return ExperienceCache
    if name == "ExperienceLoader":
        from src.recovery.experience_loader import ExperienceLoader
        return ExperienceLoader
    raise AttributeError(f"module 'src.recovery' has no attribute {name!r}")


__all__ = [
    "ExperienceExtractor",
    "RecoveryMarker",
    "ExperienceCache",
    "ExperienceLoader",
]
