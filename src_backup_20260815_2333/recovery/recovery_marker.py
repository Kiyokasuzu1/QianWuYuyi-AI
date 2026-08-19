"""
Phase A.1: RecoveryMarker

职责：
仅负责 memory_id 去重持久化。**不**保存 experience 数据。

文件结构：
data/recovery/experience_recovery_marker.json
{
    "recovered_memory_ids": ["mem_xxx", "mem_yyy"]
}

接口：
- is_recovered(memory_id) -> bool
- mark_recovered(memory_ids: List[str]) -> None
- get_all() -> Set[str]
- count() -> int

约束：
- JSON 损坏自动恢复为空集合（不抛错）
- 父目录不存在时自动创建
- 原子写：tmp + os.replace
- 不引用 personality_growth_record / growth / growth_state
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Iterable, List, Set, Union


DEFAULT_MARKER_PATH = "data/recovery/experience_recovery_marker.json"


class RecoveryMarker:
    """memory_id 去重持久化器"""

    def __init__(self, marker_path: Union[str, Path] = DEFAULT_MARKER_PATH) -> None:
        self._marker_path = Path(marker_path)

    # ---------- 公开方法 ----------

    def is_recovered(self, memory_id: str) -> bool:
        """检查 memory_id 是否已恢复"""
        if not memory_id:
            return False
        return memory_id in self.get_all()

    def mark_recovered(self, memory_ids: Iterable[str]) -> None:
        """
        标记一组 memory_id 为已恢复（增量追加 + 原子写）

        Args:
            memory_ids: 待追加的 memory_id 列表/集合
        """
        new_ids = {mid for mid in memory_ids if mid}
        if not new_ids:
            return

        current = self.get_all()
        merged = current | new_ids
        if merged == current:
            return  # 无新增，跳过写盘

        self._ensure_parent_dir()
        self._atomic_write({
            "recovered_memory_ids": sorted(merged)
        })

    def get_all(self) -> Set[str]:
        """
        加载已恢复的 memory_id 集合

        失败兜底：
        - 文件不存在 → 空集合
        - JSON 损坏 → 空集合
        - 数据结构非法 → 空集合
        """
        if not self._marker_path.exists():
            return set()

        try:
            with self._marker_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return set()

        if not isinstance(data, dict):
            return set()

        ids = data.get("recovered_memory_ids", [])
        if not isinstance(ids, list):
            return set()

        return {str(mid) for mid in ids if mid}

    def count(self) -> int:
        """已恢复 memory_id 数量"""
        return len(self.get_all())

    # ---------- 内部方法 ----------

    def _ensure_parent_dir(self) -> None:
        """确保父目录存在"""
        parent = self._marker_path.parent
        if parent and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)

    def _atomic_write(self, data: dict) -> None:
        """
        原子写：先写临时文件，再 os.replace

        任何异常都向上抛（调用方决定如何处理）
        """
        self._ensure_parent_dir()
        # 使用 NamedTemporaryFile 在同目录下写 tmp，再 rename
        # Windows 下 NamedTemporaryFile 默认不能被其他进程打开，
        # 故用 delete=False + 手动 unlink
        fd, tmp_path = tempfile.mkstemp(
            prefix=".marker_",
            suffix=".json.tmp",
            dir=str(self._marker_path.parent) if self._marker_path.parent else None,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._marker_path)
        except Exception:
            # 清理临时文件
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except OSError:
                pass
            raise
