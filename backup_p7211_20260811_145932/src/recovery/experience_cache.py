"""
Phase A.1: ExperienceCache

职责：
仅持久化 HistoricalExperience 对外结构。**不**处理 memory_id，**不**引用 Extractor/Marker。

文件结构：
data/recovery/historical_experience_cache.json
[
    {
        "experience_id": "exp_xxx",
        "category": "origin",
        "summary": "...",
        "evidence": "...",
        "timestamp": "...",
        "importance": 0.95
    },
    ...
]

接口：
- exists() -> bool
- load() -> List[dict]   # 失败兜底返回 []
- save(experiences) -> None  # 字段过滤 + 原子写
- clear() -> None  # 测试用

严格字段规则：
- 仅允许 6 个公开字段：experience_id / category / summary / evidence / timestamp / importance
- 禁止保存：_source_memory_id / source_memory_id / confidence / related_dimensions
- 禁止保存：growth_record / trigger_events / changes 等 GrowthRecord 字段
- 保存前过滤非法字段；加载时再次校验字段集合

约束：
- JSON 损坏 / 非 list / 字段非法 → 全部兜底为 []
- 父目录不存在时自动创建
- 原子写：tmp + flush + os.replace（Windows 兼容）
- 不引用 personality_growth_record / growth / growth_state
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Union


DEFAULT_CACHE_PATH = "data/recovery/historical_experience_cache.json"

# 严格公开字段白名单（6 个）
ALLOWED_FIELDS = frozenset({
    "experience_id",
    "category",
    "summary",
    "evidence",
    "timestamp",
    "importance",
})

# 明确禁止的字段（黑名单，仅做断言/日志参考）
FORBIDDEN_FIELDS = frozenset({
    "_source_memory_id",
    "source_memory_id",
    "confidence",
    "related_dimensions",
    "growth_record",
    "trigger_events",
    "changes",
    "affected_dimensions",
})


class ExperienceCache:
    """HistoricalExperience 持久化缓存（与 RecoveryMarker 职责完全分离）"""

    def __init__(self, cache_path: Union[str, Path] = DEFAULT_CACHE_PATH) -> None:
        self._cache_path = Path(cache_path)

    # ---------- 公开方法 ----------

    def exists(self) -> bool:
        """判断 cache 文件是否存在（仅检查文件，不读内容）"""
        return self._cache_path.exists() and self._cache_path.is_file()

    def load(self) -> List[Dict[str, Any]]:
        """
        加载缓存的 HistoricalExperience 列表

        失败兜底：
        - 文件不存在 → []
        - JSON 损坏 → []
        - 非 list → []
        - 任何 list 元素不是 dict 或字段非法 → 该元素被过滤

        Returns:
            List[dict]，每个 dict 字段严格 ∈ ALLOWED_FIELDS
        """
        if not self.exists():
            return []

        try:
            with self._cache_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return []

        if not isinstance(data, list):
            return []

        result: List[Dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            cleaned = self._clean_loaded_item(item)
            if cleaned is not None:
                result.append(cleaned)
        return result

    def save(self, experiences: List[Dict[str, Any]]) -> None:
        """
        保存 experience 列表

        步骤：
        1. 字段过滤：仅保留 ALLOWED_FIELDS 中的字段
        2. 自动创建父目录
        3. 原子写：tmp + flush + os.replace

        Args:
            experiences: 原始 experience 列表（可能含 _source_memory_id 等内部字段）
        """
        cleaned = [self._filter_fields(e) for e in experiences if isinstance(e, dict)]
        self._ensure_parent_dir()
        self._atomic_write(cleaned)

    def clear(self) -> None:
        """删除 cache 文件（测试用）；文件不存在不抛错"""
        try:
            if self._cache_path.exists():
                self._cache_path.unlink()
        except OSError:
            pass

    # ---------- 内部方法：字段过滤 ----------

    def _filter_fields(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """
        过滤字段：仅保留 ALLOWED_FIELDS 中的字段

        不修改原对象（创建新 dict）
        """
        return {k: v for k, v in item.items() if k in ALLOWED_FIELDS}

    def _clean_loaded_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """
        加载时的清理：
        - 仅保留 ALLOWED_FIELDS
        - 类型校验：必填字段缺失或类型错误 → 丢弃该条
        """
        cleaned = self._filter_fields(item)
        # 必填字段校验
        if "experience_id" not in cleaned:
            return None
        if "category" not in cleaned:
            return None
        return cleaned

    # ---------- 内部方法：写入 ----------

    def _ensure_parent_dir(self) -> None:
        """确保父目录存在"""
        parent = self._cache_path.parent
        if parent and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)

    def _atomic_write(self, data: List[Dict[str, Any]]) -> None:
        """
        原子写：先写临时文件（flush），再 os.replace

        Windows 兼容：使用 NamedTemporaryFile 同目录
        """
        self._ensure_parent_dir()
        parent_dir = str(self._cache_path.parent) if self._cache_path.parent else None

        fd, tmp_path = tempfile.mkstemp(
            prefix=".cache_",
            suffix=".json.tmp",
            dir=parent_dir,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except (OSError, AttributeError):
                    # 部分平台 fsync 可能失败，不阻断
                    pass
            os.replace(tmp_path, self._cache_path)
        except Exception:
            # 清理临时文件
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except OSError:
                pass
            raise
