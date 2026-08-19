"""
AuditStorage — 审计日志持久化

Phase 2.3 Audit Persistence Hardening:
- 存储格式: data/audit/audit_logs.jsonl(每行一条独立 JSON)
- 写入: append + flush + fsync(单条失败不损坏既有数据;进程异常退出不丢历史)
- 并发: per-path RLock(save 与 load 使用同一把锁;同目录多实例共享互斥)
- 读取: 逐行解析,单行损坏隔离(跳过 + warning,禁止整文件清空)
- 上限: 行数 > COMPACT_THRESHOLD 时低频压缩(持锁只读尾部 MAX_RECORDS 条,
  临时文件 + fsync + os.replace 原子替换;禁止每次 save 重写)
- 迁移: 旧 audit_logs.json 一次性迁移为 jsonl,原文件改名 .migrated.<ts> 留底,
  禁止删除原文件内容

公开 API 与旧实现完全兼容:save / load / load_by_user / load_by_type /
load_by_time_range / count 的签名与返回不变;模块函数 get_audit_storage /
save_audit_record / load_audit_records 不变。
"""

import json
import logging
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from src.audit.record import AuditRecord

logger = logging.getLogger(__name__)

# 与旧实现一致的记录数上限语义:读取与压缩都只保留最新 MAX_RECORDS 条
MAX_RECORDS = 10000
# 行数超过该值时触发低频压缩(每 COMPACT_THRESHOLD 次追加才发生一次)
COMPACT_THRESHOLD = 20000

# per-path 锁表:同一文件路径的所有 AuditStorage 实例共享互斥
_PATH_LOCKS: dict = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path) -> threading.RLock:
    key = os.path.abspath(str(path))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


class AuditStorage:
    def __init__(self, data_dir: str = None):
        if data_dir is None:
            # Phase 6.4: 支持环境变量配置（向后兼容）
            data_dir = os.environ.get("YUYI_AUDIT_DIR", "data/audit")
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.json_file = self.data_dir / "audit_logs.jsonl"
        self._legacy_json_file = self.data_dir / "audit_logs.json"
        self._line_count = 0

        with _path_lock(self.json_file):
            self._migrate_legacy_json()
            if not self.json_file.exists():
                self._init_json_file()
                self._line_count = 0
            else:
                self._line_count = self._count_lines()

    def _init_json_file(self):
        """确保 JSONL 文件存在（空文件为合法状态，load 返回 []）。"""
        try:
            with open(self.json_file, "a", encoding="utf-8"):
                pass
        except Exception as e:
            logger.error("[AuditStorage] 初始化失败: %s", e)

    def _count_lines(self) -> int:
        """统计物理行数（用于压缩触发，不受读取上限截断影响）。"""
        if not self.json_file.exists():
            return 0
        count = 0
        with open(self.json_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
        return count

    # ==========================
    # 写入（append-only）
    # ==========================

    def save(self, record: AuditRecord):
        try:
            with _path_lock(self.json_file):
                self._append_record(record)
                self._line_count += 1
                if self._line_count > COMPACT_THRESHOLD:
                    self._compact()
        except Exception as e:
            logger.error("[AuditStorage] 保存失败: %s", e)

    def _append_record(self, record: AuditRecord):
        line = json.dumps(record.to_dict(), ensure_ascii=False)
        with open(self.json_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass

    def _compact(self):
        """低频压缩：只读尾部 MAX_RECORDS 条有效记录，原子替换回 jsonl。

        调用前提：调用方已持有 _path_lock(self.json_file)。
        失败时旧文件保持完好（临时文件 + os.replace）。
        """
        records = self._read_records()  # 已截断为尾部 MAX_RECORDS 条
        tmp_path = f"{self.json_file}.tmp.{uuid.uuid4().hex[:8]}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp_path, self.json_file)
            self._line_count = len(records)
        except Exception:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise

    # ==========================
    # 读取（损坏行隔离）
    # ==========================

    def _read_records(self) -> List[dict]:
        """逐行解析 JSONL；单行损坏隔离（跳过 + warning），保留其他记录。"""
        records: List[dict] = []
        if not self.json_file.exists():
            return records
        corrupt_count = 0
        with open(self.json_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    corrupt_count += 1
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
                else:
                    corrupt_count += 1
        if corrupt_count:
            logger.warning(
                "[AuditStorage] 跳过 %d 行损坏记录: %s",
                corrupt_count,
                self.json_file,
            )
        if len(records) > MAX_RECORDS:
            records = records[-MAX_RECORDS:]
        return records

    def load(self, limit: int = 100, offset: int = 0) -> List[AuditRecord]:
        try:
            with _path_lock(self.json_file):
                records = self._read_records()
            records.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
            start = offset
            end = start + limit
            return [AuditRecord(**r) for r in records[start:end]]
        except Exception as e:
            logger.error("[AuditStorage] 加载失败: %s", e)
            return []

    def load_by_user(self, user_id: str, limit: int = 50) -> List[AuditRecord]:
        all_records = self.load(limit=1000)
        return [r for r in all_records if r.user_id == user_id][:limit]

    def load_by_type(self, operation_type: str, limit: int = 50) -> List[AuditRecord]:
        all_records = self.load(limit=1000)
        return [r for r in all_records if r.operation_type == operation_type][:limit]

    def load_by_time_range(
        self,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 50
    ) -> List[AuditRecord]:
        all_records = self.load(limit=1000)

        def in_range(record):
            ts = record.timestamp
            if start_time and ts < start_time:
                return False
            if end_time and ts > end_time:
                return False
            return True

        return [r for r in all_records if in_range(r)][:limit]

    def count(self) -> int:
        try:
            with _path_lock(self.json_file):
                return len(self._read_records())
        except Exception:
            return 0

    # ==========================
    # 旧格式迁移（一次性）
    # ==========================

    def _migrate_legacy_json(self):
        """旧 audit_logs.json → audit_logs.jsonl 一次性迁移。

        成功：原文件改名 audit_logs.json.migrated.<ts> 留底（内容保留，不删除）。
        损坏：原文件改名 audit_logs.json.corrupt.<ts> 留底，jsonl 从空开始。
        按 record_id 去重追加，迁移中断后重启可安全重入。
        """
        if not self._legacy_json_file.exists():
            return
        ts = datetime.now().strftime("%Y%m%dT%H%M%S%f")
        try:
            with open(self._legacy_json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("records"), list):
                raise ValueError("legacy format mismatch")

            existing_ids: set = set()
            if self.json_file.exists():
                with open(self.json_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                            if isinstance(rec, dict) and rec.get("record_id"):
                                existing_ids.add(rec["record_id"])
                        except Exception:
                            continue

            appended = 0
            with open(self.json_file, "a", encoding="utf-8") as f:
                for rec in data["records"]:
                    if (
                        isinstance(rec, dict)
                        and rec.get("record_id")
                        and rec["record_id"] not in existing_ids
                    ):
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        appended += 1
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass

            os.replace(
                self._legacy_json_file,
                f"{self._legacy_json_file}.migrated.{ts}",
            )
            logger.info(
                "[AuditStorage] 旧格式已迁移: %d 条 -> %s（原文件留底: .migrated.%s）",
                appended, self.json_file, ts,
            )
        except Exception as e:
            try:
                os.replace(
                    self._legacy_json_file,
                    f"{self._legacy_json_file}.corrupt.{ts}",
                )
            except OSError:
                pass
            logger.warning("[AuditStorage] 旧格式迁移失败（原文件已改名留底）: %s", e)


_global_storage = None
_global_storage_key = None


def get_audit_storage() -> AuditStorage:
    """
    Phase 6.4: 支持环境变量热切换（向后兼容）。

    行为：
    - 如果 YUYI_AUDIT_DIR 环境变量变化，自动重建 storage
    - 如果显式传入 data_dir，则使用传入值（不依赖全局单例）
    """
    global _global_storage, _global_storage_key
    # 读取当前环境变量
    current_key = os.environ.get("YUYI_AUDIT_DIR", "data/audit")
    # 单例 key 不匹配 → 重建
    if _global_storage is None or _global_storage_key != current_key:
        _global_storage = AuditStorage(data_dir=current_key)
        _global_storage_key = current_key
    return _global_storage


def save_audit_record(record: AuditRecord):
    get_audit_storage().save(record)


def load_audit_records(limit: int = 100, offset: int = 0) -> List[AuditRecord]:
    return get_audit_storage().load(limit=limit, offset=offset)
