# -*- coding: utf-8 -*-
"""
src/admin/selfmodel_consumer_audit.py

Phase 3.5.3 Step 2: SelfModel Consumer 审计层（append-only）

职责：
- 记录每次 GrowthProposal 消费结果（JSONL append-only）
- 提供 proposal_id 是否已消费查询
- 提供统计：total_processed / success_count / failed_count / last_processed_time
- 对损坏 JSONL 行做容错

设计原则：
- 不修改 selfmodel_consumer.py 主逻辑
- 通过 wrapper 模式（AuditedSelfModelConsumer）接入
- 与 SelfModelConsumer 解耦：audit 只看 result dict，不动 consumer 内部
- 不依赖 RuntimeCore / RuntimeBridge / Orchestrator / Growth / Personality

文件布局：
    <audit_dir>/
        consumer_audit.jsonl
            - 每行一条审计记录
            - 字段：proposal_id, timestamp, success, error, files_written, stats_snapshot

数据契约：
{
    "proposal_id": str,
    "timestamp": str,             # ISO 8601
    "success": bool,              # pcr_generated AND selfmodel_updated
    "applied": bool,              # apply_pcr 成功
    "dedup_skipped": bool,
    "pcr_generated": bool,
    "selfmodel_updated": bool,
    "error": str|None,
    "files_written": {
        "beliefs": str|None,
        "history": str|None,
        "reflection": str|None,
    },
    "schema": str|None,           # "growth_schema" / "proposal" / None
    "confidence": float|None,
    "actor": str,
}
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ============================================================
# 路径常量
# ============================================================

DEFAULT_AUDIT_DIR = "data/self_model_audit"
AUDIT_FILENAME = "consumer_audit.jsonl"


# ============================================================
# 工具
# ============================================================

def _now_iso() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# SelfModelConsumerAudit
# ============================================================

class SelfModelConsumerAudit:
    """
    SelfModel Consumer 审计器（append-only JSONL）。

    用法：
        audit = SelfModelConsumerAudit(audit_dir="/tmp/audit")
        # 写入
        audit.record(result_dict)
        # 查询
        if audit.has_processed("prop_xxx"):
            ...
        # 统计
        stats = audit.get_stats()
    """

    def __init__(
        self,
        audit_dir: Optional[str] = None,
        *,
        actor: str = "selfmodel_consumer_audit@phase_3_5_3",
    ) -> None:
        # 1) 目录处理
        if audit_dir is None:
            self._temp_dir: Optional[str] = None
            self._audit_dir = Path(tempfile.mkdtemp(prefix="phase_3_5_3_audit_"))
            self._temp_dir = str(self._audit_dir)
            self._owns_audit_dir = True
        else:
            self._audit_dir = Path(audit_dir)
            self._temp_dir = None
            self._owns_audit_dir = False
        try:
            self._audit_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"SelfModelConsumerAudit: cannot create dir {self._audit_dir}: {e}")

        self._audit_path = self._audit_dir / AUDIT_FILENAME
        self._actor = actor

        # 2) 内部缓存（用 file size + mtime 决定是否 reload）
        self._cached_processed: Optional[Set[str]] = None
        self._cache_signature: Optional[tuple] = None
        self._lock = threading.Lock()

    # ============================================================
    # 属性
    # ============================================================

    @property
    def audit_dir(self) -> Path:
        return self._audit_dir

    @property
    def audit_path(self) -> Path:
        return self._audit_path

    @property
    def actor(self) -> str:
        return self._actor

    # ============================================================
    # 内部：文件签名 / 缓存
    # ============================================================

    def _file_signature(self) -> Optional[tuple]:
        try:
            if not self._audit_path.exists():
                return None
            st = self._audit_path.stat()
            return (int(st.st_size), float(st.st_mtime))
        except Exception:
            return None

    def _is_cache_valid(self) -> bool:
        if self._cached_processed is None:
            return False
        sig = self._file_signature()
        return sig == self._cache_signature

    def _ensure_cache(self) -> None:
        """确保缓存反映当前 audit 文件。"""
        with self._lock:
            if self._is_cache_valid():
                return
            ids: Set[str] = set()
            for rec in self._iter_records():
                pid = rec.get("proposal_id")
                if isinstance(pid, str) and pid:
                    ids.add(pid)
            self._cached_processed = ids
            self._cache_signature = self._file_signature()

    def _iter_records(self) -> List[Dict[str, Any]]:
        """读取 audit 文件所有合法记录（损坏行跳过）。"""
        if not self._audit_path.exists():
            return []
        out: List[Dict[str, Any]] = []
        try:
            with open(self._audit_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        line = line.strip()
                        if not line:
                            continue
                        obj = json.loads(line)
                        if isinstance(obj, dict):
                            out.append(obj)
                    except (json.JSONDecodeError, ValueError):
                        # 损坏行：跳过（append-only 模式不能修改原文件）
                        continue
        except Exception as e:
            logger.warning(f"SelfModelConsumerAudit._iter_records failed: {e}")
        return out

    # ============================================================
    # 公开：record
    # ============================================================

    def record(
        self,
        result: Dict[str, Any],
        *,
        proposal_id: Optional[str] = None,
        confidence: Optional[float] = None,
        schema: Optional[str] = None,
    ) -> bool:
        """
        记录一次消费结果。

        Args:
            result: SelfModelConsumer.process() 返回的 dict
            proposal_id: 可选覆盖（默认从 result 取）
            confidence: 可选覆盖（默认从 evaluator_meta 推断）
            schema: 可选覆盖（默认从 evaluator_meta._source_schema 取）

        Returns:
            是否写入成功
        """
        with self._lock:
            try:
                pid = (
                    proposal_id
                    or result.get("proposal_id", "")
                    or ""
                )
                if not isinstance(pid, str):
                    pid = str(pid)

                applied = bool(result.get("selfmodel_updated", False))
                pcr_gen = bool(result.get("pcr_generated", False))
                success = pcr_gen and applied
                dedup = bool(result.get("dedup_skipped", False))

                # 提取 confidence / schema
                conf = confidence
                if conf is None:
                    env = result.get("envelope") or {}
                    # envelope 没有 confidence；尝试 result
                    if isinstance(result.get("confidence"), (int, float)):
                        conf = float(result["confidence"])
                schema_v = schema
                if schema_v is None:
                    # 从 envelope 或 result 推断
                    env = result.get("envelope") or {}
                    meta = env.get("evaluator_meta") or {}
                    schema_v = meta.get("_source_schema")

                record_obj: Dict[str, Any] = {
                    "proposal_id": pid,
                    "timestamp": _now_iso(),
                    "success": success,
                    "applied": applied,
                    "dedup_skipped": dedup,
                    "pcr_generated": pcr_gen,
                    "selfmodel_updated": applied,
                    "error": result.get("error"),
                    "files_written": dict(result.get("files_written") or {}),
                    "schema": schema_v,
                    "confidence": conf,
                    "actor": self._actor,
                }

                # 原子 append：先写 tmp，再 replace 追加
                # append-only：直接 a+ 模式打开写
                with open(self._audit_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record_obj, ensure_ascii=False, default=str) + "\n")
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except Exception:
                        pass

                # 失效缓存
                self._cached_processed = None
                self._cache_signature = None
                return True
            except Exception as e:
                logger.warning(f"SelfModelConsumerAudit.record failed: {e}")
                return False

    # ============================================================
    # 公开：has_processed
    # ============================================================

    def has_processed(self, proposal_id: str) -> bool:
        """查询 proposal_id 是否已经处理过（成功/失败都算）。"""
        if not proposal_id:
            return False
        self._ensure_cache()
        with self._lock:
            if self._cached_processed is None:
                return False
            return proposal_id in self._cached_processed

    def has_succeeded(self, proposal_id: str) -> bool:
        """查询 proposal_id 是否已经成功处理（success=True）。"""
        if not proposal_id:
            return False
        for rec in self._iter_records():
            if rec.get("proposal_id") == proposal_id:
                return bool(rec.get("success"))
        return False

    # ============================================================
    # 公开：get_stats
    # ============================================================

    def get_stats(self) -> Dict[str, Any]:
        """
        统计摘要。

        Returns:
            {
                "total_processed": int,        # audit 文件总记录数
                "success_count": int,          # success=True
                "failed_count": int,           # success=False
                "dedup_skipped_count": int,    # dedup_skipped=True
                "last_processed_time": str|None,
                "first_processed_time": str|None,
                "unique_proposal_ids": int,
                "audit_file_exists": bool,
                "audit_file_size_bytes": int,
            }
        """
        records = self._iter_records()
        total = len(records)
        success = 0
        failed = 0
        dedup = 0
        last_ts: Optional[str] = None
        first_ts: Optional[str] = None
        unique_ids: Set[str] = set()

        for rec in records:
            if rec.get("success"):
                success += 1
            else:
                failed += 1
            if rec.get("dedup_skipped"):
                dedup += 1
            ts = rec.get("timestamp")
            if isinstance(ts, str) and ts:
                if last_ts is None or ts > last_ts:
                    last_ts = ts
                if first_ts is None or ts < first_ts:
                    first_ts = ts
            pid = rec.get("proposal_id")
            if isinstance(pid, str) and pid:
                unique_ids.add(pid)

        size_bytes = 0
        file_exists = False
        try:
            if self._audit_path.exists():
                file_exists = True
                size_bytes = int(self._audit_path.stat().st_size)
        except Exception:
            pass

        return {
            "total_processed": int(total),
            "success_count": int(success),
            "failed_count": int(failed),
            "dedup_skipped_count": int(dedup),
            "last_processed_time": last_ts,
            "first_processed_time": first_ts,
            "unique_proposal_ids": int(len(unique_ids)),
            "audit_file_exists": bool(file_exists),
            "audit_file_size_bytes": int(size_bytes),
        }

    # ============================================================
    # 公开：list_records
    # ============================================================

    def list_records(
        self,
        *,
        success_only: bool = False,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """列出所有/仅成功的审计记录。"""
        records = self._iter_records()
        if success_only:
            records = [r for r in records if r.get("success")]
        return records[: max(0, int(limit))]

    # ============================================================
    # 清理
    # ============================================================

    def clear(self) -> bool:
        """清空 audit 文件（仅用于测试）。"""
        try:
            if self._audit_path.exists():
                self._audit_path.unlink()
            self._cached_processed = None
            self._cache_signature = None
            return True
        except Exception as e:
            logger.warning(f"SelfModelConsumerAudit.clear failed: {e}")
            return False

    def close(self) -> None:
        """关闭并清理临时目录（如有）。"""
        if self._owns_audit_dir and self._temp_dir:
            try:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
            except Exception:
                pass

    def __enter__(self) -> "SelfModelConsumerAudit":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


# ============================================================
# AuditedSelfModelConsumer（wrapper，不修改原 consumer）
# ============================================================

class AuditedSelfModelConsumer:
    """
    给 SelfModelConsumer 加审计的 wrapper。

    用法：
        consumer = SelfModelConsumer(data_dir=...)
        audit = SelfModelConsumerAudit(audit_dir=...)
        audited = AuditedSelfModelConsumer(consumer, audit)
        result = audited.process(proposal)  # 同时落 audit
    """

    def __init__(
        self,
        consumer: Any,
        audit: SelfModelConsumerAudit,
    ) -> None:
        self._consumer = consumer
        self._audit = audit

    @property
    def consumer(self) -> Any:
        return self._consumer

    @property
    def audit(self) -> SelfModelConsumerAudit:
        return self._audit

    def process(self, proposal: Any) -> Dict[str, Any]:
        """调用底层 consumer 并把结果写入 audit。"""
        result = self._consumer.process(proposal)
        try:
            self._audit.record(result)
        except Exception as e:
            # 审计失败不阻断主流程
            logger.warning(f"AuditedSelfModelConsumer: audit record failed (已隔离): {e}")
        return result

    def process_batch(self, proposals: List[Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for p in proposals:
            out.append(self.process(p))
        return out

    def close(self) -> None:
        try:
            self._consumer.close()
        except Exception:
            pass
        try:
            self._audit.close()
        except Exception:
            pass

    def __enter__(self) -> "AuditedSelfModelConsumer":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


# ============================================================
# 模块级便捷函数
# ============================================================

def create_audited_consumer(
    *,
    data_dir: Optional[str] = None,
    audit_dir: Optional[str] = None,
    **consumer_kwargs: Any,
) -> AuditedSelfModelConsumer:
    """
    一行创建带审计的 consumer。

    consumer_kwargs 透传给 SelfModelConsumer（如 actor / enable_dedup）。
    """
    from src.admin.selfmodel_consumer import SelfModelConsumer
    consumer = SelfModelConsumer(data_dir=data_dir, **consumer_kwargs)
    audit = SelfModelConsumerAudit(audit_dir=audit_dir)
    return AuditedSelfModelConsumer(consumer, audit)


__all__ = [
    "SelfModelConsumerAudit",
    "AuditedSelfModelConsumer",
    "create_audited_consumer",
    "DEFAULT_AUDIT_DIR",
    "AUDIT_FILENAME",
]
