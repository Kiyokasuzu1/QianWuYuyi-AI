# -*- coding: utf-8 -*-
"""
P2.3-B.10 Phase 5 — GovernanceInspector（治理落账只读查询器）
P2.3-B.11 Phase 4 — 只读统计增强

定位：
    对 GovernanceProposalStore 的只读封装，提供统一治理视图的查询入口
    （B.10 债务 #5：legacy mutation 来源已登记但未形成统一治理视图）。
    NEED_REVIEW 消费链（B.11 ProposalManager）的管理/观测只通过本
    Inspector 读取落账与迁移事件。

只读硬边界（B.10 任务书 + B.11 Phase 4 冻结）：
    - 只暴露查询/统计方法；不暴露任何写方法
    - 不修改落账文件、迁移事件文件、不修改任何域状态
    - 底层 store / manager 引用私有（_store / _manager），
      防止调用方拿到写句柄
    - 构造与查询均不创建文件（store 惰性创建文件，读取不落盘）

B.11 Phase 4 新增只读能力（任务书冻结五项）：
    - count_pending()          当前 pending 数量
    - count_by_domain()        各 domain mutation 数量
    - reject_reason_stats()    reject 原因统计（来自迁移事件流）
    - review_times()           review 时间（待审停留 / 已审耗时）
    - audit_completeness()     audit 完整性（链路三键齐全校验）
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.governance.proposal_store import GovernanceProposalStore


def _parse_iso(timestamp: str) -> Optional[datetime]:
    """容错解析 ISO 时间串（带 Z 后缀）；失败返回 None。"""
    if not isinstance(timestamp, str) or not timestamp:
        return None
    text = timestamp.strip()
    if text.endswith("Z"):
        text = text[:-1]
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


class GovernanceInspector:
    """治理提案落账的只读查询器（统一治理视图入口）。"""

    def __init__(
        self,
        store: Optional[GovernanceProposalStore] = None,
        data_dir: Optional[Any] = None,
        *,
        manager: Optional[Any] = None,
    ) -> None:
        if store is not None:
            self._store = store
        elif data_dir is not None:
            self._store = GovernanceProposalStore(Path(data_dir))
        else:
            self._store = GovernanceProposalStore()
        # B.11 Phase 4：可选生命周期视图（GovernanceProposalManager 协作件，
        # 只使用其只读方法 audit_trace/_read_events——以鸭子类型访问，
        # 避免本模块反向依赖 proposal_manager 造成的循环 import）
        self._manager = manager

    # ============================================================
    # 只读查询接口（B.10 Phase 5 冻结，三个方法，不变）
    # ============================================================
    def list_pending(self) -> List[Dict[str, Any]]:
        """全部 NEED_REVIEW 待消费提案记录（按落账顺序）。"""
        return self._store.list_pending()

    def get_by_domain(self, domain: str) -> List[Dict[str, Any]]:
        """按域（memory/emotion/relationship/growth/personality/self_model）过滤。"""
        return self._store.get_by_domain(domain)

    def get_by_mutation_id(self, mutation_id: str) -> List[Dict[str, Any]]:
        """按 mutation_id 精确查找（幂等落账下最多一条）。"""
        return self._store.get_by_mutation_id(mutation_id)

    # ============================================================
    # 只读统计（B.11 Phase 4 新增，任务书冻结五项）
    # ============================================================
    def count_pending(self) -> int:
        """当前 pending 数量（PENDING_REVIEW 生命周期态；
        未注入 manager 时退化为 NEED_REVIEW 落账计数）。"""
        if self._manager is not None:
            return len(self._manager.list_by_status("PENDING_REVIEW"))
        return len(self.list_pending())

    def count_by_domain(self) -> Dict[str, int]:
        """各 domain mutation 数量（全量落账，按 domain 分组）。"""
        counts: Dict[str, int] = {}
        for record in self._store.load_records():
            domain = str(record.get("domain") or "unknown")
            counts[domain] = counts.get(domain, 0) + 1
        return counts

    def _lifecycle_events(self) -> List[Dict[str, Any]]:
        if self._manager is None:
            return []
        read_events = getattr(self._manager, "_read_events", None)
        if not callable(read_events):
            return []
        return list(read_events())

    def reject_reason_stats(self) -> Dict[str, int]:
        """reject 原因统计（迁移事件流中 reject 事件的 reason 计数）。

        未注入 manager 或无 reject 事件 → 空字典。
        """
        stats: Dict[str, int] = {}
        for event in self._lifecycle_events():
            if str(event.get("event")) == "reject":
                reason = str(event.get("reason") or "unspecified")
                stats[reason] = stats.get(reason, 0) + 1
        return stats

    def review_times(self) -> List[Dict[str, Any]]:
        """review 时间（秒）。

        每条已落账 proposal 一项：
        - reviewed=False（仍待审）：created_at → 现在的停留秒数
        - reviewed=True（已审结）：created_at → 审结事件 timestamp 的耗时秒数
        解析失败的字段时间不计入（该项跳过）。
        """
        review_events: Dict[str, Dict[str, Any]] = {}
        for event in self._lifecycle_events():
            name = str(event.get("event"))
            if name in ("approve", "reject"):
                review_events[str(event.get("proposal_id"))] = event

        results: List[Dict[str, Any]] = []
        for record in self._store.load_records():
            proposal_id = str(record.get("proposal_id") or "")
            if not proposal_id:
                continue
            created = _parse_iso(str(record.get("created_at") or ""))
            if created is None:
                continue
            event = review_events.get(proposal_id)
            if event is not None:
                reviewed_at = _parse_iso(str(event.get("timestamp") or ""))
                if reviewed_at is None:
                    continue
                results.append({
                    "proposal_id": proposal_id,
                    "reviewed": True,
                    "review_seconds": max(0.0, (reviewed_at - created).total_seconds()),
                    "reviewer": str(event.get("reviewer") or ""),
                })
            else:
                now = datetime.utcnow()
                results.append({
                    "proposal_id": proposal_id,
                    "reviewed": False,
                    "waiting_seconds": max(0.0, (now - created).total_seconds()),
                })
        return results

    def audit_completeness(self) -> Dict[str, Any]:
        """audit 完整性：链路三键（mutation_id/request_id/trace_id）齐全校验。

        校验对象：全部落账记录 + 全部迁移事件的 trace_ref。
        返回：{total, complete, incomplete_count, incomplete_ids}
        """
        records = self._store.load_records()
        incomplete_ids: List[str] = []
        complete = 0
        for record in records:
            proposal_id = str(record.get("proposal_id") or "")
            keys_ok = all(
                str(record.get(key) or "").strip()
                for key in ("mutation_id", "request_id", "trace_id")
            )
            if keys_ok:
                complete += 1
            elif proposal_id:
                incomplete_ids.append(proposal_id)

        events = self._lifecycle_events()
        event_total = len(events)
        event_complete = 0
        for event in events:
            ref = event.get("trace_ref") or {}
            if isinstance(ref, dict) and all(
                str(ref.get(key) or "").strip()
                for key in ("mutation_id", "request_id", "trace_id")
            ):
                event_complete += 1

        return {
            "records_total": len(records),
            "records_complete": complete,
            "records_incomplete": len(incomplete_ids),
            "incomplete_proposal_ids": incomplete_ids,
            "lifecycle_events_total": event_total,
            "lifecycle_events_complete": event_complete,
        }


__all__ = ["GovernanceInspector"]
