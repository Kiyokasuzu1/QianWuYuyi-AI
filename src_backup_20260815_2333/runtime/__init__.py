"""
Runtime 模块 —— 羽依的生命循环系统

使用惰性导出，避免在导入 `src.runtime.xxx` 子模块时触发不必要的重依赖。
"""

from importlib import import_module

__all__ = [
    "RuntimeCore",
    "SelfState",
    "WorldState",
    "DecisionEngine",
    "Decision",
    "ActionDispatcher",
    "Action",
    "Scheduler",
    "RuntimeEventBus",
    "RuntimeBridge",
    "get_runtime_bridge",
    "InitiativeBridge",
    "ReflectionGrowthBridge",
    "ReflectionGrowthBridgeConfig",
    "RuntimeLifecycleOrchestrator",
    "RuntimeIntegrationManager",
    "AutonomousDecisionLayer",
    "AutonomousScheduler",
    "CognitiveLoopVerifier",
    "DomainRuntimeEventBus",
    # R2.7.0-A RuntimeTrace Contract
    "FROZEN_RUNTIME_TRACE_KEYS",
    "STEP_SNAPSHOT_FIELDS",
    "SUMMARY_FIELDS",
    "EXCEPTION_FIELDS",
    "RUNTIME_PHASE_WHITELIST",
    "RUNTIME_STEP_WHITELIST",
    "OVERALL_STATUS_WHITELIST",
    "RUNTIME_TRACE_FORBIDDEN_IMPORTS",
    "RUNTIME_TRACE_FORBIDDEN_CALLS",
    "validate_runtime_trace_shape",
    "create_empty_runtime_trace",
]

_EXPORT_MAP = {
    "RuntimeCore": ("src.runtime.runtime_core", "RuntimeCore"),
    "SelfState": ("src.runtime.self_state", "SelfState"),
    "WorldState": ("src.runtime.world_state", "WorldState"),
    "DecisionEngine": ("src.runtime.decision_engine", "DecisionEngine"),
    "Decision": ("src.runtime.decision_engine", "Decision"),
    "ActionDispatcher": ("src.runtime.action_dispatcher", "ActionDispatcher"),
    "Action": ("src.runtime.action_dispatcher", "Action"),
    "Scheduler": ("src.runtime.scheduler", "Scheduler"),
    "RuntimeEventBus": ("src.runtime.event_bus", "RuntimeEventBus"),
    "RuntimeBridge": ("src.runtime.runtime_bridge", "RuntimeBridge"),
    "get_runtime_bridge": ("src.runtime.runtime_bridge", "get_runtime_bridge"),
    "InitiativeBridge": ("src.runtime.initiative_bridge", "InitiativeBridge"),
    "ReflectionGrowthBridge": ("src.runtime.reflection_growth_bridge", "ReflectionGrowthBridge"),
    "ReflectionGrowthBridgeConfig": ("src.runtime.reflection_growth_bridge", "ReflectionGrowthBridgeConfig"),
    "RuntimeLifecycleOrchestrator": ("src.runtime.lifecycle_orchestrator", "RuntimeLifecycleOrchestrator"),
    "RuntimeIntegrationManager": ("src.runtime.runtime_integration_manager", "RuntimeIntegrationManager"),
    "AutonomousDecisionLayer": ("src.runtime.autonomous_decision_layer", "AutonomousDecisionLayer"),
    "AutonomousScheduler": ("src.runtime.autonomous_scheduler", "AutonomousScheduler"),
    "CognitiveLoopVerifier": ("src.runtime.cognitive_loop_verifier", "CognitiveLoopVerifier"),
    "DomainRuntimeEventBus": ("src.runtime.runtime_event_bus", "RuntimeEventBus"),
    # R2.7.0-A RuntimeTrace Contract
    "FROZEN_RUNTIME_TRACE_KEYS": ("src.runtime.runtime_trace_schema", "FROZEN_RUNTIME_TRACE_KEYS"),
    "STEP_SNAPSHOT_FIELDS": ("src.runtime.runtime_trace_schema", "STEP_SNAPSHOT_FIELDS"),
    "SUMMARY_FIELDS": ("src.runtime.runtime_trace_schema", "SUMMARY_FIELDS"),
    "EXCEPTION_FIELDS": ("src.runtime.runtime_trace_schema", "EXCEPTION_FIELDS"),
    "RUNTIME_PHASE_WHITELIST": ("src.runtime.runtime_trace_schema", "RUNTIME_PHASE_WHITELIST"),
    "RUNTIME_STEP_WHITELIST": ("src.runtime.runtime_trace_schema", "RUNTIME_STEP_WHITELIST"),
    "OVERALL_STATUS_WHITELIST": ("src.runtime.runtime_trace_schema", "OVERALL_STATUS_WHITELIST"),
    "RUNTIME_TRACE_FORBIDDEN_IMPORTS": ("src.runtime.runtime_trace_schema", "FORBIDDEN_IMPORTS"),
    "RUNTIME_TRACE_FORBIDDEN_CALLS": ("src.runtime.runtime_trace_schema", "FORBIDDEN_CALLS"),
    "validate_runtime_trace_shape": ("src.runtime.runtime_trace_schema", "validate_runtime_trace_shape"),
    "create_empty_runtime_trace": ("src.runtime.runtime_trace_schema", "create_empty_runtime_trace"),
}


def __getattr__(name):
    if name not in _EXPORT_MAP:
        raise AttributeError(f"module 'src.runtime' has no attribute {name!r}")
    module_name, attr_name = _EXPORT_MAP[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
