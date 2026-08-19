# -*- coding: utf-8 -*-
"""
src/runtime/experience_journal.py

Phase 4.1D —— ExperienceJournal：Runtime Experience 的最小持久化。

背景（Phase 4.1C 审计结论，docs/audit/PHASE4_1C_MEMORY_CONTINUITY_AUDIT.md）：
  Phase C.2.3 起 PollutionGuard 明确拒绝 runtime_experience 进入 MemoryStore
  （type 硬黑名单 + role=system 黑名单 + [RuntimeExperience] 内容模式），
  memory.json 的语义是「羽依关于用户、关系、事实的长期记忆」。
  RuntimeExperience 是「羽依自己的运行经历记录」——两者不是同一种数据。
  但经验因此完全没有持久化（handle_completed_experience 每次静默失败），
  Growth/Reflection 的跨重启 readback 断裂。

本模块职责（仅此一项）：
  append-only JSONL 经验日志，供 MemoryAdapter 读写：
    Experience → store_experience → journal → get_recent_experiences → Growth

设计约束（与 ActionPersistenceManager / EvolutionHistoryStore 同模式）：
  - 仅 stdlib，线程锁保护，append-only（从不 truncate）
  - fail-soft：写盘失败自动降级为内存模式（本进程内仍可读），不抛异常
  - 读盘损坏行隔离：跳过 + 计入 corrupt_count
  - 容量保护：超过 MAX_JOURNAL_BYTES 单轮转（.1 后缀）
  - 不 import src.memory.* —— 经验不进入 Memory 系统
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

EXPERIENCE_JOURNAL_SCHEMA_VERSION = "1.0"
DEFAULT_JOURNAL_PATH = "data/experience_journal.jsonl"
MAX_JOURNAL_BYTES = 64 * 1024 * 1024  # 64 MB safety guard（与既有 store 惯例一致）


class ExperienceJournal:
    """append-only JSONL 经验日志（fail-soft）。

    每行一个 JSON object，格式即 MemoryAdapter._convert_to_memory 产出的
    记录 dict（含 metadata.type == "runtime_experience"）。
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = path or DEFAULT_JOURNAL_PATH
        self._lock = threading.Lock()
        # 写盘失败后的内存降级缓冲（本进程内 get_recent_experiences 仍可用）
        self._memory_fallback: List[Dict[str, Any]] = []
        self._degraded = False
        self._corrupt_count = 0
        self._last_error: Optional[str] = None

    # -------------------- 写 --------------------

    def append(self, record: Dict[str, Any]) -> bool:
        """追加一条经验记录。任何失败降级内存模式，不抛异常。"""
        if not isinstance(record, dict):
            return False
        line: Optional[str] = None
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"serialize_failed: {exc}"
            return False
        with self._lock:
            try:
                self._rotate_if_needed()
                folder = os.path.dirname(self._path)
                if folder:
                    os.makedirs(folder, exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except OSError:
                        pass
                return True
            except Exception as exc:  # noqa: BLE001
                if not self._degraded:
                    logger.warning(
                        "[ExperienceJournal] 写盘失败，降级为内存模式: %s", exc,
                    )
                self._degraded = True
                self._last_error = str(exc)
                self._memory_fallback.append(record)
                return True  # 降级后本进程仍可读，语义上「已记录」

    def _rotate_if_needed(self) -> None:
        """超过容量上限时单轮转（journal → journal.1）。"""
        try:
            if os.path.exists(self._path) and (
                os.path.getsize(self._path) > MAX_JOURNAL_BYTES
            ):
                rotated = self._path + ".1"
                if os.path.exists(rotated):
                    os.remove(rotated)
                os.replace(self._path, rotated)
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"rotate_failed: {exc}"

    # -------------------- 读 --------------------

    def get_recent(self, n: int = 5) -> List[Dict[str, Any]]:
        """读取最近 N 条有效记录（按时间倒序）。

        Phase 3.7.1：供 Stage 14 将近期经历注入 Prompt，
        使羽依在回复时能感知最近发生过的重要事件。

        Args:
            n: 返回最近 N 条记录

        Returns:
            List[Dict]: 最近 N 条记录（按时间倒序），最多 n 条。
        """
        records = self.load()
        # 按时间倒序（最近在前）
        records.sort(
            key=lambda r: r.get("timestamp", ""),
            reverse=True,
        )
        return records[:n]

    def load(self) -> List[Dict[str, Any]]:
        """读取全部有效记录（损坏行隔离）。降级模式下附带内存缓冲。"""
        records: List[Dict[str, Any]] = []
        try:
            if os.path.exists(self._path):
                with open(self._path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            if isinstance(obj, dict):
                                records.append(obj)
                        except Exception:  # noqa: BLE001
                            self._corrupt_count += 1
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"read_failed: {exc}"
        if self._memory_fallback:
            records.extend(self._memory_fallback)
        return records

    # -------------------- 状态 --------------------

    @property
    def path(self) -> str:
        return self._path

    @property
    def degraded(self) -> bool:
        return self._degraded

    @property
    def corrupt_count(self) -> int:
        return self._corrupt_count

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error
