"""
Runtime Adapters —— 经验与成长适配器。

使用惰性导出，避免导入单个适配器时拉起全部依赖。

Phase 3.7.1 更新：
- 引入 AdapterBase 抽象基类
- 新增 EmotionAdapter / PersonalityAdapter / MemoryAdapterSpec / GrowthAdapterSpec
  作为 Runtime 接入各业务模块的统一接口
- 保留 Phase 3.5.x 既有具体实现（ExperienceMemoryAdapter / ReflectionGrowthAdapter）
  通过 src.runtime.adapters.memory_adapter / .growth_adapter 子模块直接访问
"""

from importlib import import_module

__all__ = [
    # 抽象接口（Phase 3.7.1+）
    "AdapterBase",
    "MemoryAdapterSpec",
    "EmotionAdapter",
    "GrowthAdapterSpec",
    "PersonalityAdapter",
    # 既有具体实现（Phase 3.5.x 遗留 / 通过子模块访问）
    "MemoryAdapter",
    "GrowthAdapter",
]

_EXPORT_MAP = {
    "AdapterBase": ("src.runtime.adapters.base", "AdapterBase"),
    "MemoryAdapterSpec": ("src.runtime.adapters.memory_adapter", "MemoryAdapterSpec"),
    "EmotionAdapter": ("src.runtime.adapters.emotion_adapter", "EmotionAdapter"),
    "GrowthAdapterSpec": ("src.runtime.adapters.growth_adapter", "GrowthAdapterSpec"),
    "PersonalityAdapter": ("src.runtime.adapters.personality_adapter", "PersonalityAdapter"),
    "MemoryAdapter": ("src.runtime.adapters.memory_adapter", "MemoryAdapter"),
    "GrowthAdapter": ("src.runtime.adapters.growth_adapter", "GrowthAdapter"),
}


def __getattr__(name):
    if name not in _EXPORT_MAP:
        raise AttributeError(f"module 'src.runtime.adapters' has no attribute {name!r}")
    module_name, attr_name = _EXPORT_MAP[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
