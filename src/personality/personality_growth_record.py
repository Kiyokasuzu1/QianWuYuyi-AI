"""
人格成长记录 (PersonalityGrowthRecord) v1.2

职责：
记录一次人格变化背后的意义，连接"经历 → 人格变化 → 自我理解"。

v1.2 (Phase 3.8.3-C) 更新：
- 新增 source_growth_record_id：追溯来源 GrowthRecord
- 新增 evidence_ids：保存支持该成长判断的原始证据
- 旧数据兼容：缺失字段默认 None / []

v1.1 修正：
- record_id 使用 uuid 保证唯一性
- changes 类型定义 TraitChange，避免类型冲突
- 增加 validate_record 验证方法（含空 changes 检查）
- PersonalityGrowthHistory 增加 all() 方法
"""

from typing import TypedDict, Dict, List, Optional
from datetime import datetime
import json
import logging
import os
import shutil
import threading
import uuid
from pathlib import Path

from src.memory.atomic_write import atomic_write_json

logger = logging.getLogger(__name__)

# ============================================================
# Phase 2.4: per-path RLock + 损坏备份（与 memory/audit 同模式）
# ============================================================
_PATH_LOCKS: Dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path) -> threading.RLock:
    """按绝对路径取 RLock（可重入，同线程嵌套写安全）。"""
    key = os.path.abspath(str(path))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _backup_corrupt(path) -> None:
    """把损坏文件复制为 .corrupt.timestamp 备份（copy 而非 move，
    不干扰并发写者；备份失败不阻断主流程）。"""
    try:
        src = Path(path)
        if not src.exists():
            return
        backup = Path(
            f"{path}.corrupt.{datetime.now().strftime('%Y%m%dT%H%M%S%f')}"
        )
        shutil.copy2(src, backup)
        logger.warning("PersonalityGrowthHistory 损坏文件已备份: %s", backup)
    except Exception as e:  # noqa: BLE001
        logger.warning("PersonalityGrowthHistory 损坏文件备份失败: %s", e)


class TraitChange(TypedDict, total=False):
    """单个人格维度的变化详情"""
    before: float
    after: float
    delta: float
    momentum_before: float
    momentum_after: float
    reason: str


class PersonalityGrowthRecord(TypedDict, total=False):
    """人格成长记录"""

    # ---- 标识 ----
    record_id: str
    timestamp: str

    # ---- 来源追溯 (Phase 3.8.3-C) ----
    source_growth_record_id: Optional[str]  # 来源 GrowthRecord 的 record_id
    evidence_ids: Optional[List[str]]       # 支持该成长判断的原始证据ID列表

    # ---- 来源 ----
    trigger_events: List[str]

    # ---- 人格变化 ----
    changes: Dict[str, TraitChange]
    affected_dimensions: List[str]

    # ---- 成长理解 ----
    meaning: str
    narrative: str
    confidence: float

    # ---- 元数据 ----
    validation_count: int
    growth_level: str


def create_personality_growth_record(
    trigger_events: List[str],
    changes: Dict[str, TraitChange],
    affected_dimensions: List[str],
    meaning: str,
    narrative: str = "",
    confidence: float = 0.5,
    validation_count: int = 1,
    growth_level: str = "context",
    source_growth_record_id: Optional[str] = None,
    evidence_ids: Optional[List[str]] = None,
) -> PersonalityGrowthRecord:
    """创建一条人格成长记录"""
    return {
        "record_id": f"pgr_{uuid.uuid4().hex[:8]}",
        "timestamp": datetime.now().isoformat(),
        "source_growth_record_id": source_growth_record_id,
        "evidence_ids": evidence_ids or [],
        "trigger_events": trigger_events,
        "changes": changes,
        "affected_dimensions": affected_dimensions,
        "meaning": meaning,
        "narrative": narrative,
        "confidence": confidence,
        "validation_count": validation_count,
        "growth_level": growth_level,
    }


def validate_record(record: PersonalityGrowthRecord) -> bool:
    """验证成长记录的有效性"""
    confidence = record.get("confidence", 0)
    if not 0.0 <= confidence <= 1.0:
        return False

    valid_levels = {"context", "preference", "trait"}
    if record.get("growth_level") not in valid_levels:
        return False

    if not record.get("affected_dimensions"):
        return False

    if not record.get("changes"):
        return False

    if not record.get("meaning"):
        return False

    return True


class PersonalityGrowthHistory:
    """人格成长历史管理器

    v1.3 (Phase 3.8.5): 新增 JSON 文件持久化，支持重启后恢复。
    """

    DEFAULT_STORAGE_PATH = "data/personality_growth_history.json"

    def __init__(self, storage_path: Optional[str] = None):
        """
        Args:
            storage_path: 持久化文件路径。为 None 时不启用持久化（向后兼容）。
        """
        self.records: List[PersonalityGrowthRecord] = []
        self._storage_path: Optional[Path] = None
        if storage_path is not None:
            self._storage_path = Path(storage_path)
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            self._load_from_disk()

    def add(self, record: PersonalityGrowthRecord) -> bool:
        """
        添加一条成长记录。
        返回 True 表示添加成功，False 表示验证失败。
        Phase 3.8.4: 失败时内部记录日志，调用方无需关心验证细节。
        Phase 2.4: 持久化实例在 per-path 锁内先合并磁盘记录（按 record_id
        去重）再追加，避免多实例整盘覆盖造成的丢失更新。
        """
        if not validate_record(record):
            logger.warning(
                "PersonalityGrowthHistory.add() 验证失败: "
                "record_id=%s, confidence=%s, growth_level=%s, "
                "affected_dimensions=%s, changes=%s, meaning=%s",
                record.get("record_id"),
                record.get("confidence"),
                record.get("growth_level"),
                record.get("affected_dimensions"),
                list(record.get("changes", {}).keys()) if record.get("changes") else None,
                record.get("meaning", "")[:50] if record.get("meaning") else None,
            )
            return False

        if self._storage_path is None:
            if record.get("record_id") and any(
                isinstance(r, dict) and r.get("record_id") == record.get("record_id")
                for r in self.records
            ):
                return True  # 幂等：同 record_id 已存在，不重复追加
            self.records.append(record)
            return True

        with _path_lock(self._storage_path):
            self._merge_from_disk_locked()
            if record.get("record_id") and any(
                isinstance(r, dict) and r.get("record_id") == record.get("record_id")
                for r in self.records
            ):
                return True  # 幂等：合并后同 record_id 已存在，不重复追加
            self.records.append(record)
            self._save_to_disk()
        return True

    def all(self) -> List[PersonalityGrowthRecord]:
        """返回所有记录"""
        return self.records

    def latest(self) -> Optional[PersonalityGrowthRecord]:
        """获取最近一条记录"""
        return self.records[-1] if self.records else None

    def get_by_dimension(self, trait: str) -> List[PersonalityGrowthRecord]:
        """获取影响某个维度的所有成长记录"""
        return [
            r for r in self.records
            if trait in r.get("affected_dimensions", [])
        ]

    def get_by_level(self, level: str) -> List[PersonalityGrowthRecord]:
        """获取指定成长层级的所有记录"""
        return [
            r for r in self.records
            if r.get("growth_level") == level
        ]

    def get_high_confidence(self, threshold: float = 0.7) -> List[PersonalityGrowthRecord]:
        """获取高置信度的成长记录"""
        return [
            r for r in self.records
            if r.get("confidence", 0) >= threshold
        ]

    def count(self) -> int:
        """记录总数"""
        return len(self.records)

    # ============================================================
    # Phase 3.8.5: 持久化
    # ============================================================
    def save(self) -> None:
        """手动触发持久化（通常 add() 会自动调用）。"""
        self._save_to_disk()

    def _merge_from_disk_locked(self) -> None:
        """Phase 2.4: 写前合并磁盘记录（多实例共享文件时的丢失更新防护）。

        以 record_id 去重：把内存中不存在的磁盘记录追加到内存尾部，
        随后写入新记录，避免「实例 B 先写、实例 A 后整盘覆盖」丢失 B 的记录。
        读取/解析失败 → 备份损坏文件，保持内存数据继续服务（不回写）。
        """
        if self._storage_path is None or not self._storage_path.exists():
            return
        try:
            with open(self._storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            disk_records = data.get("records", [])
            if not isinstance(disk_records, list):
                return
            known = {
                str(r.get("record_id"))
                for r in self.records
                if isinstance(r, dict) and r.get("record_id")
            }
            for r in disk_records:
                if not isinstance(r, dict):
                    continue
                rid = str(r.get("record_id") or "")
                if rid and rid not in known:
                    self.records.append(r)
                    known.add(rid)
        except Exception as e:
            logger.warning(
                "PersonalityGrowthHistory 合并磁盘记录失败（已备份损坏文件）: %s", e,
            )
            try:
                _backup_corrupt(self._storage_path)
            except Exception:  # noqa: BLE001
                pass

    def _save_to_disk(self) -> None:
        """保存记录到 JSON 文件（Phase 2.4: per-path RLock + 原子写）。"""
        if self._storage_path is None:
            return
        try:
            data = {
                "version": 1,
                "saved_at": datetime.now().isoformat(),
                "count": len(self.records),
                "records": self.records,
            }
            with _path_lock(self._storage_path):
                atomic_write_json(str(self._storage_path), data)
        except Exception as e:
            logger.warning("PersonalityGrowthHistory 保存失败: %s", e)

    def _load_from_disk(self) -> None:
        """从 JSON 文件恢复记录。"""
        if self._storage_path is None or not self._storage_path.exists():
            return
        try:
            with open(self._storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            loaded = data.get("records", [])
            if isinstance(loaded, list):
                self.records = loaded
                logger.info(
                    "PersonalityGrowthHistory 从磁盘恢复 %d 条记录 (version=%s)",
                    len(self.records),
                    data.get("version", "unknown"),
                )
        except Exception as e:
            # Phase 2.4: 损坏 → 备份后置空内存；旧数据文件保留不覆盖。
            logger.warning(
                "PersonalityGrowthHistory 加载失败（已备份损坏文件）: %s", e,
            )
            try:
                _backup_corrupt(self._storage_path)
            except Exception:  # noqa: BLE001
                pass
            self.records = []