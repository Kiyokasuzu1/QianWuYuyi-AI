"""
Phase A.1: ExperienceLoader

职责：
恢复流程协调层，串联 ExperienceExtractor / RecoveryMarker / ExperienceCache。

recover() 流程（保证永远返回 List[dict]）：

1. 检查 cache
   - cache.exists() 为 True → cache.load() 返回非空 → 直接返回
   - cache 不存在 / 为空 / 损坏 → 走步骤 2

2. Extractor 扫描 memory.json
   - extractor.extract(memory_path) → 内部 List[dict]（含 _source_memory_id）

3. Marker 去重
   - 过滤掉 marker.get_all() 中的 source_memory_id

4. 删除内部字段
   - 移除 _source_memory_id 等内部字段，仅保留 6 公开字段

5. 保存 cache
   - cache.save(public_experiences)

6. 更新 marker
   - marker.mark_recovered([所有新增的 source_memory_id])

7. 返回 public_experiences

约束：
- 不引用 personality_growth_record / growth / growth_state
- 不修改 memory.json（只读）
- 全程 try/except 隔离，单点失败不抛错
- 任何时候 recover() 都返回 List[dict]（可能为空）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Union

from src.recovery.experience_extractor import ExperienceExtractor
from src.recovery.recovery_marker import RecoveryMarker
from src.recovery.experience_cache import ExperienceCache


# 内部字段：进入 SelfModel 前必须删除
_INTERNAL_FIELDS = frozenset({
    "_source_memory_id",
    "source_memory_id",
})


class ExperienceLoader:
    """恢复流程协调层（Cache 优先 / Extractor 兜底）"""

    def __init__(
        self,
        memory_path: Union[str, Path],
        marker: RecoveryMarker,
        cache: ExperienceCache,
        extractor: ExperienceExtractor = None,
    ) -> None:
        self._memory_path = Path(memory_path)
        self._marker = marker
        self._cache = cache
        self._extractor = extractor or ExperienceExtractor()

    # ---------- 公开方法 ----------

    def recover(self) -> List[Dict[str, Any]]:
        """
        启动恢复入口

        优先级：cache → memory.json 重新提取

        Returns:
            List[dict]，每个 dict 仅含 6 个公开字段
        """
        # Step 1: cache 优先
        try:
            if self._cache.exists():
                cached = self._cache.load()
                if cached:
                    return cached
        except Exception:
            # cache 读取失败 → 走提取路径
            pass

        # Step 2: Extractor 扫描
        try:
            candidates = self._extractor.extract(self._memory_path)
        except Exception:
            return []

        if not candidates:
            return []

        # Step 3: Marker 去重
        try:
            recovered_ids = self._marker.get_all()
        except Exception:
            recovered_ids = set()

        new_experiences = [
            e for e in candidates
            if e.get("_source_memory_id") not in recovered_ids
        ]

        if not new_experiences:
            return []

        # Step 4: 删除内部字段
        public = [self._strip_internal_fields(e) for e in new_experiences]

        # Step 5: 保存 cache
        try:
            if public:
                self._cache.save(public)
        except Exception:
            pass  # cache 写失败不阻断主流程

        # Step 6: 更新 marker
        try:
            new_memory_ids = [
                e.get("_source_memory_id")
                for e in new_experiences
                if e.get("_source_memory_id")
            ]
            if new_memory_ids:
                self._marker.mark_recovered(new_memory_ids)
        except Exception:
            pass

        # Step 7: 返回
        return public

    # ---------- 内部方法 ----------

    @staticmethod
    def _strip_internal_fields(item: Dict[str, Any]) -> Dict[str, Any]:
        """
        删除内部字段（_source_memory_id / source_memory_id）

        不修改原对象，返回新 dict
        """
        return {k: v for k, v in item.items() if k not in _INTERNAL_FIELDS}
