"""
Phase 4.0 — R2.5.2-B: RelationshipMemory（关系事件库 / Store）

定位（严格与 Growth 链路平行）：

    MemoryCreatedEvent
        ↓
    ExperienceBridge（分类 relationship_memory）
        ↓
    RelationshipEventStore.append(record-derived event)   本模块实现
        ↓
    （R2.5.2-C / D 之后：RelationshipIntelligenceEngine）
        ↓
    RelationshipProposal（未来）
        ↓
    Approval（未来）
        ↓
    RelationshipState（未来才可能碰，现在绝对不）

红线 R2.5.2-B 冻结不得违反：
  1. ❌ 绝不 write bond / trust / familiarity / promise / shared_history
  2. ❌ 绝不做 content 关键词触发（Bridge 已经先判定结构化 signal 才会到达本模块）
  3. ❌ 绝不把事件直接塞进 prompt（future context provider 再消费）
  4. ✅ 本模块是 append-only 事件库；查询方法返回 list[RelationshipEvent]，不做数值推断
"""
from __future__ import annotations

import logging
from threading import Lock
from typing import Any, Dict, Iterable, List, Optional

from src.relationship.relationship_event import (
    RELATIONSHIP_EVENT_ALLOWED_STATUS,
    RELATIONSHIP_EVENT_FROZEN_KEYS,
    RELATIONSHIP_EVENT_REGISTERED_KEYS,
    RelationshipEvent,
    build_relationship_event_from_record,
)

logger = logging.getLogger(__name__)

# 5 维关系值（红线 1：本模块绝对不写）
_RED_LINE_DIMENSIONS = ("bond", "trust", "familiarity", "promise", "shared_history")


class RelationshipEventStore:
    """append-only 关系事件库（进程内单例）。

    对外 API：
      append_record(record)       → RelationshipEvent：从 memory record 构建并追加
      append_event(event)         → RelationshipEvent：追加已构建好的事件（校验 shape）
      list(user_id=?, type=?, status="observed") → List[RelationshipEvent]
      get(source_memory_id=?)     → RelationshipEvent | None：反向溯源
      count(user_id=?)            → int
      snapshot()                  → dict：仅 debug 导出，不写 prompt
    """

    _default_store: Optional["RelationshipEventStore"] = None
    _default_lock: Lock = Lock()

    def __init__(self) -> None:
        self._lock = Lock()
        self._events: List[RelationshipEvent] = []
        # 加速索引
        self._by_source_memory_id: Dict[str, List[RelationshipEvent]] = {}
        self._by_user_id: Dict[str, List[RelationshipEvent]] = {}
        # 红线 1：防御性 — 明确标记未写入
        self._red_line_guard: Dict[str, bool] = {d: False for d in _RED_LINE_DIMENSIONS}

    # ============================================================
    # 单例便捷（Bridge / Tests 调用）
    # ============================================================
    @classmethod
    def default(cls) -> "RelationshipEventStore":
        if cls._default_store is None:
            with cls._default_lock:
                if cls._default_store is None:
                    cls._default_store = RelationshipEventStore()
        return cls._default_store

    @classmethod
    def reset_default_for_tests(cls) -> None:
        with cls._default_lock:
            cls._default_store = None

    # ============================================================
    # 追加（对外主入口）
    # ============================================================
    def append_record(self, record: Dict[str, Any]) -> RelationshipEvent:
        """Bridge 分类 relationship_memory 后调用：构建 + 追加。

        Returns:
            已追加的事件；若构建失败则抛 ValueError（Bridge 侧 try/except 隔离）
        """
        try:
            ev = build_relationship_event_from_record(record)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[RelationshipEventStore] build event 失败: %s", exc)
            raise
        return self.append_event(ev)

    def append_event(self, event: RelationshipEvent) -> RelationshipEvent:
        """追加一个已经构建好的事件（做 shape 校验 + 红线 1/2 断言式保护）。"""
        # 红线校验
        self._enforce_shape(event)
        self._enforce_red_lines(event)

        with self._lock:
            self._events.append(event)
            sid = str(event.get("source_memory_id") or "")
            uid = str(event.get("user_id") or "")
            if sid:
                self._by_source_memory_id.setdefault(sid, []).append(event)
            if uid:
                self._by_user_id.setdefault(uid, []).append(event)
        # 审计：失败不影响主流程（防御）
        try:
            self._emit_audit(event)
        except Exception as exc:  # noqa: BLE001
            try:
                logger.warning("[RelationshipEventStore] audit 失败(已隔离): %s", exc)
            except Exception:  # noqa: BLE001
                pass
        return event

    # ============================================================
    # 查询
    # ============================================================
    def list_events(
        self,
        *,
        user_id: Optional[str] = None,
        event_type: Optional[str] = None,
        status: Optional[str] = "observed",
        limit: Optional[int] = None,
    ) -> List[RelationshipEvent]:
        with self._lock:
            pool: Iterable[RelationshipEvent] = self._events
            if user_id:
                pool = self._by_user_id.get(str(user_id), [])
            out: List[RelationshipEvent] = []
            for e in pool:
                if status is not None and e.get("status") != status:
                    continue
                if event_type is not None and e.get("type") != event_type:
                    continue
                out.append(e)
            if limit is not None and limit > 0:
                out = out[-int(limit):]
            return list(out)

    def get_by_source_memory_id(self, memory_id: str) -> List[RelationshipEvent]:
        if not memory_id:
            return []
        with self._lock:
            return list(self._by_source_memory_id.get(str(memory_id), []))

    def count_events(self, *, user_id: Optional[str] = None, status: Optional[str] = "observed") -> int:
        return len(self.list_events(user_id=user_id, status=status, limit=None))

    def snapshot(self) -> Dict[str, Any]:
        """导出只读调试快照。红线 1 保护：结果里永远不含 5 维关系数值。"""
        with self._lock:
            return {
                "total_events": len(self._events),
                "by_user": {
                    uid: len(events) for uid, events in self._by_user_id.items()
                },
                "by_source_memory_count": len(self._by_source_memory_id),
                "red_line_guard": dict(self._red_line_guard),  # 全 False，永远为 False
                "last_10_events": [dict(e) for e in self._events[-10:]],
            }

    # ============================================================
    # 红线 & shape 校验（Gate 测试重点覆盖）
    # ============================================================
    def _enforce_shape(self, event: RelationshipEvent) -> None:
        if not isinstance(event, dict):
            raise ValueError("RelationshipEvent 必须是 dict")
        missing = [k for k in RELATIONSHIP_EVENT_FROZEN_KEYS if k not in event]
        if missing:
            raise ValueError(f"RelationshipEvent 缺少冻结字段: {missing}")
        # Phase 4.2-A：extra 判定改用统一 schema 的已登记字段集
        # （bridge 事件 11 键 / 提取事件 11+2 键均可入库，其余仍拒绝）
        extra = [k for k in event.keys() if k not in RELATIONSHIP_EVENT_REGISTERED_KEYS]
        if extra:
            raise ValueError(f"RelationshipEvent 包含未登记字段: {extra}")
        if event.get("status") not in RELATIONSHIP_EVENT_ALLOWED_STATUS:
            raise ValueError(
                f"RelationshipEvent.status 非法: {event.get('status')!r}，"
                f"R2.5.2-B 只允许 {RELATIONSHIP_EVENT_ALLOWED_STATUS!r}"
            )

    def _enforce_red_lines(self, event: RelationshipEvent) -> None:
        # 红线 1：事件 dict 本身绝不带 bond/trust/... 这些 key（shape 里没定义过但这里再加一层）
        for k in _RED_LINE_DIMENSIONS:
            if k in event:
                raise ValueError(f"红线1：事件不允许携带关系维度值：{k}")
        # 红线 2：即使 metadata.meaning 没写，这里已经 append 了 — Bridge 必须先保证。
        #         在 Store 里加一层二次校验：如果 content 里是"喜欢拉面"这种常见日常词，但
        #         type 被推断成 declaration/promise — 拒绝入库（不过我们 type 是 meaning/memory_type 字面量匹配，
        #         不会触发；这里只是防御性）。
        #         注：不检查 content，符合红线要求 — "Store 不做关键词判断"
        # 红线 3：Store 不 export prompt（由 list_events 返回纯事件，外部自行决定用途）
        pass

    # ============================================================
    # 审计
    # ============================================================
    @staticmethod
    def _emit_audit(event: RelationshipEvent) -> None:
        from src.audit.record import record_audit_log

        record_audit_log(
            operation_type="relationship_memory_append",
            source="relationship_event_store",
            action="append",
            user_id=str(event.get("user_id") or ""),
            detail={
                "id": event.get("id"),
                "type": event.get("type"),
                "status": event.get("status"),
                "source_memory_id": event.get("source_memory_id"),
                "meaning": event.get("meaning"),
                "memory_type": event.get("memory_type"),
            },
            result="success",
        )
