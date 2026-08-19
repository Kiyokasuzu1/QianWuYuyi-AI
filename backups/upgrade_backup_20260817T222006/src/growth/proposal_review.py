# -*- coding: utf-8 -*-
"""
src/growth/proposal_review.py

Phase C.4.3 / C.4.6.3 — Proposal Review System (人工 Review 工具)

职责:
- 提供 proposal 的人工 review 工具
- 不修改任何核心模块
- 不自动 accept(严格保持 auto_accept_enabled=False)
- 任何 apply 都需要显式 approve

提供:
- list_pending(): 列出所有 pending/proposed 的 proposal
- get(proposal_id): 查看详情(evidence, confidence, change_items)
- approve(proposal_id, reviewer, comment): 标记为 approved
- reject(proposal_id, reviewer, comment): 标记为 rejected
- archive(proposal_id, reviewer, comment): 标记为 archived

约束:
- 不调用 LLM
- 不修改 CoreIdentity / Personality / Memory
- 仅修改 proposal 自身的 review 字段
- 保持向后兼容(支持 v1 + v2 proposal schema)
- 写操作原子(读 → 修改 → 写,带锁)
- 状态机: pending → approved / rejected / archived
- 禁止 auto_accept_enabled=True
- 禁止脚本绕过 review
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# proposal 状态(冻结 4 状态规范)
# 状态机(单向,不可自动回退):
#   pending → approved
#          ↘ rejected
#          ↘ archived
PENDING_STATUS = "pending"
APPROVED_STATUS = "approved"
REJECTED_STATUS = "rejected"
ARCHIVED_STATUS = "archived"

# 合法的 status 集合(用于校验)
VALID_STATUSES = {PENDING_STATUS, APPROVED_STATUS, REJECTED_STATUS, ARCHIVED_STATUS}

# PENDING 集合(包含 legacy "proposed" 别名)
PENDING_STATUSES = {PENDING_STATUS, "proposed"}
REVIEWABLE_STATUSES = VALID_STATUSES | {"proposed"}

# 全局开关: auto_accept 必须为 False(否则违规)
AUTO_ACCEPT_ENABLED = False


# ============================================================
# 安全保护
# ============================================================
def assert_auto_accept_disabled() -> None:
    """断言 auto_accept_enabled 必须为 False。

    若代码或配置中将此值改为 True,所有 review 操作会拒绝执行。
    """
    if AUTO_ACCEPT_ENABLED:
        raise RuntimeError(
            "auto_accept_enabled=True 被禁止。"
            "Proposal review 流程要求显式人工 review。"
        )


def assert_human_reviewer(reviewer: str) -> None:
    """断言 reviewer 是非空的人类 ID。

    禁止脚本 / 自动 agent 充当 reviewer。
    """
    if not reviewer or not isinstance(reviewer, str):
        raise ValueError("reviewer 必须是非空字符串(人类 ID)")
    if reviewer.lower() in {"script", "auto", "agent", "bot", "system", "llm"}:
        raise ValueError(
            f"reviewer='{reviewer}' 被禁止:必须由人类操作员执行 review。"
        )


class ProposalReviewer:
    """
    人工 review 工具。

    Args:
        proposals_path: proposals.json 或 proposals.jsonl 路径
    """

    def __init__(self, proposals_path: str | os.PathLike):
        self.path = Path(proposals_path)
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {"version": "1.0", "proposals": []}
        self._loaded = False
        self._load()

    # ============================================================
    # 加载 / 保存
    # ============================================================
    def _load(self) -> None:
        """加载 proposals(支持 json / jsonl 两种格式)。"""
        if not self.path.exists():
            self._loaded = True
            return

        with self._lock:
            try:
                if self.path.suffix == ".jsonl":
                    items: List[Dict[str, Any]] = []
                    with open(self.path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                items.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
                    self._data = {"version": "1.0", "proposals": items}
                else:
                    with open(self.path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict) and "proposals" in data:
                        self._data = data
                    elif isinstance(data, list):
                        self._data = {"version": "1.0", "proposals": data}
                    else:
                        self._data = {"version": "1.0", "proposals": []}
            except Exception:
                # 加载失败保持空
                self._data = {"version": "1.0", "proposals": []}
            self._loaded = True

    def _save(self) -> bool:
        """原子写入 proposals.json。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            os.replace(tmp_path, self.path)
            return True
        except Exception:
            return False

    # ============================================================
    # 字段兼容(v1 + v2 schema)
    # ============================================================
    @staticmethod
    def _get_id(p: Dict[str, Any]) -> str:
        return str(p.get("id") or p.get("proposal_id") or "")

    @staticmethod
    def _get_status(p: Dict[str, Any]) -> str:
        return str(p.get("status", "unknown"))

    @staticmethod
    def _get_confidence(p: Dict[str, Any]) -> float:
        try:
            return float(p.get("confidence", 0.0))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _get_evidence(p: Dict[str, Any]) -> List[str]:
        ev = p.get("evidence") or p.get("evidence_ids") or []
        if isinstance(ev, list):
            return [str(x) for x in ev]
        return []

    @staticmethod
    def _get_change_items(p: Dict[str, Any]) -> List[Dict[str, Any]]:
        # v2: proposed_changes (List[ChangeItem])
        changes = p.get("proposed_changes") or []
        if isinstance(changes, list) and changes:
            return changes
        # v1: before_state/after_state
        before = p.get("before_state") or {}
        after = p.get("after_state") or {}
        affected = p.get("affected_dimensions") or {}
        if not (before or after):
            return []
        items = []
        for dim in set(list(before.keys()) + list(after.keys()) + list(affected.keys())):
            items.append({
                "path": dim,
                "before": before.get(dim),
                "after": after.get(dim),
                "delta": affected.get(dim),
            })
        return items

    # ============================================================
    # 查询
    # ============================================================
    def list_all(self) -> List[Dict[str, Any]]:
        """列出所有 proposal。"""
        with self._lock:
            return list(self._data.get("proposals", []))

    def list_pending(self) -> List[Dict[str, Any]]:
        """列出所有 pending/proposed 的 proposal(待审核)。"""
        with self._lock:
            return [
                p for p in self._data.get("proposals", [])
                if self._get_status(p) in PENDING_STATUSES
            ]

    def list_by_status(self, status: str) -> List[Dict[str, Any]]:
        """按状态过滤。"""
        with self._lock:
            return [
                p for p in self._data.get("proposals", [])
                if self._get_status(p) == status
            ]

    def get(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        """按 ID 查找 proposal。"""
        with self._lock:
            for p in self._data.get("proposals", []):
                if self._get_id(p) == proposal_id:
                    return p
        return None

    def get_detail(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        """返回 proposal 详情,包含 derived 字段(便于 review)。"""
        p = self.get(proposal_id)
        if p is None:
            return None
        return {
            "id": self._get_id(p),
            "status": self._get_status(p),
            "confidence": self._get_confidence(p),
            "proposal_type": p.get("proposal_type") or p.get("type", "unknown"),
            "source_event_id": p.get("source_event_id", ""),
            "user_id": p.get("user_id", ""),
            "timestamp": p.get("timestamp", ""),
            "evidence": self._get_evidence(p),
            "change_items": self._get_change_items(p),
            "reason": p.get("reason", ""),
            "reviewer_id": p.get("reviewer_id", ""),
            "review_comment": p.get("review_comment", ""),
            "reviewed_at": p.get("reviewed_at"),
        }

    def count_by_status(self) -> Dict[str, int]:
        """按状态统计。"""
        with self._lock:
            result: Dict[str, int] = {}
            for p in self._data.get("proposals", []):
                s = self._get_status(p)
                result[s] = result.get(s, 0) + 1
            return result

    # ============================================================
    # Review 操作(必须显式调用,不自动)
    # ============================================================
    def _update_proposal(
        self,
        proposal_id: str,
        new_status: str,
        reviewer: str,
        comment: str = "",
    ) -> Dict[str, Any]:
        """更新 proposal 状态 + 写 review 字段。

        记录字段:
        - status: 新状态
        - decision: 操作决定(approve / reject / archive)
        - reviewer_id: 人类 reviewer ID
        - review_comment: 评论
        - reviewed_at: ISO 时间戳
        - review_history: 历史 review 记录(追加)

        安全保护:
        - auto_accept_enabled 必须为 False
        - reviewer 必须为非空人类 ID
        - 已 review 过的 proposal 默认不可再次 review(防止误操作)
        """
        # 安全检查 1: auto_accept 必须关闭
        try:
            assert_auto_accept_disabled()
        except RuntimeError as e:
            return {
                "success": False,
                "proposal_id": proposal_id,
                "error": "auto_accept_forbidden",
                "detail": str(e),
            }

        # 安全检查 2: reviewer 必须合法
        try:
            assert_human_reviewer(reviewer)
        except ValueError as e:
            return {
                "success": False,
                "proposal_id": proposal_id,
                "error": "invalid_reviewer",
                "detail": str(e),
            }

        with self._lock:
            for p in self._data.get("proposals", []):
                if self._get_id(p) == proposal_id:
                    # 安全检查 3: 已 review 过的 proposal 不可再次 review
                    current_status = self._get_status(p)
                    if current_status in (APPROVED_STATUS, REJECTED_STATUS, ARCHIVED_STATUS):
                        # 防止重复 review(必须先 reset)
                        return {
                            "success": False,
                            "proposal_id": proposal_id,
                            "error": "already_reviewed",
                            "detail": (
                                f"proposal 已处于 '{current_status}' 状态,"
                                f"如需重新 review 请先 reset 到 pending"
                            ),
                            "current_status": current_status,
                        }

                    # 写新状态
                    p["status"] = new_status
                    # 新字段:decision(独立于 status,语义明确)
                    p["decision"] = new_status
                    p["reviewer_id"] = reviewer
                    p["review_comment"] = comment
                    p["reviewed_at"] = _now_iso()
                    # v1 schema 兼容
                    if "updated_at" in p:
                        p["updated_at"] = _now_iso()

                    # 追加到 review history
                    if "review_history" not in p or not isinstance(p.get("review_history"), list):
                        p["review_history"] = []
                    p["review_history"].append({
                        "decision": new_status,
                        "reviewer_id": reviewer,
                        "review_comment": comment,
                        "reviewed_at": p["reviewed_at"],
                    })

                    saved = self._save()
                    return {
                        "success": saved,
                        "proposal_id": proposal_id,
                        "new_status": new_status,
                        "decision": new_status,
                        "reviewer": reviewer,
                        "comment": comment,
                        "reviewed_at": p["reviewed_at"],
                    }
        return {
            "success": False,
            "proposal_id": proposal_id,
            "error": "proposal_not_found",
        }

    def approve(
        self,
        proposal_id: str,
        reviewer: str,
        comment: str = "",
    ) -> Dict[str, Any]:
        """
        标记 proposal 为 approved。

        注意:仅标记 status=approved + decision=approve,
        **不**自动 apply 到 Personality。
        真正的 apply 需通过 ProposalManager.accept_proposal() 显式调用。
        """
        if not reviewer:
            return {"success": False, "error": "reviewer_required"}
        return self._update_proposal(proposal_id, APPROVED_STATUS, reviewer, comment)

    def reject(
        self,
        proposal_id: str,
        reviewer: str,
        comment: str = "",
    ) -> Dict[str, Any]:
        """标记 proposal 为 rejected(reason 必填,建议填写)。"""
        if not reviewer:
            return {"success": False, "error": "reviewer_required"}
        return self._update_proposal(proposal_id, REJECTED_STATUS, reviewer, comment)

    def archive(
        self,
        proposal_id: str,
        reviewer: str,
        comment: str = "",
    ) -> Dict[str, Any]:
        """标记 proposal 为 archived(归档,不再处理)。"""
        if not reviewer:
            return {"success": False, "error": "reviewer_required"}
        return self._update_proposal(proposal_id, ARCHIVED_STATUS, reviewer, comment)

    def reset_to_pending(
        self,
        proposal_id: str,
        reviewer: str,
        comment: str = "manual reset for re-review",
    ) -> Dict[str, Any]:
        """将已 review 的 proposal 重置为 pending(用于重新 review)。

        仅用于人工纠错,会记录到 review_history。
        """
        if not reviewer:
            return {"success": False, "error": "reviewer_required"}
        with self._lock:
            for p in self._data.get("proposals", []):
                if self._get_id(p) == proposal_id:
                    if "review_history" not in p or not isinstance(p.get("review_history"), list):
                        p["review_history"] = []
                    p["review_history"].append({
                        "decision": "reset",
                        "reviewer_id": reviewer,
                        "review_comment": comment,
                        "reviewed_at": _now_iso(),
                    })
                    p["status"] = PENDING_STATUS
                    p["decision"] = None  # 清空 decision
                    p["reviewed_at"] = None
                    p["reviewer_id"] = ""
                    p["review_comment"] = ""
                    if "updated_at" in p:
                        p["updated_at"] = _now_iso()
                    saved = self._save()
                    return {
                        "success": saved,
                        "proposal_id": proposal_id,
                        "new_status": PENDING_STATUS,
                        "reviewer": reviewer,
                    }
        return {
            "success": False,
            "proposal_id": proposal_id,
            "error": "proposal_not_found",
        }

    # ============================================================
    # 统计与健康度
    # ============================================================
    def summary(self) -> Dict[str, Any]:
        """返回 review 系统摘要。"""
        with self._lock:
            proposals = list(self._data.get("proposals", []))
        pending = [p for p in proposals if self._get_status(p) in PENDING_STATUSES]
        return {
            "total": len(proposals),
            "pending_count": len(pending),
            "approved_count": sum(1 for p in proposals if self._get_status(p) == APPROVED_STATUS),
            "rejected_count": sum(1 for p in proposals if self._get_status(p) == REJECTED_STATUS),
            "archived_count": sum(1 for p in proposals if self._get_status(p) == ARCHIVED_STATUS),
            "by_status": self.count_by_status(),
            "path": str(self.path),
        }


# ============================================================
# CLI 入口(供 scripts/ 工具调用)
# ============================================================
def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Proposal 人工 Review 工具")
    parser.add_argument(
        "--proposals",
        type=Path,
        default=Path("data/growth/proposals/proposals.json"),
        help="proposals.json 路径",
    )

    sub = parser.add_subparsers(dest="action", help="review 动作")

    # list
    p_list = sub.add_parser("list", help="列出 proposal")
    p_list.add_argument("--status", type=str, default="pending", help="状态过滤(默认 pending)")

    # show
    p_show = sub.add_parser("show", help="查看详情")
    p_show.add_argument("proposal_id", type=str)

    # approve
    p_approve = sub.add_parser("approve", help="批准 proposal(不自动 apply)")
    p_approve.add_argument("proposal_id", type=str)
    p_approve.add_argument("--reviewer", type=str, required=True)
    p_approve.add_argument("--comment", type=str, default="")

    # reject
    p_reject = sub.add_parser("reject", help="拒绝 proposal")
    p_reject.add_argument("proposal_id", type=str)
    p_reject.add_argument("--reviewer", type=str, required=True)
    p_reject.add_argument("--comment", type=str, default="")

    # archive
    p_archive = sub.add_parser("archive", help="归档 proposal")
    p_archive.add_argument("proposal_id", type=str)
    p_archive.add_argument("--reviewer", type=str, required=True)
    p_archive.add_argument("--comment", type=str, default="")

    # summary
    sub.add_parser("summary", help="摘要统计")

    args = parser.parse_args(argv)

    if not args.action:
        parser.print_help()
        return 0

    reviewer = ProposalReviewer(args.proposals)

    if args.action == "list":
        if args.status == "all":
            items = reviewer.list_all()
        elif args.status == "pending":
            items = reviewer.list_pending()
        else:
            items = reviewer.list_by_status(args.status)
        print(f"共 {len(items)} 条 proposal (status={args.status}):")
        for p in items[:50]:
            pid = reviewer._get_id(p)
            conf = reviewer._get_confidence(p)
            status = reviewer._get_status(p)
            print(f"  - {pid} | status={status} | confidence={conf:.2f}")

    elif args.action == "show":
        detail = reviewer.get_detail(args.proposal_id)
        if detail is None:
            print(f"❌ proposal 不存在: {args.proposal_id}", file=sys.stderr)
            return 1
        print(json.dumps(detail, ensure_ascii=False, indent=2))

    elif args.action in ("approve", "reject", "archive"):
        method = getattr(reviewer, args.action)
        result = method(args.proposal_id, args.reviewer, args.comment)
        if result.get("success"):
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        else:
            print(f"❌ 操作失败: {result}", file=sys.stderr)
            return 1

    elif args.action == "summary":
        print(json.dumps(reviewer.summary(), ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main())
