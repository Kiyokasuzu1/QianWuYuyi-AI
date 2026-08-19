# -*- coding: utf-8 -*-
"""
src/runtime/adapters/impl/personality_adapter_impl.py

Phase 3.7.2: PersonalityAdapterImpl —— Personality 模块 Adapter 实现

职责：
- 桥接 Runtime Adapter 接口（PersonalityAdapter）到 PersonalityResolver
- 内部依赖：src.personality.personality_resolver.PersonalityResolver
- 不修改 PersonalityResolver

Runtime → PersonalityAdapter → PersonalityAdapterImpl → PersonalityResolver

约束：
- snapshot() 调 PersonalityResolver.resolve() 出 PersonalityVector
- apply_update(proposal) 不直接修改人格,只走 PersonalityResolver.resolve() 重算
  并缓存 growth_records / change_items
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.runtime.adapters.base import AdapterBase
from src.runtime.adapters.personality_adapter import PersonalityAdapter

logger = logging.getLogger(__name__)


class PersonalityAdapterImpl(PersonalityAdapter):
    """Personality 模块 Adapter 实现（Phase 3.7.2 / v1.0）

    桥接 Runtime 抽象接口到现有 PersonalityResolver。
    """

    name: str = "personality_adapter_impl"
    schema_version: str = "1.0"

    def __init__(self, personality_resolver: Optional[Any] = None) -> None:
        super().__init__()
        self._resolver: Optional[Any] = personality_resolver
        self._last_snapshot: Optional[Any] = None
        self._applied_change_count: int = 0

    # --------------------------------------------------------
    # AdapterBase 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        """注入 PersonalityResolver（默认自建）。"""
        if self._resolver is None:
            try:
                from src.personality.personality_resolver import PersonalityResolver
                self._resolver = PersonalityResolver()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "PersonalityAdapterImpl.attach() 自建 PersonalityResolver 失败: %s",
                    exc,
                )
                self._resolver = None
        self._mark_attached()
        self._cache_health({
            "healthy": self._resolver is not None,
            "name": self.name,
            "schema_version": self.schema_version,
        })

    def detach(self) -> None:
        """解除接入（不关闭业务实例,只清空引用）。"""
        self._resolver = None
        self._last_snapshot = None
        self._mark_detached()

    def health_check(self) -> Dict[str, Any]:
        healthy = self.is_attached and self._resolver is not None
        result = {
            "healthy": healthy,
            "name": self.name,
            "schema_version": self.schema_version,
        }
        self._cache_health(result)
        return result

    # --------------------------------------------------------
    # Personality 业务接口实现
    # --------------------------------------------------------
    def snapshot(self) -> Any:
        """获取当前人格快照（PersonalityVector.to_dict 形态）。"""
        if not self.is_attached or self._resolver is None:
            return None

        try:
            vector = self._resolver.resolve()
            data: Optional[Dict[str, Any]] = None
            if vector is not None and hasattr(vector, "get_all"):
                data = vector.get_all()
            self._last_snapshot = data
            return data
        except Exception as exc:  # noqa: BLE001
            logger.warning("PersonalityAdapterImpl.snapshot() 失败: %s", exc)
            return None

    def apply_update(self, proposal: Any) -> Any:
        """应用一个成长提案（只缓存与重算,不改人格源数据）。

        流程：
        1) 解析 proposal 拿到 proposed_changes
        2) 把每个 change 追加到 resolver.growth_records（缓存层）
        3) 调 resolver.resolve() 重算 PersonalityVector
        """
        if not self.is_attached or self._resolver is None:
            return {"applied": False, "reason": "not_attached"}

        try:
            from src.contracts.growth_schema import (
                GrowthProposal,
                CANONICAL_SCHEMA_VERSION,
            )

            # 解析 proposal
            if isinstance(proposal, GrowthProposal):
                if proposal.schema_version != CANONICAL_SCHEMA_VERSION:
                    return {
                        "applied": False,
                        "reason": (
                            f"schema_version {proposal.schema_version!r} "
                            f"!= canonical {CANONICAL_SCHEMA_VERSION!r}"
                        ),
                    }
                changes = list(proposal.proposed_changes or [])
                evidence_ids = list(proposal.evidence_ids or [])
                source_event_id = proposal.source_event_id
            elif isinstance(proposal, dict):
                if proposal.get("schema_version") != CANONICAL_SCHEMA_VERSION:
                    return {
                        "applied": False,
                        "reason": (
                            f"schema_version {proposal.get('schema_version')!r} "
                            f"!= canonical {CANONICAL_SCHEMA_VERSION!r}"
                        ),
                    }
                raw_changes = proposal.get("proposed_changes", []) or []
                changes = []
                for c in raw_changes:
                    if isinstance(c, dict):
                        changes.append({
                            "path": c.get("path"),
                            "before": c.get("before"),
                            "after": c.get("after"),
                            "reason": c.get("reason"),
                        })
                evidence_ids = list(proposal.get("evidence_ids", []) or [])
                source_event_id = proposal.get("source_event_id")
            else:
                return {
                    "applied": False,
                    "reason": f"unsupported proposal type: {type(proposal)!r}",
                }

            # 缓存到 resolver.growth_records（PersonalityResolver 已在 resolve 阶段消费）
            record = {
                "source_event_id": source_event_id,
                "evidence_ids": evidence_ids,
                "changes": [
                    {
                        "path": getattr(c, "path", None) if not isinstance(c, dict) else c.get("path"),
                        "before": getattr(c, "before", None) if not isinstance(c, dict) else c.get("before"),
                        "after": getattr(c, "after", None) if not isinstance(c, dict) else c.get("after"),
                        "reason": getattr(c, "reason", None) if not isinstance(c, dict) else c.get("reason"),
                    }
                    for c in changes
                ],
            }
            try:
                self._resolver.growth_records.append(record)
            except Exception:
                # resolver 可能不允许写入,降级为不追加
                pass

            # 重新解析
            vector = self._resolver.resolve()
            snapshot = vector.get_all() if vector is not None and hasattr(vector, "get_all") else None
            self._last_snapshot = snapshot
            self._applied_change_count += len(changes)

            return {
                "applied": True,
                "change_count": len(changes),
                "snapshot": snapshot,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("PersonalityAdapterImpl.apply_update() 失败: %s", exc)
            return {"applied": False, "reason": str(exc)}

    # --------------------------------------------------------
    # 状态查询
    # --------------------------------------------------------
    @property
    def last_snapshot(self) -> Optional[Any]:
        return self._last_snapshot

    @property
    def applied_change_count(self) -> int:
        return self._applied_change_count
