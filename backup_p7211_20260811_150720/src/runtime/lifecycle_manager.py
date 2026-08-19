"""
Phase 3.5.13: Runtime Lifecycle Integration

职责：
- 管理 RuntimeCore 的完整生命周期：启动 → 恢复 → 运行 → 保存 → 关闭
- 编排所有子系统的启动/停止顺序（按依赖关系）
- 记录生命周期事件审计记录
- 提供健康状态聚合与故障恢复支持

连接的子系统：
- Memory（MemoryAdapter）
- Persona（PersonalityAdapter）
- Identity（IdentityAnchorManager / IdentityContinuityChecker）
- SelfModel（SelfModelManager / SelfModelUpdater）
- Emotion（SelfState 中的情绪字段）
- Growth（GrowthAdapter / ApprovalManager）
- Reflection（ReflectionEngine / ReflectionScheduler / ReflectionEvaluator）

设计原则：
- 不修改 ModuleBase 接口
- 不破坏现有 RuntimeCore 逻辑
- LifecycleManager 作为 RuntimeCore 的编排层，不替换它
- 所有生命周期行为可审计、可追溯
- 默认关闭，需显式启用

约束：
- 不自动接受 GrowthProposal
- 不直接修改 Personality / TraitState
- 不修改 Persona 文档
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.contracts.lifecycle_schema import (
    PHASE_INIT, PHASE_START, PHASE_RESUME, PHASE_RUN,
    PHASE_SAVE, PHASE_STOP, PHASE_ERROR,
    ALL_PHASES,
    ModuleStatus,
    LifecycleRecord,
    LifecycleSnapshot,
    now_iso,
)

logger = logging.getLogger(__name__)


# ============================================================
# 模块定义：可被生命周期管理的模块
# ============================================================

class ManagedModule:
    """
    被生命周期管理的模块包装。

    将一个模块（如 MemoryAdapter）包装为统一接口，
    提供 start/stop/save/health_check 的标准调用。
    """

    def __init__(
        self,
        name: str,
        obj: Any,
        *,
        start_fn: Optional[Callable[[], bool]] = None,
        stop_fn: Optional[Callable[[], bool]] = None,
        save_fn: Optional[Callable[[], bool]] = None,
        load_fn: Optional[Callable[[], bool]] = None,
        health_fn: Optional[Callable[[], Dict[str, Any]]] = None,
        dependencies: Optional[List[str]] = None,
        required: bool = False,
    ):
        self.name = name
        self.obj = obj
        self._start_fn = start_fn
        self._stop_fn = stop_fn
        self._save_fn = save_fn
        self._load_fn = load_fn
        self._health_fn = health_fn
        self.dependencies = dependencies or []
        self.required = required

        self.status = ModuleStatus(
            module_name=name,
            enabled=obj is not None,
            state="UNINITIALIZED",
        )


# ============================================================
# LifecycleManager: 生命周期管理器
# ============================================================

class LifecycleManager:
    """
    Runtime 生命周期管理器。

    用法：
        manager = LifecycleManager()
        manager.register_module("memory", memory_adapter, ...)
        manager.start_all()    # 按依赖顺序启动
        manager.save_all()     # 保存所有模块状态
        manager.stop_all()     # 按依赖逆序停止
        snap = manager.get_snapshot()  # 获取快照
    """

    def __init__(self, history_path: Optional[str] = None):
        """
        Args:
            history_path: 生命周期审计记录持久化路径（可选）
        """
        self._modules: Dict[str, ManagedModule] = {}
        self._module_order: List[str] = []  # 注册顺序（用于无依赖时的默认顺序）
        self._history: List[LifecycleRecord] = []
        self._history_path = Path(history_path) if history_path else None
        if self._history_path:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            if not self._history_path.exists():
                self._save_history_to_file([])

        self._current_phase: str = PHASE_INIT
        self._start_time: float = 0.0
        self._last_save_time: float = 0.0

        # 统计
        self._total_starts: int = 0
        self._total_stops: int = 0
        self._total_saves: int = 0
        self._total_errors: int = 0

        # 从文件加载历史
        self._history = self._load_history_from_file()

    # ============================================================
    # 模块注册
    # ============================================================

    def register_module(
        self,
        name: str,
        obj: Any,
        *,
        start_fn: Optional[Callable[[], bool]] = None,
        stop_fn: Optional[Callable[[], bool]] = None,
        save_fn: Optional[Callable[[], bool]] = None,
        load_fn: Optional[Callable[[], bool]] = None,
        health_fn: Optional[Callable[[], Dict[str, Any]]] = None,
        dependencies: Optional[List[str]] = None,
        required: bool = False,
    ) -> None:
        """
        注册一个模块到生命周期管理。

        Args:
            name: 模块名称（唯一标识）
            obj: 模块对象（可为 None，表示可选模块未启用）
            start_fn: 启动函数（默认调用 obj.start()）
            stop_fn: 停止函数（默认调用 obj.stop()）
            save_fn: 保存函数（默认无操作）
            load_fn: 加载函数（默认无操作）
            health_fn: 健康检查函数（默认返回 {"healthy": True}）
            dependencies: 依赖的模块名列表（启动顺序）
            required: 是否必需模块（启动失败将中止整个生命周期）
        """
        module = ManagedModule(
            name=name,
            obj=obj,
            start_fn=start_fn,
            stop_fn=stop_fn,
            save_fn=save_fn,
            load_fn=load_fn,
            health_fn=health_fn,
            dependencies=dependencies or [],
            required=required,
        )
        self._modules[name] = module
        self._module_order.append(name)
        logger.debug(f"注册生命周期模块: {name} (required={required})")

    def unregister_module(self, name: str) -> bool:
        """注销模块"""
        if name in self._modules:
            del self._modules[name]
            if name in self._module_order:
                self._module_order.remove(name)
            return True
        return False

    # ============================================================
    # 生命周期：启动
    # ============================================================

    def start_all(self) -> LifecycleRecord:
        """
        按依赖顺序启动所有模块。

        流程：
        1. 拓扑排序模块（按 dependencies）
        2. 依次启动每个模块
        3. 必需模块启动失败 → 中止并记录
        4. 可选模块启动失败 → 标记 DEGRADED 但继续
        """
        start_ts = time.time()
        self._current_phase = PHASE_START
        before_state = self._current_phase

        ordered = self._topological_sort()
        started_count = 0
        failed_count = 0
        errors: List[str] = []

        for name in ordered:
            module = self._modules.get(name)
            if module is None or module.obj is None:
                continue

            ok = self._start_module(module)
            if ok:
                started_count += 1
            else:
                failed_count += 1
                errors.append(f"{name}: 启动失败")
                if module.required:
                    # 必需模块失败 → 中止
                    record = LifecycleRecord(
                        phase=PHASE_START,
                        action="start_all",
                        success=False,
                        error=f"必需模块 {name} 启动失败，中止生命周期启动",
                        before_state=before_state,
                        after_state=PHASE_ERROR,
                        details={
                            "started": started_count,
                            "failed": failed_count,
                            "errors": errors,
                        },
                    )
                    self._add_record(record)
                    self._current_phase = PHASE_ERROR
                    return record

        self._start_time = time.time()
        self._current_phase = PHASE_RUN
        self._total_starts += 1

        record = LifecycleRecord(
            phase=PHASE_START,
            action="start_all",
            success=True,
            before_state=before_state,
            after_state=PHASE_RUN,
            duration_ms=(time.time() - start_ts) * 1000,
            details={
                "total": len(ordered),
                "started": started_count,
                "failed": failed_count,
                "errors": errors,
            },
        )
        self._add_record(record)
        logger.info(
            f"生命周期启动完成: {started_count} 成功, {failed_count} 失败"
        )
        return record

    def _start_module(self, module: ManagedModule) -> bool:
        """启动单个模块"""
        if module.obj is None:
            return True  # 可选模块未启用，视为成功

        start_ts = time.time()
        before_state = module.status.state

        try:
            if module._start_fn:
                ok = module._start_fn()
            elif hasattr(module.obj, "start"):
                ok = module.obj.start()
            else:
                ok = True  # 无 start 方法，视为成功

            if ok:
                module.status.state = "RUNNING"
                module.status.started_at = now_iso()
                module.status.phase = PHASE_RUN
            else:
                module.status.state = "ERROR"
                module.status.error_count += 1

            self._record_module_event(
                module, PHASE_START, "start_module",
                success=ok, before_state=before_state,
                after_state=module.status.state,
                duration_ms=(time.time() - start_ts) * 1000,
            )
            return ok

        except Exception as e:
            module.status.state = "ERROR"
            module.status.error_count += 1
            module.status.last_error = str(e)
            self._record_module_event(
                module, PHASE_START, "start_module",
                success=False, error=str(e),
                before_state=before_state,
                after_state="ERROR",
                duration_ms=(time.time() - start_ts) * 1000,
            )
            logger.error(f"模块 {module.name} 启动异常: {e}")
            return False

    # ============================================================
    # 生命周期：恢复
    # ============================================================

    def resume_all(self) -> LifecycleRecord:
        """
        从持久化状态恢复所有模块。

        流程：
        1. 按依赖顺序加载各模块状态
        2. 加载失败的模块标记为 DEGRADED
        3. 启动所有模块
        """
        start_ts = time.time()
        self._current_phase = PHASE_RESUME
        before_state = self._current_phase

        ordered = self._topological_sort()
        loaded_count = 0
        failed_count = 0

        for name in ordered:
            module = self._modules.get(name)
            if module is None or module.obj is None:
                continue

            ok = self._load_module(module)
            if ok:
                loaded_count += 1
            else:
                failed_count += 1
                module.status.state = "DEGRADED"

        # 恢复后启动
        start_record = self.start_all()
        if not start_record.success:
            return start_record

        self._current_phase = PHASE_RUN
        record = LifecycleRecord(
            phase=PHASE_RESUME,
            action="resume_all",
            success=True,
            before_state=before_state,
            after_state=PHASE_RUN,
            duration_ms=(time.time() - start_ts) * 1000,
            details={
                "loaded": loaded_count,
                "failed": failed_count,
            },
        )
        self._add_record(record)
        logger.info(f"生命周期恢复完成: {loaded_count} 加载, {failed_count} 失败")
        return record

    def _load_module(self, module: ManagedModule) -> bool:
        """加载单个模块的持久化状态"""
        if module.obj is None:
            return True

        try:
            if module._load_fn:
                ok = module._load_fn()
            elif hasattr(module.obj, "load_state"):
                ok = module.obj.load_state()
            else:
                ok = True  # 无 load 方法，视为成功

            self._record_module_event(
                module, PHASE_RESUME, "load_state",
                success=ok,
            )
            return ok

        except Exception as e:
            module.status.last_error = str(e)
            self._record_module_event(
                module, PHASE_RESUME, "load_state",
                success=False, error=str(e),
            )
            logger.warning(f"模块 {module.name} 状态加载失败: {e}")
            return False

    # ============================================================
    # 生命周期：保存
    # ============================================================

    def save_all(self) -> LifecycleRecord:
        """
        保存所有模块的状态。

        流程：
        1. 按依赖逆序保存（先保存依赖方，再保存被依赖方）
        2. 保存失败的模块记录但不中止
        3. 更新最后保存时间
        """
        start_ts = time.time()
        before_phase = self._current_phase
        self._current_phase = PHASE_SAVE

        ordered = self._topological_sort()
        ordered.reverse()  # 逆序保存

        saved_count = 0
        failed_count = 0
        errors: List[str] = []

        for name in ordered:
            module = self._modules.get(name)
            if module is None or module.obj is None:
                continue

            ok = self._save_module(module)
            if ok:
                saved_count += 1
            else:
                failed_count += 1
                errors.append(name)

        self._last_save_time = time.time()
        self._current_phase = PHASE_RUN if before_phase == PHASE_RUN else before_phase
        self._total_saves += 1

        record = LifecycleRecord(
            phase=PHASE_SAVE,
            action="save_all",
            success=(failed_count == 0),
            before_state=before_phase,
            after_state=self._current_phase,
            duration_ms=(time.time() - start_ts) * 1000,
            details={
                "saved": saved_count,
                "failed": failed_count,
                "errors": errors,
            },
        )
        self._add_record(record)
        logger.info(f"生命周期保存完成: {saved_count} 成功, {failed_count} 失败")
        return record

    def _save_module(self, module: ManagedModule) -> bool:
        """保存单个模块的状态"""
        if module.obj is None:
            return True

        try:
            if module._save_fn:
                ok = module._save_fn()
            elif hasattr(module.obj, "save_state"):
                ok = module.obj.save_state()
            else:
                ok = True  # 无 save 方法，视为成功

            self._record_module_event(
                module, PHASE_SAVE, "save_state",
                success=ok,
            )
            return ok

        except Exception as e:
            module.status.last_error = str(e)
            self._record_module_event(
                module, PHASE_SAVE, "save_state",
                success=False, error=str(e),
            )
            logger.warning(f"模块 {module.name} 状态保存失败: {e}")
            return False

    # ============================================================
    # 生命周期：停止
    # ============================================================

    def stop_all(self) -> LifecycleRecord:
        """
        按依赖逆序停止所有模块。

        流程：
        1. 先保存所有模块状态（尝试性）
        2. 按依赖逆序停止模块
        3. 停止失败的模块记录但不影响其他模块
        """
        start_ts = time.time()
        before_state = self._current_phase
        self._current_phase = PHASE_STOP

        # 先尝试保存
        self.save_all()

        ordered = self._topological_sort()
        ordered.reverse()  # 逆序停止

        stopped_count = 0
        failed_count = 0
        errors: List[str] = []

        for name in ordered:
            module = self._modules.get(name)
            if module is None or module.obj is None:
                continue

            ok = self._stop_module(module)
            if ok:
                stopped_count += 1
            else:
                failed_count += 1
                errors.append(name)

        self._current_phase = PHASE_STOP
        self._total_stops += 1

        record = LifecycleRecord(
            phase=PHASE_STOP,
            action="stop_all",
            success=(failed_count == 0),
            before_state=before_state,
            after_state=PHASE_STOP,
            duration_ms=(time.time() - start_ts) * 1000,
            details={
                "stopped": stopped_count,
                "failed": failed_count,
                "errors": errors,
            },
        )
        self._add_record(record)
        logger.info(f"生命周期停止完成: {stopped_count} 成功, {failed_count} 失败")
        return record

    def _stop_module(self, module: ManagedModule) -> bool:
        """停止单个模块"""
        if module.obj is None:
            return True

        start_ts = time.time()
        before_state = module.status.state

        try:
            if module._stop_fn:
                ok = module._stop_fn()
            elif hasattr(module.obj, "stop"):
                ok = module.obj.stop()
            else:
                ok = True

            if ok:
                module.status.state = "STOPPED"
                module.status.stopped_at = now_iso()
                module.status.phase = PHASE_STOP
            else:
                module.status.state = "ERROR"
                module.status.error_count += 1

            self._record_module_event(
                module, PHASE_STOP, "stop_module",
                success=ok, before_state=before_state,
                after_state=module.status.state,
                duration_ms=(time.time() - start_ts) * 1000,
            )
            return ok

        except Exception as e:
            module.status.state = "ERROR"
            module.status.error_count += 1
            module.status.last_error = str(e)
            self._record_module_event(
                module, PHASE_STOP, "stop_module",
                success=False, error=str(e),
                before_state=before_state,
                after_state="ERROR",
                duration_ms=(time.time() - start_ts) * 1000,
            )
            logger.error(f"模块 {module.name} 停止异常: {e}")
            return False

    # ============================================================
    # 健康检查
    # ============================================================

    def health_check_all(self) -> Dict[str, Any]:
        """对所有模块执行健康检查"""
        results: Dict[str, Any] = {}
        healthy_count = 0
        degraded_count = 0
        error_count = 0

        for name, module in self._modules.items():
            if module.obj is None:
                results[name] = {"healthy": True, "state": "DISABLED"}
                continue

            try:
                if module._health_fn:
                    health = module._health_fn()
                elif hasattr(module.obj, "health_check"):
                    health = module.obj.health_check()
                    if hasattr(health, "to_dict"):
                        health = health.to_dict()
                else:
                    health = {"healthy": True}

                is_healthy = health.get("healthy", True) if isinstance(health, dict) else True
                module.status.healthy = is_healthy
                module.status.last_heartbeat = now_iso()
                if is_healthy:
                    healthy_count += 1
                else:
                    degraded_count += 1
                    module.status.state = "DEGRADED"

                results[name] = health

            except Exception as e:
                error_count += 1
                module.status.healthy = False
                module.status.error_count += 1
                module.status.last_error = str(e)
                results[name] = {"healthy": False, "error": str(e)}

        return {
            "total": len(self._modules),
            "healthy": healthy_count,
            "degraded": degraded_count,
            "error": error_count,
            "modules": results,
        }

    # ============================================================
    # 快照与历史
    # ============================================================

    def get_snapshot(self) -> LifecycleSnapshot:
        """生成生命周期完整快照"""
        modules = [m.status for m in self._modules.values()]
        running = sum(1 for m in modules if m.state == "RUNNING")
        error = sum(1 for m in modules if m.state == "ERROR")
        degraded = sum(1 for m in modules if m.state == "DEGRADED")

        uptime = (time.time() - self._start_time) if self._start_time > 0 else 0.0
        last_record = self._history[-1] if self._history else None

        return LifecycleSnapshot(
            current_phase=self._current_phase,
            uptime_seconds=uptime,
            total_modules=len(self._modules),
            running_modules=running,
            error_modules=error,
            degraded_modules=degraded,
            modules=modules,
            last_record_id=last_record.record_id if last_record else "",
            last_phase=last_record.phase if last_record else "",
            total_records=len(self._history),
            total_errors=self._total_errors,
            total_starts=self._total_starts,
            total_stops=self._total_stops,
            total_saves=self._total_saves,
        )

    def get_history(self, limit: int = 50) -> List[LifecycleRecord]:
        """获取生命周期历史（最新在前）"""
        hist = list(self._history)
        hist.reverse()
        return hist[:limit]

    def get_module_status(self, name: str) -> Optional[ModuleStatus]:
        """获取指定模块的状态"""
        module = self._modules.get(name)
        return module.status if module else None

    def get_current_phase(self) -> str:
        """获取当前生命周期阶段"""
        return self._current_phase

    def clear_history(self) -> int:
        """清空历史，返回清理数"""
        n = len(self._history)
        self._history.clear()
        if self._history_path:
            self._save_history_to_file([])
        return n

    # ============================================================
    # 内部方法
    # ============================================================

    def _topological_sort(self) -> List[str]:
        """
        按依赖关系拓扑排序。

        简化实现：使用 Kahn 算法。
        无依赖的模块按注册顺序排列。
        """
        # 构建依赖图
        in_degree: Dict[str, int] = {}
        graph: Dict[str, List[str]] = {}

        for name in self._module_order:
            module = self._modules.get(name)
            if module is None:
                continue
            in_degree[name] = 0
            graph[name] = []

        for name in self._module_order:
            module = self._modules.get(name)
            if module is None:
                continue
            for dep in module.dependencies:
                if dep in in_degree:
                    graph[dep].append(name)
                    in_degree[name] += 1

        # Kahn 算法
        queue = [n for n in self._module_order if in_degree.get(n, 0) == 0]
        result: List[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            for neighbor in graph.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # 处理循环依赖（未排序的节点直接追加）
        for name in self._module_order:
            if name not in result and name in self._modules:
                result.append(name)

        return result

    def _record_module_event(
        self,
        module: ManagedModule,
        phase: str,
        action: str,
        success: bool = True,
        error: str = "",
        before_state: str = "",
        after_state: str = "",
        duration_ms: float = 0.0,
    ) -> None:
        """记录模块级生命周期事件"""
        record = LifecycleRecord(
            phase=phase,
            module_name=module.name,
            action=action,
            success=success,
            error=error,
            before_state=before_state,
            after_state=after_state,
            duration_ms=duration_ms,
        )
        self._add_record(record)

    def _add_record(self, record: LifecycleRecord) -> None:
        """添加审计记录并持久化"""
        self._history.append(record)
        if not record.success:
            self._total_errors += 1
        if self._history_path:
            self._save_history_to_file(self._history)

    def _load_history_from_file(self) -> List[LifecycleRecord]:
        """从文件加载历史"""
        if not self._history_path or not self._history_path.exists():
            return []
        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            records = []
            for r in data:
                records.append(LifecycleRecord(
                    record_id=r.get("record_id", ""),
                    timestamp=r.get("timestamp", now_iso()),
                    phase=r.get("phase", ""),
                    module_name=r.get("module_name", ""),
                    action=r.get("action", ""),
                    success=r.get("success", True),
                    error=r.get("error", ""),
                    before_state=r.get("before_state", ""),
                    after_state=r.get("after_state", ""),
                    duration_ms=r.get("duration_ms", 0.0),
                    details=r.get("details", {}),
                ))
            return records
        except Exception as e:
            logger.error(f"加载生命周期历史失败: {e}")
            return []

    def _save_history_to_file(self, records: List[LifecycleRecord]) -> None:
        """持久化历史到文件"""
        if not self._history_path:
            return
        try:
            data = [r.to_dict() for r in records]
            with open(self._history_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存生命周期历史失败: {e}")
