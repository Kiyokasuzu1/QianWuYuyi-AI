# -*- coding: utf-8 -*-
"""
src/growth/sync/retry_worker.py

Phase 5.5.2 Stability Layer: RetryWorker

职责：
- 消费 RetryQueue 中的失败记录
- 执行 exponential backoff
- 最大 retry 次数（达到后转入 dead_letter_queue）
- 提供 tick() 触发式处理与 run() 循环处理

设计原则：
- 不修改 RetryQueue 内部结构
- 不引入 EventBus
- 通过 Callable 注入重试逻辑（不绑定 Authority）
- 失败不 silent fail
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# Dead Letter Queue
# ============================================================

@dataclass
class DeadLetterRecord:
    """死信队列记录（达到 max_retry 仍未成功）"""
    record_id: str = field(default_factory=lambda: f"dlq_{uuid.uuid4().hex[:10]}")
    original_record_id: str = ""
    proposal_id: str = ""
    operation: str = ""
    error_message: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    total_retries: int = 0
    failed_at: str = field(default_factory=_now_iso)
    final_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DeadLetterRecord":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class DeadLetterQueue:
    """死信队列（持久化到 JSON）"""

    DEFAULT_PATH = "data/growth/sync/dead_letter_queue.json"

    def __init__(self, storage_path: Optional[str] = None):
        self._path = Path(storage_path or self.DEFAULT_PATH)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._init_file()

    def _init_file(self) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump({"version": "1.0", "records": []}, f, ensure_ascii=False, indent=2)

    def _load(self) -> List[Dict[str, Any]]:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                return json.load(f).get("records", [])
        except Exception as e:
            logger.error(f"DeadLetterQueue 加载失败: {e}")
            return []

    def _save(self, records: List[Dict[str, Any]]) -> None:
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(
                    {"version": "1.0", "records": records},
                    f, ensure_ascii=False, indent=2,
                )
        except Exception as e:
            logger.error(f"DeadLetterQueue 保存失败: {e}")

    def add(self, record: DeadLetterRecord) -> None:
        records = self._load()
        records.append(record.to_dict())
        self._save(records)
        logger.error(
            f"DeadLetterQueue: 记录 {record.record_id} 进入死信 "
            f"({record.operation} {record.proposal_id})"
        )

    def list_all(self, limit: int = 100) -> List[DeadLetterRecord]:
        records = self._load()
        return [DeadLetterRecord.from_dict(r) for r in records[-limit:]]

    def count(self) -> int:
        return len(self._load())

    def clear(self) -> None:
        """清空（仅供测试）"""
        self._save([])


# ============================================================
# Retry Worker
# ============================================================

@dataclass
class WorkerStats:
    """Worker 统计信息"""
    total_ticks: int = 0
    total_processed: int = 0
    total_succeeded: int = 0
    total_failed: int = 0
    total_dead_lettered: int = 0
    total_backoff_wait: float = 0.0  # 累计退避等待时间（秒）
    last_tick_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RetryWorker:
    """
    消费 RetryQueue 的后台 worker。

    特性：
    - exponential backoff: backoff = base * 2^retry_count
    - max_retry 后转入 DeadLetterQueue
    - 失败不 silent fail
    - 提供 tick() 触发式和 run() 循环式处理

    使用示例：
        def my_handler(record):
            # 自定义重试逻辑
            do_something(record.proposal_id, record.payload)
            return True  # True=成功，False/Exception=失败

        worker = RetryWorker(
            retry_queue=my_queue,
            handler=my_handler,
            max_retry=3,
            backoff_base=1.0,
        )
        worker.tick()  # 处理一次
        worker.run(max_iterations=10)  # 循环处理
    """

    def __init__(
        self,
        retry_queue: Any,
        handler: Optional[Callable[[Any], bool]] = None,
        dead_letter_queue: Optional[DeadLetterQueue] = None,
        max_retry: int = 3,
        backoff_base: float = 1.0,
        backoff_cap: float = 60.0,
        batch_size: int = 10,
        dry_run: bool = False,
    ):
        """
        Args:
            retry_queue: RetryQueue 实例
            handler: (record) -> bool  重试逻辑；返回 True=成功
            dead_letter_queue: 死信队列（None=使用默认）
            max_retry: 最大重试次数（达到后入死信）
            backoff_base: 退避基数（秒）
            backoff_cap: 最大退避（秒）
            batch_size: 每次 tick 处理的记录数
            dry_run: True=不实际执行 handler，仅模拟
        """
        self._retry_queue = retry_queue
        self._handler = handler or self._default_handler
        self._dlq = dead_letter_queue or DeadLetterQueue()
        self._max_retry = max(1, int(max_retry))
        self._backoff_base = max(0.0, float(backoff_base))
        self._backoff_cap = max(self._backoff_base, float(backoff_cap))
        self._batch_size = max(1, int(batch_size))
        self._dry_run = dry_run
        self._stats = WorkerStats()

    @property
    def stats(self) -> WorkerStats:
        return self._stats

    @property
    def dead_letter_queue(self) -> DeadLetterQueue:
        return self._dlq

    def _default_handler(self, record: Any) -> bool:
        """默认 handler（仅记录日志）"""
        logger.warning(
            f"RetryWorker: 未提供 handler，跳过 record {record.record_id}"
        )
        return False

    def _compute_backoff(self, retry_count: int) -> float:
        """计算 exponential backoff"""
        delay = self._backoff_base * (2 ** max(0, retry_count))
        return min(delay, self._backoff_cap)

    def _should_retry(self, record: Any) -> bool:
        """判断是否应继续重试（未达到 max_retry）"""
        current = int(getattr(record, "retry_count", 0) or 0)
        return current < self._max_retry

    def tick(self, sleep_fn: Optional[Callable[[float], None]] = None) -> WorkerStats:
        """
        处理一个批次。

        Args:
            sleep_fn: 注入的 sleep 函数（测试用，避免真实等待）

        Returns:
            更新后的 WorkerStats
        """
        sleep_fn = sleep_fn or time.sleep
        self._stats.total_ticks += 1
        self._stats.last_tick_at = _now_iso()

        pending = []
        try:
            pending = self._retry_queue.get_pending(limit=self._batch_size)
        except Exception as e:
            logger.error(f"RetryWorker: 获取 pending 失败: {e}")
            return self._stats

        for record in pending:
            self._stats.total_processed += 1
            self._process_one(record, sleep_fn)

        return self._stats

    def _process_one(self, record: Any, sleep_fn: Callable[[float], None]) -> None:
        """处理单条记录"""
        if not self._should_retry(record):
            self._send_to_dlq(record, final_reason="max_retry_exceeded")
            return

        # 计算 backoff
        backoff = self._compute_backoff(int(getattr(record, "retry_count", 0) or 0))
        if backoff > 0:
            self._stats.total_backoff_wait += backoff
            if not self._dry_run:
                try:
                    sleep_fn(backoff)
                except Exception as e:
                    logger.error(f"RetryWorker: sleep 异常: {e}")

        # 执行 handler
        success = False
        error_message = ""
        if self._dry_run:
            success = True  # dry_run 视为成功
        else:
            try:
                success = bool(self._handler(record))
            except Exception as e:
                success = False
                error_message = f"handler_exception: {e}"

        if success:
            try:
                self._retry_queue.mark_success(record.record_id)
                self._stats.total_succeeded += 1
                logger.info(
                    f"RetryWorker: 重试成功 {record.record_id} "
                    f"({record.operation} {record.proposal_id})"
                )
            except Exception as e:
                logger.error(f"RetryWorker: mark_success 失败: {e}")
                self._stats.total_failed += 1
        else:
            try:
                updated = self._retry_queue.mark_retry(
                    record.record_id,
                    error_message=error_message or record.error_message,
                )
                # mark_retry 内已处理 max_retry 判定；这里再检查 status
                if updated.status == "abandoned":
                    self._send_to_dlq(
                        record,
                        final_reason="abandoned_after_max_retry",
                    )
                else:
                    self._stats.total_failed += 1
            except Exception as e:
                logger.error(f"RetryWorker: mark_retry 失败: {e}")
                self._stats.total_failed += 1

    def _send_to_dlq(self, record: Any, final_reason: str) -> None:
        """转入死信队列"""
        dlq_record = DeadLetterRecord(
            original_record_id=getattr(record, "record_id", ""),
            proposal_id=getattr(record, "proposal_id", ""),
            operation=getattr(record, "operation", ""),
            error_message=getattr(record, "error_message", ""),
            payload=dict(getattr(record, "payload", {}) or {}),
            total_retries=int(getattr(record, "retry_count", 0) or 0),
            final_reason=final_reason,
        )
        try:
            self._dlq.add(dlq_record)
            self._stats.total_dead_lettered += 1
            # 标记原 record 为 abandoned
            try:
                self._retry_queue.abandon(record.record_id)
            except Exception:
                pass
        except Exception as e:
            logger.error(f"RetryWorker: DLQ 写入失败: {e}")

    def run(
        self,
        max_iterations: int = 10,
        sleep_fn: Optional[Callable[[float], None]] = None,
    ) -> WorkerStats:
        """
        循环处理直到无 pending 或达到 max_iterations。

        Args:
            max_iterations: 最大迭代次数
            sleep_fn: 注入的 sleep 函数

        Returns:
            最终 WorkerStats
        """
        for i in range(max_iterations):
            self.tick(sleep_fn=sleep_fn)
            try:
                pending_count = len(self._retry_queue.get_pending(limit=1000))
            except Exception:
                break
            if pending_count == 0:
                break
        return self._stats

    def reset_stats(self) -> None:
        """重置统计（仅供测试）"""
        self._stats = WorkerStats()


# ============================================================
# 模块级单例辅助
# ============================================================

_worker_instance: Optional[RetryWorker] = None


def get_retry_worker() -> Optional[RetryWorker]:
    return _worker_instance


def set_retry_worker(worker: Optional[RetryWorker]) -> None:
    global _worker_instance
    _worker_instance = worker
