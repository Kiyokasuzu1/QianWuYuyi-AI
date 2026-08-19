# -*- coding: utf-8 -*-
"""
src/runtime/self_model/audit/snapshot_diff_engine.py

Phase 4.2.4: SnapshotDiffEngine —— 快照版本差异分析

职责:
- 对比两个 SelfModelSnapshot,输出结构化 diff
- 覆盖字段: identity / core_values / stable_traits / preferences /
  current_state / entries
- 不修改 snapshot 内容
- 不调用 LLM,纯结构化

约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import src.personality.* 业务模块
- 不修改 RuntimeContext schema
- 异常隔离:任何 diff 失败返回最小 diff({error: ...})

设计:
- diff(a, b) 永远是 b 相对 a 的变化(add/remove/change)
- summary() 给出高阶总结(总条目数 / 变化数)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


SNAPSHOT_DIFF_ENGINE_SCHEMA_VERSION = "1.0"


def _to_dict(snap: Any) -> Optional[Dict[str, Any]]:
    if snap is None:
        return None
    if isinstance(snap, dict):
        return dict(snap)
    fn = getattr(snap, "to_dict", None)
    if callable(fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return None
    return None


def _field(snap_dict: Optional[Dict[str, Any]], key: str, default: Any) -> Any:
    if not isinstance(snap_dict, dict):
        return default
    return snap_dict.get(key, default)


def _by_key(items: Any, key: str = "name") -> Dict[str, Dict[str, Any]]:
    """把 list[dict] 转 dict[key, dict]。"""
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(items, list):
        return out
    for it in items:
        if isinstance(it, dict):
            k = it.get(key)
            if k is not None:
                out[str(k)] = it
        else:
            try:
                k = getattr(it, key, None)
                if k is not None:
                    out[str(k)] = it if isinstance(it, dict) else {"value": it}
            except Exception:  # noqa: BLE001
                continue
    return out


def _diff_list(
    a_list: List[Any], b_list: List[Any], key: str = "name",
) -> Dict[str, List[Any]]:
    """比较 list[dict]: 返回 added / removed / common (按 key)。"""
    a = _by_key(a_list, key=key)
    b = _by_key(b_list, key=key)
    added = [b[k] for k in b.keys() if k not in a]
    removed = [a[k] for k in a.keys() if k not in b]
    common: List[Dict[str, Any]] = []
    for k in a.keys() & b.keys():
        if a[k] != b[k]:
            common.append({
                "key": k,
                "before": a[k],
                "after": b[k],
            })
    return {"added": added, "removed": removed, "changed": common}


def _diff_dict(
    a_dict: Dict[str, Any], b_dict: Dict[str, Any],
) -> Dict[str, Any]:
    """比较 dict: 返回 added_keys / removed_keys / changed。"""
    a = a_dict if isinstance(a_dict, dict) else {}
    b = b_dict if isinstance(b_dict, dict) else {}
    added = {k: b[k] for k in b.keys() if k not in a}
    removed = {k: a[k] for k in a.keys() if k not in b}
    changed: Dict[str, Dict[str, Any]] = {}
    for k in a.keys() & b.keys():
        if a[k] != b[k]:
            changed[k] = {"before": a[k], "after": b[k]}
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def _diff_entries(
    a_entries: List[Any], b_entries: List[Any],
) -> Dict[str, List[Dict[str, Any]]]:
    """比较 entries 列表(用 entry_id 标识)。"""
    def _index(items: Any) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        if not isinstance(items, list):
            return out
        for it in items:
            if isinstance(it, dict):
                eid = it.get("entry_id") or it.get("id")
                if eid:
                    out[str(eid)] = it
            else:
                eid = getattr(it, "entry_id", None) or getattr(it, "id", None)
                if eid:
                    out[str(eid)] = (
                        it.to_dict() if hasattr(it, "to_dict") and callable(it.to_dict)
                        else {"summary": str(it)}
                    )
        return out

    a = _index(a_entries)
    b = _index(b_entries)
    added = [b[k] for k in b.keys() if k not in a]
    removed = [a[k] for k in a.keys() if k not in b]
    changed: List[Dict[str, Any]] = []
    for k in a.keys() & b.keys():
        if a[k] != b[k]:
            changed.append({
                "entry_id": k,
                "before": a[k],
                "after": b[k],
            })
    return {"added": added, "removed": removed, "changed": changed}


class SnapshotDiffEngine:
    """SelfModelSnapshot 版本差异引擎(Phase 4.2.4 / v1.0)。

    字段:
    - _diff_count:     int              # 累计 diff 次数
    - _last_diff:      Optional[Dict]   # 最近一次 diff 结果
    - _last_error:     Optional[str]    # 最近错误

    方法:
    - diff(a, b) -> Dict                    # 详细 diff
    - summary(a, b) -> Dict                 # 高阶 summary
    - has_changes(diff_result) -> bool      # 是否有任何变化
    - health_check() / describe()
    """

    def __init__(self) -> None:
        self._diff_count: int = 0
        self._last_diff: Optional[Dict[str, Any]] = None
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # diff
    # --------------------------------------------------------
    def diff(
        self,
        a: Any,
        b: Any,
        identity_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """对比两个 snapshot,返回 diff 结构。

        a: 旧 / before
        b: 新 / after

        返回:
            {
              "schema_version": "1.0",
              "identity_id": str,
              "from_version": int,
              "to_version": int,
              "identity": {added, removed, changed},
              "core_values": {added, removed, changed},
              "stable_traits": {added, removed, changed},
              "preferences": {added, removed, changed},
              "current_state": {added, removed, changed},
              "entries": {added, removed, changed},
              "summary": {total_changes: int, ...},
            }
        """
        if a is None or b is None:
            self._last_error = "diff_input_none"
            return {
                "schema_version": SNAPSHOT_DIFF_ENGINE_SCHEMA_VERSION,
                "error": "one_or_both_inputs_none",
                "summary": {"total_changes": 0},
            }
        try:
            a_dict = _to_dict(a) or {}
            b_dict = _to_dict(b) or {}
            fid = (
                identity_id
                or _field(a_dict, "identity_id", "")
                or _field(b_dict, "identity_id", "")
            )
            from_v = _field(a_dict, "version", 0)
            to_v = _field(b_dict, "version", 0)

            identity_diff = _diff_dict(
                _field(a_dict, "identity", {}),
                _field(b_dict, "identity", {}),
            )
            cv_diff = _diff_list(
                _field(a_dict, "core_values", []),
                _field(b_dict, "core_values", []),
                key="key",
            )
            # core_values 也可能用 name / label
            if not cv_diff["added"] and not cv_diff["removed"] and not cv_diff["changed"]:
                cv_diff = _diff_list(
                    _field(a_dict, "core_values", []),
                    _field(b_dict, "core_values", []),
                    key="name",
                )
            st_diff = _diff_list(
                _field(a_dict, "stable_traits", []),
                _field(b_dict, "stable_traits", []),
                key="name",
            )
            pref_diff = _diff_list(
                _field(a_dict, "preferences", []),
                _field(b_dict, "preferences", []),
                key="name",
            )
            cs_diff = _diff_dict(
                _field(a_dict, "current_state", {}),
                _field(b_dict, "current_state", {}),
            )
            entries_diff = _diff_entries(
                _field(a_dict, "entries", []),
                _field(b_dict, "entries", []),
            )

            summary = self._make_summary(
                identity_diff, cv_diff, st_diff,
                pref_diff, cs_diff, entries_diff,
            )
            result: Dict[str, Any] = {
                "schema_version": SNAPSHOT_DIFF_ENGINE_SCHEMA_VERSION,
                "identity_id": fid,
                "from_version": from_v,
                "to_version": to_v,
                "identity": identity_diff,
                "core_values": cv_diff,
                "stable_traits": st_diff,
                "preferences": pref_diff,
                "current_state": cs_diff,
                "entries": entries_diff,
                "summary": summary,
            }
            self._diff_count += 1
            self._last_diff = result
            self._last_error = None
            return result
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"diff_failed: {exc}"
            logger.warning("SnapshotDiffEngine.diff 失败: %s", exc)
            return {
                "schema_version": SNAPSHOT_DIFF_ENGINE_SCHEMA_VERSION,
                "error": str(exc),
                "summary": {"total_changes": 0},
            }

    def _make_summary(
        self,
        identity_diff: Dict[str, Any],
        cv_diff: Dict[str, Any],
        st_diff: Dict[str, Any],
        pref_diff: Dict[str, Any],
        cs_diff: Dict[str, Any],
        entries_diff: Dict[str, Any],
    ) -> Dict[str, Any]:
        identity_changes = (
            len(identity_diff.get("added", {}))
            + len(identity_diff.get("removed", {}))
            + len(identity_diff.get("changed", {}))
        )
        cv_changes = (
            len(cv_diff.get("added", []))
            + len(cv_diff.get("removed", []))
            + len(cv_diff.get("changed", []))
        )
        st_changes = (
            len(st_diff.get("added", []))
            + len(st_diff.get("removed", []))
            + len(st_diff.get("changed", []))
        )
        pref_changes = (
            len(pref_diff.get("added", []))
            + len(pref_diff.get("removed", []))
            + len(pref_diff.get("changed", []))
        )
        cs_changes = (
            len(cs_diff.get("added", {}))
            + len(cs_diff.get("removed", {}))
            + len(cs_diff.get("changed", {}))
        )
        entries_changes = (
            len(entries_diff.get("added", []))
            + len(entries_diff.get("removed", []))
            + len(entries_diff.get("changed", []))
        )
        return {
            "total_changes": (
                identity_changes + cv_changes + st_changes
                + pref_changes + cs_changes + entries_changes
            ),
            "identity_changes": identity_changes,
            "core_values_changes": cv_changes,
            "stable_traits_changes": st_changes,
            "preferences_changes": pref_changes,
            "current_state_changes": cs_changes,
            "entries_changes": entries_changes,
        }

    def summary(self, a: Any, b: Any) -> Dict[str, Any]:
        """只返回 summary 部分(便宜一些)。"""
        full = self.diff(a, b)
        return full.get("summary", {"total_changes": 0})

    def has_changes(self, diff_result: Dict[str, Any]) -> bool:
        if not isinstance(diff_result, dict):
            return False
        summary = diff_result.get("summary") or {}
        try:
            return int(summary.get("total_changes", 0)) > 0
        except (TypeError, ValueError):
            return False

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    @property
    def diff_count(self) -> int:
        return self._diff_count

    @property
    def last_diff(self) -> Optional[Dict[str, Any]]:
        return self._last_diff

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": True,
            "schema_version": SNAPSHOT_DIFF_ENGINE_SCHEMA_VERSION,
            "diff_count": self._diff_count,
            "last_error": self._last_error,
        }

    def describe(self) -> Dict[str, Any]:
        return {
            "schema_version": SNAPSHOT_DIFF_ENGINE_SCHEMA_VERSION,
            "diff_count": self._diff_count,
            "last_error": self._last_error,
        }


__all__ = [
    "SnapshotDiffEngine",
    "SNAPSHOT_DIFF_ENGINE_SCHEMA_VERSION",
]
