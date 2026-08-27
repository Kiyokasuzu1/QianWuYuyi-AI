# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle/tasks/memory_cycle.py

P2.7 Phase D-1: Memory Consolidation 周期任务(Thin Trigger)。

职责(任务书 Step 4):
- 周期调用 MemoryConsolidationEngine.consolidate()(只读整理)
- 不修改 MemoryStore(只 load 读取,绝不 save/写回)
- 输出整理结果摘要(metrics.report_summary)

红线:
- 禁止删除 / 修改旧记忆
- 整理结果只进入审计,不写回 MemoryStore
- 若未来需要把整理结论转化为记忆变更,必须走已有治理链提案
  (本阶段不实现,仅预留 produced_proposal_ids 审计位)

注入设计:
- engine: MemoryConsolidationEngine 实例(默认惰性构造,无外部依赖)
- memory_loader: Callable[[], List[dict]](默认惰性 MemoryStore.load,
  只读;测试/实验注入 fake 或临时路径 loader)
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from src.contracts.lifecycle_event_schema import TASK_TYPE_MEMORY_CYCLE
from src.runtime.lifecycle.lifecycle_decision import AfterInterval
from src.runtime.lifecycle.lifecycle_task import BaseLifecycleTask

DEFAULT_MEMORY_PATH = "data/memory.json"
DEFAULT_INTERVAL_SECONDS = 86400.0  # 默认每日一次
DEFAULT_LIMIT = 50
DEFAULT_BUDGET_MS = 5000
OWNER = "p2.7.d1"
TASK_ID = "memory.cycle"


def _build_default_loader(memory_path: str) -> Callable[[], List[Dict[str, Any]]]:
    """惰性构造 MemoryStore 只读 loader(文件不存在时 load 返回空列表)。"""

    def _load() -> List[Dict[str, Any]]:
        from src.memory.memory_store import MemoryStore

        store = MemoryStore(str(memory_path))
        return list(store.load() or [])

    return _load


class MemoryCycleTask(BaseLifecycleTask):
    """记忆整理周期任务:只读 consolidate + 摘要审计。"""

    TASK_TYPE = TASK_TYPE_MEMORY_CYCLE
    IDEMPOTENCY_BUCKET_SECONDS = 86400  # 幂等键按自然日分桶

    def __init__(
        self,
        engine: Any = None,
        memory_loader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        *,
        task_id: str = TASK_ID,
        interval_seconds: Optional[float] = None,
        limit: int = DEFAULT_LIMIT,
        budget_ms: Optional[int] = DEFAULT_BUDGET_MS,
        memory_path: str = DEFAULT_MEMORY_PATH,
    ) -> None:
        interval = float(
            interval_seconds if interval_seconds is not None else DEFAULT_INTERVAL_SECONDS
        )
        super().__init__(
            task_id,
            OWNER,
            display_name="Memory Consolidation Cycle",
            condition=AfterInterval(interval),
            budget_ms=budget_ms,
            interval_seconds=interval,
            max_concurrent=1,
        )
        if engine is None:
            from src.memory.memory_consolidation_engine import MemoryConsolidationEngine

            engine = MemoryConsolidationEngine()
        self._engine = engine
        self._memory_loader = (
            memory_loader if memory_loader is not None else _build_default_loader(memory_path)
        )
        try:
            self._limit = int(limit or DEFAULT_LIMIT)
        except Exception:
            self._limit = DEFAULT_LIMIT
        if self._limit <= 0:
            self._limit = DEFAULT_LIMIT
        self.memory_path = str(memory_path)

    # --------------------------------------------------------
    # 执行
    # --------------------------------------------------------
    def execute(self, context) -> Dict[str, Any]:
        """只读执行:加载记忆 → consolidate → 返回摘要 metrics。

        返回 dict 由 BaseLifecycleTask.run_safely 包装为 SUCCESS LifecycleResult。
        任何异常同样由 run_safely 隔离为 FAILED(不向上传播)。
        """
        memories: List[Dict[str, Any]] = list(self._memory_loader() or [])
        report = self._engine.consolidate(memories, limit=self._limit)
        stats = dict(getattr(report, "stats", {}) or {})
        return {
            "metrics": {
                "input_count": len(memories),
                "report_summary": stats,
            },
        }


__all__ = [
    "MemoryCycleTask",
    "DEFAULT_MEMORY_PATH",
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_LIMIT",
    "DEFAULT_BUDGET_MS",
]
