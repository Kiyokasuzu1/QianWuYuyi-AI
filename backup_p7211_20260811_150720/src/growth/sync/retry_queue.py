# -*- coding: utf-8 -*-
"""
src/growth/sync/retry_queue.py

Phase 5.5.1 Hotfix [4]: Mirror 同步失败 Retry Queue

目的：
- 记录 Mirror 同步失败
- 提供 retry 机制
- 持久化失败记录（便于人工干预）

设计原则：
- 不引入 EventBus（保持最小侵入）
- 不修改 MirrorStorage 内部逻辑
- 提供纯函数式 retry API
- 持久化到独立 JSON 文件
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class RetryRecord:
    """
    Mirror 同步失败 Retry 记录。

    Attributes:
        record_id: 唯一 ID
        timestamp: 创建时间
        proposal_id: 失败的 Proposal ID（Type A 或 Type B）
        operation: 失败操作类型 (mirror / sync_status)
        error_message: 错误信息
        payload: 失败时的 payload（用于 retry）
        retry_count: 已重试次数
        next_retry_at: 下次重试时间（ISO 格式）
        status: pending / success / abandoned
        last_attempt_at: 最后一次尝试时间
    """
    record_id: str = field(default_factory=lambda: f"retry_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=_now_iso)
    proposal_id: str = ""
    operation: str = "mirror"
    error_message: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0
    next_retry_at: Optional[str] = None
    status: str = "pending"
    last_attempt_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RetryRecord":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class RetryQueue:
    """
    Mirror 同步失败 Retry Queue。

    用法：
        queue = RetryQueue()
        queue.add_failure(
            proposal_id="gov_xxx",
            operation="mirror",
            error_message="IO error",
            payload={"type_b_dict": {...}},
        )
        # 重试
        for record in queue.get_pending():
            # 尝试重试...
            queue.mark_success(record.record_id)
    """

    DEFAULT_PATH = "data/growth/sync/retry_queue.json"

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
                data = json.load(f)
            return data.get("records", [])
        except Exception as e:
            logger.error(f"RetryQueue 加载失败: {e}")
            return []

    def _save(self, records: List[Dict[str, Any]]) -> None:
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(
                    {"version": "1.0", "records": records},
                    f, ensure_ascii=False, indent=2,
                )
        except Exception as e:
            logger.error(f"RetryQueue 保存失败: {e}")

    # ============================================================
    # 写入接口
    # ============================================================

    def add_failure(
        self,
        proposal_id: str,
        operation: str,
        error_message: str,
        payload: Optional[Dict[str, Any]] = None,
        max_retry: int = 3,
    ) -> RetryRecord:
        """
        添加失败记录到重试队列。

        Args:
            proposal_id: 失败的 Proposal ID
            operation: 操作类型 (mirror / sync_status)
            error_message: 错误信息
            payload: 失败时的 payload
            max_retry: 最大重试次数（达到后标记为 abandoned）

        Returns:
            RetryRecord
        """
        record = RetryRecord(
            proposal_id=proposal_id,
            operation=operation,
            error_message=error_message,
            payload=payload or {},
        )
        records = self._load()
        records.append(record.to_dict())
        self._save(records)
        logger.warning(
            f"RetryQueue: 新增失败记录 {record.record_id} "
            f"({operation} {proposal_id}: {error_message})"
        )
        return record

    def mark_success(self, record_id: str) -> bool:
        """标记重试成功"""
        records = self._load()
        for r in records:
            if r.get("record_id") == record_id:
                r["status"] = "success"
                r["last_attempt_at"] = _now_iso()
                self._save(records)
                return True
        return False

    def mark_retry(self, record_id: str, error_message: str = "") -> RetryRecord:
        """标记一次重试失败（增加 retry_count）"""
        records = self._load()
        for r in records:
            if r.get("record_id") == record_id:
                r["retry_count"] = int(r.get("retry_count", 0)) + 1
                r["last_attempt_at"] = _now_iso()
                r["error_message"] = error_message or r.get("error_message", "")
                if r["retry_count"] >= 3:
                    r["status"] = "abandoned"
                self._save(records)
                return RetryRecord.from_dict(r)
        return RetryRecord()

    def abandon(self, record_id: str) -> bool:
        """手动标记为 abandoned"""
        records = self._load()
        for r in records:
            if r.get("record_id") == record_id:
                r["status"] = "abandoned"
                self._save(records)
                return True
        return False

    # ============================================================
    # 读取接口
    # ============================================================

    def get_pending(self, limit: int = 50) -> List[RetryRecord]:
        """获取待重试的记录"""
        records = self._load()
        out = []
        for r in records:
            if r.get("status") == "pending" and int(r.get("retry_count", 0)) < 3:
                out.append(RetryRecord.from_dict(r))
        return out[:limit]

    def get_all(self, limit: int = 100) -> List[RetryRecord]:
        """获取所有记录"""
        records = self._load()
        return [RetryRecord.from_dict(r) for r in records[-limit:]]

    def count(self) -> int:
        return len(self._load())

    def count_by_status(self, status: str) -> int:
        return sum(1 for r in self._load() if r.get("status") == status)

    def clear(self) -> None:
        """清空（仅供测试）"""
        self._save([])


# 模块级单例
_retry_queue_instance: Optional[RetryQueue] = None


def get_retry_queue() -> RetryQueue:
    """获取 RetryQueue 单例"""
    global _retry_queue_instance
    if _retry_queue_instance is None:
        _retry_queue_instance = RetryQueue()
    return _retry_queue_instance


def reset_retry_queue_for_testing() -> None:
    """测试用：重置单例"""
    global _retry_queue_instance
    _retry_queue_instance = None
