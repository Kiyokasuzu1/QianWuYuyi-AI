# -*- coding: utf-8 -*-
"""
src/runtime/self_model/evolution/self_model_evolution_engine.py

Phase 4.5: SelfModelEvolutionEngine —— 自我模型演化协调器。

职责:
- 接收 SelfModelSnapshot + GrowthProposal(们) + ReflectionRecord(们)
- 通过 EvolutionPolicy 评估每一项 change
- 把"通过"的 change 不可变地应用到新 SelfModelSnapshot(深拷贝)
- 把"拒绝"的 change 收集到 rejected_changes(原因 = reject_reason)
- 产出至少一条 EvolutionRecord(可多条,按 source_type 分组)
- 异常隔离:任何步骤失败均不影响主流程,返回"空结果"

约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import src.personality.*
- 不修改 RuntimeContext schema
- 不修改 ResponseEngine.generate()
- 不直接修改原 snapshot —— 使用 from_dict(to_dict()) 不可变拷贝
- 不在 evolution 内部 import 任何 Phase 4.4/4.5 runtime 绑定层(单向依赖)
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional, Union


from src.runtime.self_model.evolution.evolution_record import (
    SelfModelChange,
    EvolutionRecord,
    EvolutionSourceType,
    SelfModelEvolutionResult,
    _coerce_change_list,
    EVOLUTION_RECORD_SCHEMA_VERSION,
)
from src.runtime.self_model.evolution.evolution_policy import (
    EvolutionPolicy,
    EvolutionVerdict,
    EvolutionPolicyDecision,
)


logger = logging.getLogger(__name__)


SELF_MODEL_EVOLUTION_ENGINE_SCHEMA_VERSION = "1.0"


# 工具
def _now_iso() -> str:
    try:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):  # pragma: no cover
        from datetime import datetime
        return datetime.utcnow().isoformat() + "Z"


def _safe_str(value: Any, max_len: int = 80) -> str:
    try:
        s = str(value)
    except Exception:  # noqa: BLE001
        return ""
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


def _coerce_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return list(value)
    if value is None:
        return []
    return [value]


# ============================================================
# SelfModelEvolutionEngine
# ============================================================
class SelfModelEvolutionEngine:
    """自我模型演化协调器(Phase 4.5 / v1.0)。

    使用方式:
        engine = SelfModelEvolutionEngine(policy=EvolutionPolicy())
        result = engine.evolve(
            snapshot=snap,
            proposals=[proposal1, proposal2],
            reflections=[refl1],
        )
        if not result.is_noop:
            new_snap = result.new_snapshot

    关键不变量:
    - 原 snapshot 不被修改(深拷贝隔离)
    - 每次 accept/reject 必产生 EvolutionRecord
    - 空输入 → 返回 is_noop=True 的空 result,不抛异常
    - 单条 change 失败 → 该条被 reject,其它正常
    """

    name: str = "self_model_evolution_engine"
    schema_version: str = SELF_MODEL_EVOLUTION_ENGINE_SCHEMA_VERSION

    def __init__(
        self,
        policy: Optional[EvolutionPolicy] = None,
        max_changes_per_record: int = 64,
        auto_record: bool = True,
    ) -> None:
        self._policy: EvolutionPolicy = policy or EvolutionPolicy()
        try:
            self._max_changes = int(max_changes_per_record)
        except (TypeError, ValueError):
            self._max_changes = 64
        if self._max_changes < 1:
            self._max_changes = 64
        self._auto_record: bool = bool(auto_record)

        # 状态
        self._evolve_count: int = 0
        self._accept_count: int = 0
        self._reject_count: int = 0
        self._record_count: int = 0
        self._last_error: Optional[str] = None
        self._last_identity_id: Optional[str] = None
        self._last_source_types: List[str] = []

    # --------------------------------------------------------
    # Policy
    # --------------------------------------------------------
    @property
    def policy(self) -> EvolutionPolicy:
        return self._policy

    def set_policy(self, policy: EvolutionPolicy) -> None:
        self._policy = policy or EvolutionPolicy()

    # --------------------------------------------------------
    # 主入口: evolve
    # --------------------------------------------------------
    def evolve(
        self,
        snapshot: Optional[Any] = None,
        proposals: Optional[List[Any]] = None,
        reflections: Optional[List[Any]] = None,
        manual_changes: Optional[List[Any]] = None,
    ) -> SelfModelEvolutionResult:
        """主入口:执行一次演化。

        Args:
            snapshot:    SelfModelSnapshot(可为 None,会构造空 result)
            proposals:   GrowthProposal 列表(可为 None)
            reflections: ReflectionRecord 列表(可为 None)
            manual_changes: 手动注入的 change 列表(可为 None)

        Returns:
            SelfModelEvolutionResult
        """
        try:
            self._evolve_count += 1
            self._last_source_types = []

            # 1) 拷贝原 snapshot(深拷贝 → 不可变)
            if snapshot is None:
                # 空演化:不修改,返回空 result
                return SelfModelEvolutionResult(
                    is_noop=True,
                    summary="no_snapshot",
                )
            # original_snapshot = 输入引用(供对比)
            original = snapshot
            new_snapshot = self._clone_snapshot(snapshot)

            # 2) 提取 identity_id
            identity_id = self._extract_identity_id(snapshot)
            from_version = self._extract_version(snapshot)

            # 3) 收集所有 changes + decisions
            all_accepted: List[SelfModelChange] = []
            all_rejected: List[SelfModelChange] = []
            all_reject_reasons: List[str] = []
            all_records: List[EvolutionRecord] = []

            # 3.1) 来自 proposals
            proposal_list = _coerce_list(proposals)
            self._last_source_types.append(EvolutionSourceType.GROWTH_PROPOSAL.value)
            for p in proposal_list:
                record = self._process_proposal(
                    p, identity_id=identity_id,
                    from_version=from_version,
                )
                if record is None:
                    continue
                # 拆分 accept / reject
                for c in record.changes:
                    all_accepted.append(c)
                for c, r in zip(record.rejected_changes, record.reject_reasons):
                    all_rejected.append(c)
                    all_reject_reasons.append(r)
                all_records.append(record)

            # 3.2) 来自 reflections
            reflection_list = _coerce_list(reflections)
            if reflection_list:
                self._last_source_types.append(EvolutionSourceType.REFLECTION.value)
                for r in reflection_list:
                    record = self._process_reflection(
                        r, identity_id=identity_id,
                        from_version=from_version,
                    )
                    if record is None:
                        continue
                    for c in record.changes:
                        all_accepted.append(c)
                    for c, rs in zip(record.rejected_changes, record.reject_reasons):
                        all_rejected.append(c)
                        all_reject_reasons.append(rs)
                    all_records.append(record)

            # 3.3) 来自 manual_changes
            manual_list = _coerce_list(manual_changes)
            if manual_list:
                self._last_source_types.append(EvolutionSourceType.MANUAL.value)
                record = self._process_manual(
                    manual_list, identity_id=identity_id,
                    from_version=from_version,
                )
                if record is not None:
                    for c in record.changes:
                        all_accepted.append(c)
                    for c, rs in zip(record.rejected_changes, record.reject_reasons):
                        all_rejected.append(c)
                        all_reject_reasons.append(rs)
                    all_records.append(record)

            # 4) 应用 accepted 到 new_snapshot(顺序:先 preferences 再 current_state 再 cautious)
            applied = self._apply_changes(new_snapshot, all_accepted)
            new_version = from_version + (1 if applied else 0)
            try:
                if applied and hasattr(new_snapshot, "version_bump"):
                    # _apply_changes 内部已调用 version_bump,这里仅同步读
                    new_version = int(getattr(new_snapshot, "version", from_version))
            except Exception:  # noqa: BLE001
                pass

            # 5) 写入每条 record 的 to_version(应用后)
            for r in all_records:
                try:
                    r.to_version = int(new_version)
                except Exception:  # noqa: BLE001
                    pass

            # 6) 拼装 result
            is_noop = (len(all_accepted) == 0)
            summary = (
                f"accepted={len(all_accepted)} "
                f"rejected={len(all_rejected)} "
                f"records={len(all_records)}"
            )
            self._accept_count += len(all_accepted)
            self._reject_count += len(all_rejected)
            self._record_count += len(all_records)
            self._last_identity_id = identity_id
            self._last_error = None

            return SelfModelEvolutionResult(
                accepted_changes=all_accepted,
                rejected_changes=all_rejected,
                reject_reasons=all_reject_reasons,
                evolution_records=all_records,
                new_snapshot=new_snapshot if applied else None,
                original_snapshot=original,
                is_noop=is_noop,
                summary=summary,
            )
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"evolve_failed: {exc}"
            logger.warning("SelfModelEvolutionEngine.evolve 失败: %s", exc)
            # 返回空 result,不影响主流程
            return SelfModelEvolutionResult(
                is_noop=True,
                summary=f"evolve_failed:{_safe_str(exc, 80)}",
            )

    # --------------------------------------------------------
    # 子流程
    # --------------------------------------------------------
    def _process_proposal(
        self,
        proposal: Any,
        identity_id: str,
        from_version: int,
    ) -> Optional[EvolutionRecord]:
        if proposal is None:
            return None
        try:
            changes = self._policy.from_proposal(proposal)
        except Exception as exc:  # noqa: BLE001
            logger.debug("EvolutionPolicy.from_proposal 失败: %s", exc)
            changes = []
        return self._evaluate_and_record(
            changes=changes,
            source_type=EvolutionSourceType.GROWTH_PROPOSAL.value,
            source_id=self._safe_attr(proposal, "proposal_id", default=""),
            identity_id=identity_id,
            from_version=from_version,
        )

    def _process_reflection(
        self,
        reflection: Any,
        identity_id: str,
        from_version: int,
    ) -> Optional[EvolutionRecord]:
        if reflection is None:
            return None
        # 从 ReflectionRecord 提取"可能被演化"的字段
        try:
            changes = self._extract_changes_from_reflection(reflection)
        except Exception as exc:  # noqa: BLE001
            logger.debug("_extract_changes_from_reflection 失败: %s", exc)
            changes = []
        return self._evaluate_and_record(
            changes=changes,
            source_type=EvolutionSourceType.REFLECTION.value,
            source_id=self._safe_attr(reflection, "reflection_id", default=""),
            identity_id=identity_id,
            from_version=from_version,
        )

    def _process_manual(
        self,
        manual_changes: List[Any],
        identity_id: str,
        from_version: int,
    ) -> Optional[EvolutionRecord]:
        if not manual_changes:
            return None
        changes = _coerce_change_list(manual_changes)
        return self._evaluate_and_record(
            changes=changes,
            source_type=EvolutionSourceType.MANUAL.value,
            source_id="manual",
            identity_id=identity_id,
            from_version=from_version,
        )

    # --------------------------------------------------------
    # 从 ReflectionRecord 派生 change
    # --------------------------------------------------------
    def _extract_changes_from_reflection(
        self,
        reflection: Any,
    ) -> List[SelfModelChange]:
        """从 ReflectionRecord 派生可能的 change。

        派生规则(启发式):
        - kind == TRAIT_TREND / VALUE_SHIFT → 映射到 cautious 字段(stable_traits / core_values)
        - kind == PREFERENCE → preferences
        - kind == STATE_NOTE / GROWTH_NOTE → current_state.mood / temporary_states
        - confidence 直接用 reflection.confidence
        """
        if reflection is None:
            return []
        kind = self._safe_attr(reflection, "reflection_kind", default="")
        try:
            confidence = float(self._safe_attr(reflection, "confidence", default=0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        try:
            confidence = max(0.0, min(1.0, confidence))
        except Exception:  # noqa: BLE001
            confidence = 0.0
        rid = self._safe_attr(reflection, "reflection_id", default="")
        interp = self._safe_attr(reflection, "interpretation", default="")

        out: List[SelfModelChange] = []
        if kind == "trait_trend":
            out.append(SelfModelChange(
                field_name="stable_traits",
                old_value=None,
                new_value={"note": interp},
                reason=interp or "trait_trend",
                confidence=confidence,
                evidence_ids=[rid],
            ))
        elif kind == "value_shift":
            out.append(SelfModelChange(
                field_name="core_values",
                old_value=None,
                new_value={"note": interp},
                reason=interp or "value_shift",
                confidence=confidence,
                evidence_ids=[rid],
            ))
        elif kind == "preference":
            out.append(SelfModelChange(
                field_name="preferences",
                old_value=None,
                new_value={"note": interp},
                reason=interp or "preference",
                confidence=confidence,
                evidence_ids=[rid],
            ))
        elif kind == "state_note":
            out.append(SelfModelChange(
                field_name="current_state.mood",
                old_value=None,
                new_value=interp,
                reason=interp or "state_note",
                confidence=confidence,
                evidence_ids=[rid],
            ))
        elif kind == "growth_note":
            out.append(SelfModelChange(
                field_name="temporary_states",
                old_value=None,
                new_value={"note": interp},
                reason=interp or "growth_note",
                confidence=confidence,
                evidence_ids=[rid],
            ))
        return out

    # --------------------------------------------------------
    # 评估 + 记录
    # --------------------------------------------------------
    def _evaluate_and_record(
        self,
        changes: List[SelfModelChange],
        source_type: str,
        source_id: str,
        identity_id: str,
        from_version: int,
    ) -> Optional[EvolutionRecord]:
        if not self._auto_record:
            return None
        accepted: List[SelfModelChange] = []
        rejected: List[SelfModelChange] = []
        reject_reasons: List[str] = []
        for ch in changes:
            try:
                decision = self._policy.evaluate(ch, source_type=source_type)
            except Exception as exc:  # noqa: BLE001
                rejected.append(ch)
                reject_reasons.append(f"policy_evaluate_error:{exc}")
                continue
            if decision.is_allowed():
                accepted.append(ch)
            else:
                rejected.append(ch)
                reject_reasons.append(decision.reason or "rejected")
        # 截断(防止巨量)
        accepted = accepted[: self._max_changes]
        rejected = rejected[: self._max_changes]
        reject_reasons = reject_reasons[: self._max_changes]
        record = EvolutionRecord(
            identity_id=identity_id,
            source_type=source_type,
            source_id=source_id,
            from_version=from_version,
            to_version=from_version,  # 后续会修正
            changes=accepted,
            rejected_changes=rejected,
            reject_reasons=reject_reasons,
            summary=(
                f"{source_type} accepted={len(accepted)} "
                f"rejected={len(rejected)}"
            ),
            metadata={
                "source_id": source_id,
            },
        )
        return record

    # --------------------------------------------------------
    # 应用 changes 到 snapshot
    # --------------------------------------------------------
    def _apply_changes(
        self,
        snapshot: Any,
        changes: List[SelfModelChange],
    ) -> bool:
        """把 accepted changes 应用到 snapshot(就地修改新 snapshot;不修改原 snapshot)。

        返回: True 表示至少一项被应用;False 表示无变化。
        """
        if snapshot is None or not changes:
            return False
        applied_any = False
        for ch in changes:
            try:
                if self._apply_one(snapshot, ch):
                    applied_any = True
            except Exception as exc:  # noqa: BLE001
                logger.debug("_apply_one 失败（已隔离）: %s", exc)
        if applied_any:
            try:
                if hasattr(snapshot, "version_bump"):
                    snapshot.version_bump()
            except Exception:  # noqa: BLE001
                pass
        return applied_any

    def _apply_one(
        self,
        snapshot: Any,
        change: SelfModelChange,
    ) -> bool:
        """应用单个 change 到 snapshot。支持:
        - preferences: list[dict] 追加
        - interests: list[dict] 追加
        - behavior_tendencies: list[dict] 追加
        - temporary_states: list[dict] 追加
        - current_state / current_state.mood / current_state.energy / current_state.recent_focus: dict 设置
        - stable_traits / stable_traits.new: list[dict] 追加
        - communication_style: dict 设置
        - core_values: list[dict] 追加
        """
        fname = change.field_name
        # current_state.* 子字段
        if fname in ("current_state.mood", "current_state.energy", "current_state.recent_focus"):
            sub = fname.split(".", 1)[1]
            cur = self._read_field(snapshot, "current_state")
            if not isinstance(cur, dict):
                cur = {}
            cur[sub] = change.new_value
            self._write_field(snapshot, "current_state", cur)
            return True
        # 直接列表字段
        list_fields = {
            "preferences", "interests", "behavior_tendencies",
            "temporary_states", "stable_traits", "core_values",
        }
        if fname in list_fields:
            arr = self._read_field(snapshot, fname)
            if not isinstance(arr, list):
                arr = []
            entry = self._build_list_entry(change)
            arr.append(entry)
            self._write_field(snapshot, fname, arr)
            return True
        # subkey 形式: stable_traits.new
        if fname in ("stable_traits.new", "stable_traits.strength",
                     "communication_style", "preferences.new", "preferences.dislikes",
                     "interests.active", "core_values.new"):
            # 追加为同名字段
            arr = self._read_field(snapshot, fname.split(".")[0])
            if not isinstance(arr, list):
                arr = []
            arr.append(self._build_list_entry(change))
            self._write_field(snapshot, fname.split(".")[0], arr)
            return True
        # current_state 整体
        if fname == "current_state":
            cur = self._read_field(snapshot, "current_state")
            if not isinstance(cur, dict):
                cur = {}
            if isinstance(change.new_value, dict):
                cur.update(change.new_value)
            else:
                cur["value"] = change.new_value
            self._write_field(snapshot, "current_state", cur)
            return True
        # 兜底:写入 meta
        meta = self._read_field(snapshot, "meta")
        if not isinstance(meta, dict):
            meta = {}
        meta[fname] = change.new_value
        self._write_field(snapshot, "meta", meta)
        return True

    def _build_list_entry(self, change: SelfModelChange) -> Dict[str, Any]:
        entry: Dict[str, Any] = {
            "value": change.new_value,
            "confidence": change.confidence,
            "reason": change.reason,
        }
        if change.evidence_ids:
            entry["evidence_ids"] = list(change.evidence_ids)
        return entry

    def _read_field(self, snapshot: Any, name: str) -> Any:
        if snapshot is None:
            return None
        if isinstance(snapshot, dict):
            return snapshot.get(name)
        try:
            return getattr(snapshot, name)
        except Exception:  # noqa: BLE001
            return None

    def _write_field(self, snapshot: Any, name: str, value: Any) -> None:
        if snapshot is None:
            return
        if isinstance(snapshot, dict):
            snapshot[name] = value
            return
        try:
            setattr(snapshot, name, value)
        except Exception:  # noqa: BLE001
            pass

    # --------------------------------------------------------
    # 工具
    # --------------------------------------------------------
    def _clone_snapshot(self, snapshot: Any) -> Any:
        """深拷贝 snapshot(优先用 to_dict/from_dict,否则 copy.deepcopy)。

        行为:
        - 优先尝试: snapshot.to_dict() -> type(snapshot).from_dict(d)
          若 to_dict() 抛异常, 整个 _clone_snapshot 也会抛(由外层 try/except 接住)
        - 兜底: copy.deepcopy(snapshot)
        - 若两种方式都失败, 抛 RuntimeError
        """
        if snapshot is None:
            return None
        if hasattr(snapshot, "to_dict") and hasattr(snapshot, "from_dict"):
            d = snapshot.to_dict()  # 失败时直接抛(由外层接住)
            return type(snapshot).from_dict(d)  # type: ignore[arg-type]
        try:
            return copy.deepcopy(snapshot)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"clone_snapshot_failed: {exc}"
            ) from exc

    def _extract_identity_id(self, snapshot: Any) -> str:
        if snapshot is None:
            return ""
        if isinstance(snapshot, dict):
            return str(snapshot.get("identity_id", "") or "")
        try:
            return str(getattr(snapshot, "identity_id", "") or "")
        except Exception:  # noqa: BLE001
            return ""

    def _extract_version(self, snapshot: Any) -> int:
        if snapshot is None:
            return 0
        if isinstance(snapshot, dict):
            try:
                return int(snapshot.get("version", 0) or 0)
            except (TypeError, ValueError):
                return 0
        try:
            return int(getattr(snapshot, "version", 0) or 0)
        except Exception:  # noqa: BLE001
            return 0

    @staticmethod
    def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(name, default)
        try:
            return getattr(obj, name)
        except Exception:  # noqa: BLE001
            return default

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    @property
    def evolve_count(self) -> int:
        return self._evolve_count

    @property
    def accept_count(self) -> int:
        return self._accept_count

    @property
    def reject_count(self) -> int:
        return self._reject_count

    @property
    def record_count(self) -> int:
        return self._record_count

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def last_identity_id(self) -> Optional[str]:
        return self._last_identity_id

    @property
    def last_source_types(self) -> List[str]:
        return list(self._last_source_types)

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "schema_version": self.schema_version,
            "evolve_count": self._evolve_count,
            "accept_count": self._accept_count,
            "reject_count": self._reject_count,
            "record_count": self._record_count,
            "last_identity_id": self._last_identity_id,
            "last_source_types": list(self._last_source_types),
            "policy": self._policy.describe() if self._policy else None,
            "max_changes_per_record": self._max_changes,
            "auto_record": self._auto_record,
            "last_error": self._last_error,
        }

    def health_check(self) -> Dict[str, Any]:
        h: Dict[str, Any] = {
            "healthy": True,
            "name": self.name,
            "schema_version": self.schema_version,
            "evolve_count": self._evolve_count,
            "policy_healthy": True,
        }
        if self._policy is not None:
            try:
                ph = self._policy.health_check()
                h["policy_health"] = ph
                h["policy_healthy"] = bool(ph.get("healthy", True))
            except Exception as exc:  # noqa: BLE001
                h["policy_healthy"] = False
                h["policy_health_error"] = _safe_str(exc, 80)
        if self._last_error is not None:
            h["last_error"] = self._last_error
            h["healthy"] = False
        return h


__all__ = [
    "SelfModelEvolutionEngine",
    "SELF_MODEL_EVOLUTION_ENGINE_SCHEMA_VERSION",
]
