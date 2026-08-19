"""记忆系统 (Memory System)。

使用惰性导出，避免导入轻量子模块时强制要求向量数据库依赖。
"""

from importlib import import_module

__all__ = [
    "MemoryStore",
    "VectorMemory",
    "EventMemory",
    "MemoryContext",
    "MemoryFormatter",
    "MemorySystem",
    "MemoryService",
    "MemoryRelevanceEvaluator",
    "MemoryRelevanceEvaluatorConfig",
]

_EXPORT_MAP = {
    "MemoryStore": ("src.memory.memory_store", "MemoryStore"),
    "VectorMemory": ("src.memory.vector", "VectorMemory"),
    "EventMemory": ("src.memory.event_memory", "EventMemory"),
    "MemoryContext": ("src.memory.memory_context", "MemoryContext"),
    "MemoryFormatter": ("src.memory.memory_formatter", "MemoryFormatter"),
    "MemorySystem": ("src.memory.memory_system", "MemorySystem"),
    "MemoryService": ("src.memory.memory_service", "MemoryService"),
    "MemoryRelevanceEvaluator": ("src.memory.memory_relevance_evaluator", "MemoryRelevanceEvaluator"),
    "MemoryRelevanceEvaluatorConfig": ("src.memory.memory_relevance_evaluator", "MemoryRelevanceEvaluatorConfig"),
}


def __getattr__(name):
    if name not in _EXPORT_MAP:
        raise AttributeError(f"module 'src.memory' has no attribute {name!r}")
    module_name, attr_name = _EXPORT_MAP[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
