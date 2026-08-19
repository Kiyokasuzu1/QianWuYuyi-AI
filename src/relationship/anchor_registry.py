# -*- coding: utf-8 -*-
"""Phase 2.5-C 阶段2:AnchorRegistry —— 关系核心锚点的持久化来源。

解决 2.5-B 的遗留缺口:CORE_RELATIONSHIP_MEMORY_IDS 只是内存 set(),
进程重启即丢失。本模块建立读取链:

    data/relationship_core/relationship_core.jsonl
        → RelationshipCoreStore.load()
        → AnchorRegistry.load_anchor_ids()          (各核心 anchor_memory_ids 并集)
        → seed_into(CORE_RELATIONSHIP_MEMORY_IDS)   (运行时启动时播种)

安全约束:
- 只读:registry 绝不写 store,也绝不修改 memory 记录内容;
- 失败自动降级空集合(行为 = 2.5-B 现状,锚点不生效但不报错);
- 锚点进入白名单的唯一正当来源是「已审核的关系核心」,本模块不做任何判定。
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Optional, Set

logger = logging.getLogger(__name__)


class AnchorRegistry:
    """从 RelationshipCoreStore 读取锚点并播种到内存白名单。

    纯读取器 + 播种器,不缓存跨 store 状态(每次 load 都走 store,
    保证重启/多实例一致)。
    """

    def __init__(self, store: Any = None):
        self._store = store

    def load_anchor_ids(self) -> Set[str]:
        """读取全部关系核心的 anchor_memory_ids 并集。

        任何失败(store 缺失 / 损坏 / 字段非法)都返回空集合,
        绝不抛异常——与 2.5-B「白名单为空」的降级行为一致。
        """
        anchors: Set[str] = set()
        try:
            store = self._store
            if store is None:
                return anchors
            records = store.load() if hasattr(store, "load") else store.list_all()
            for record in records or []:
                if not isinstance(record, dict):
                    continue
                ids = record.get("anchor_memory_ids")
                if not isinstance(ids, (list, tuple)):
                    continue
                for mid in ids:
                    if mid is not None and str(mid):
                        anchors.add(str(mid))
        except Exception as exc:  # noqa: BLE001
            logger.debug("AnchorRegistry.load_anchor_ids 失败(已降级空集合): %s", exc)
            return set()
        return anchors

    def seed_into(self, target: Set[str]) -> int:
        """把锚点 id 播种进内存白名单,返回新增数量。"""
        try:
            ids = self.load_anchor_ids()
            if not isinstance(target, set) or target is None:
                target = set()
            new_ids = ids - target
            for mid in new_ids:
                target.add(mid)
            return len(new_ids)
        except Exception as exc:  # noqa: BLE001
            logger.debug("AnchorRegistry.seed_into 失败(已隔离): %s", exc)
            return 0

    def seed_all(self, target: Set[str]) -> int:
        """兼容别名:与 seed_into 行为一致(供其他调用方使用)。"""
        return self.seed_into(target)


__all__ = ["AnchorRegistry"]
