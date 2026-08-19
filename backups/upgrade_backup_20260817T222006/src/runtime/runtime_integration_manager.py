"""
Phase 3.5.20: Runtime Integration Manager

职责：
- 统一注册 Runtime 子系统
- 输出 RuntimeHealthReport
- 为生命周期管理提供更高层的模块视图
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from src.contracts.runtime_integration_schema import (
    RuntimeHealthReport,
    RuntimeModuleRegistration,
    RuntimeSubsystemStatus,
)


class RuntimeIntegrationManager:
    def __init__(self):
        self._modules: Dict[str, RuntimeModuleRegistration] = {}
        self._health_fns: Dict[str, Callable[[], Dict[str, Any]]] = {}

    def register_module(
        self,
        *,
        name: str,
        category: str,
        enabled: bool,
        dependencies: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        health_fn: Optional[Callable[[], Dict[str, Any]]] = None,
    ) -> None:
        self._modules[name] = RuntimeModuleRegistration(
            name=name,
            category=category,
            enabled=enabled,
            dependencies=list(dependencies or []),
            metadata=dict(metadata or {}),
        )
        if health_fn:
            self._health_fns[name] = health_fn

    def get_registered_modules(self) -> List[Dict[str, Any]]:
        return [m.to_dict() for m in self._modules.values()]

    def build_health_report(self) -> RuntimeHealthReport:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for name, reg in self._modules.items():
            item = {
                "name": reg.name,
                "enabled": reg.enabled,
                "dependencies": list(reg.dependencies),
                "metadata": dict(reg.metadata),
            }
            if name in self._health_fns:
                try:
                    health = self._health_fns[name]() or {}
                except Exception as e:
                    health = {"healthy": False, "error": str(e)}
            else:
                health = {"healthy": reg.enabled}
            item["health"] = health
            grouped.setdefault(reg.category, []).append(item)

        report = RuntimeHealthReport(
            registered_modules=self.get_registered_modules(),
        )
        report.memory_status = self._aggregate(grouped.get("memory", []))
        report.reflection_status = self._aggregate(grouped.get("reflection", []))
        report.growth_status = self._aggregate(grouped.get("growth", []))
        report.identity_status = self._aggregate(grouped.get("identity", []))
        report.personality_status = self._aggregate(grouped.get("personality", []))
        report.relationship_status = self._aggregate(grouped.get("relationship", []))
        report.emotion_status = self._aggregate(grouped.get("emotion", []))
        report.autonomous_status = self._aggregate(grouped.get("autonomous", []))

        subsystem_states = [
            report.memory_status,
            report.reflection_status,
            report.growth_status,
            report.identity_status,
            report.personality_status,
            report.relationship_status,
            report.emotion_status,
            report.autonomous_status,
        ]
        enabled_states = [s for s in subsystem_states if s.enabled]
        if not enabled_states:
            report.overall_status = "disabled"
        elif all(s.healthy for s in enabled_states):
            report.overall_status = "healthy"
        elif any(s.healthy for s in enabled_states):
            report.overall_status = "degraded"
        else:
            report.overall_status = "unhealthy"
        return report

    def _aggregate(self, items: List[Dict[str, Any]]) -> RuntimeSubsystemStatus:
        if not items:
            return RuntimeSubsystemStatus(enabled=False, healthy=False, state="disabled", details={"modules": []})
        enabled = any(bool(i.get("enabled")) for i in items)
        module_health = []
        for i in items:
            health = i.get("health", {})
            module_health.append({
                "name": i.get("name", ""),
                "enabled": i.get("enabled", False),
                "healthy": bool(health.get("healthy", False)) if i.get("enabled", False) else False,
                "details": health,
            })
        enabled_modules = [m for m in module_health if m["enabled"]]
        if not enabled_modules:
            return RuntimeSubsystemStatus(enabled=False, healthy=False, state="disabled", details={"modules": module_health})
        healthy = all(m["healthy"] for m in enabled_modules)
        state = "healthy" if healthy else ("degraded" if any(m["healthy"] for m in enabled_modules) else "unhealthy")
        return RuntimeSubsystemStatus(
            enabled=True,
            healthy=healthy,
            state=state,
            details={"modules": module_health},
        )
