"""
Phase 4.0 — R2.6.1: SelfModelBuilder（自我模型构建器 数据聚合层）

定位：
  把 IdentityAnchor + PersonalityState + EvolutionRecord（只读）聚合成 SelfModelSnapshot。

职责：
  - 只聚合，不推理（不判断"变化好不好"，那是 Approval / IdentityContinuity / Reflection）
  - 不修改任何输入（anchor / state / records 都只读）
  - 输入缺失时安全降级（返回空 view，不抛异常）
  - deterministic：同一输入返回同一 SelfModel（same version / same generated_at precision 以内）

数据流（严格单向）：
  IdentityAnchorManager  ──readonly──→  identity_view
  PersonalityState      ──readonly──→  personality_view + contradiction_view + capability_view
  EvolutionRecord list  ──readonly──→  personality_view.recent_changes + development_view

红线（SB-3 / SB-4）：
  ❌ 不 import FORBIDDEN_IMPORTS（growth / approval / relationship / emotion / memory / adapter）
  ❌ 不调用 FORBIDDEN_CALLS（apply_evolution / accept_proposal / accept_experience / govern_proposal / set_trait / execute）
  ❌ 不修改输入对象的任何字段
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.self_model.self_model_schema import (
    SelfModel,
    DEFAULT_CONTRADICTION_THRESHOLD,
    DEFAULT_STABLE_TRAIT_THRESHOLD,
    DEFAULT_EVOLVING_TRAIT_THRESHOLD,
    DEFAULT_RECENT_CHANGES_LIMIT,
    validate_self_model_shape,
)
from src.self_model.self_model_snapshot import SelfModelSnapshot, build_snapshot

logger = logging.getLogger(__name__)


# ============================================================
# 已知的 trait 对立即义（用于 contradiction_view 结构检测）
# 只做结构检测，不做 LLM 推理；永远是 detected 状态，不会自动"消解"
# ============================================================
KNOWN_TENSION_PAIRS: List[Tuple[str, str, str]] = [
    # (trait_a, trait_b, description_en)
    ("independence", "connection_value", "values independence while seeking connection"),
    ("self_reliance", "vulnerability", "values self-reliance while embracing vulnerability"),
    ("rationality", "emotionality", "balances reason with emotional sensitivity"),
    ("orderliness", "spontaneity", "values structure while appreciating spontaneity"),
]


class SelfModelBuilder:
    """R2.6.1: 数据聚合层。3 个只读输入 → SelfModelSnapshot。"""

    def __init__(
        self,
        *,
        stable_trait_threshold: float = DEFAULT_STABLE_TRAIT_THRESHOLD,
        evolving_trait_threshold: float = DEFAULT_EVOLVING_TRAIT_THRESHOLD,
        contradiction_threshold: float = DEFAULT_CONTRADICTION_THRESHOLD,
        recent_changes_limit: int = DEFAULT_RECENT_CHANGES_LIMIT,
    ) -> None:
        self._stable_t = float(stable_trait_threshold)
        self._evolving_t = float(evolving_trait_threshold)
        self._contradiction_t = float(contradiction_threshold)
        self._recent_changes_limit = max(1, int(recent_changes_limit))
        # 版本计数器：每次 build 递增
        self._version_counter: int = 0

    # ============================================================
    # 入口
    # ============================================================
    def build(
        self,
        identity_anchor_manager: Any = None,
        personality_state: Any = None,
        evolution_records: List[Any] = None,
        *,
        generated_at: Optional[str] = None,
    ) -> SelfModelSnapshot:
        """
        从三个只读输入构建 SelfModelSnapshot。

        Args:
            identity_anchor_manager: IdentityAnchorManager 实例（可选；None → 空 identity_view）
            personality_state: PersonalityState 实例（可选；None → 空 personality_view）
            evolution_records: EvolutionRecord list（可选；None/空 → 空 development + 空 recent）
            generated_at: 可选覆盖时间戳（测试用）

        Returns:
            SelfModelSnapshot（永远成功；输入异常会降级为空 view）
        """
        try:
            return self._build_safe(
                identity_anchor_manager=identity_anchor_manager,
                personality_state=personality_state,
                evolution_records=evolution_records or [],
                generated_at=generated_at,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[self_model_builder_isolated] error=%s", exc)
            return self._build_degraded_snapshot(generated_at=generated_at)

    # ============================================================
    # 内部实现
    # ============================================================
    def _build_safe(
        self,
        *,
        identity_anchor_manager: Any,
        personality_state: Any,
        evolution_records: List[Any],
        generated_at: Optional[str],
    ) -> SelfModelSnapshot:
        self._version_counter += 1
        version = self._version_counter
        ts = generated_at or datetime.now(timezone.utc).isoformat()

        # === View 1: identity_view ===
        identity_view = self._build_identity_view(identity_anchor_manager)

        # === View 2: personality_view ===
        personality_view = self._build_personality_view(personality_state, evolution_records)

        # === View 3: development_view ===
        development_view = self._build_development_view(evolution_records)

        # === View 4: contradiction_view ===
        contradiction_view = self._build_contradiction_view(personality_state)

        # === View 5: capability_view ===
        capability_view = self._build_capability_view(
            personality_state, identity_anchor_manager,
        )

        model: SelfModel = {
            "identity_view": identity_view,
            "personality_view": personality_view,
            "development_view": development_view,
            "contradiction_view": contradiction_view,
            "capability_view": capability_view,
            "version": version,
            "generated_at": ts,
        }
        # 形状校验（一旦契约不符合立即报错）
        validate_self_model_shape(model)
        return build_snapshot(model)

    # ============================================================
    # View 1: identity_view（来源：IdentityAnchorManager，只读）
    # ============================================================
    @staticmethod
    def _build_identity_view(iam: Any) -> Dict[str, Any]:
        view: Dict[str, Any] = {
            "origin": "",
            "core_values": [],
            "stable_identity_markers": [],
        }
        if iam is None:
            return view

        try:
            # origin = anchor_creator 的 principle（如果有）
            creator = iam.get_anchor("anchor_creator")
            if creator is not None:
                principle = getattr(creator, "principle", "") or ""
                display = getattr(creator, "display_name", "") or ""
                if principle:
                    view["origin"] = f"[{display}] {principle}" if display else principle
        except Exception as exc:  # noqa: BLE001
            logger.debug("[self_model_builder_identity_origin_skip] error=%s", exc)

        try:
            # core_values = 所有 anchor 的 related_core_values 去重
            seen_values: set = set()
            all_anchors = list(iam.get_all_anchors() or [])
            for a in all_anchors:
                rcv = list(getattr(a, "related_core_values", None) or [])
                for v in rcv:
                    v = str(v).strip()
                    if v and v not in seen_values:
                        seen_values.add(v)
                        view["core_values"].append(v)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[self_model_builder_identity_core_skip] error=%s", exc)

        try:
            # stable_identity_markers = 所有 core anchor 的 display_name
            all_anchors = list(iam.get_all_anchors() or [])
            for a in all_anchors:
                if bool(getattr(a, "is_core", False)):
                    name = getattr(a, "display_name", None) or getattr(a, "name", "")
                    name = str(name).strip()
                    if name and name not in view["stable_identity_markers"]:
                        view["stable_identity_markers"].append(name)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[self_model_builder_identity_markers_skip] error=%s", exc)

        return view

    # ============================================================
    # View 2: personality_view（来源：PersonalityState + EvolutionRecord，只读）
    # ============================================================
    def _build_personality_view(
        self,
        state: Any,
        records: List[Any],
    ) -> Dict[str, Any]:
        view: Dict[str, Any] = {
            "stable_traits": {},
            "evolving_traits": {},
            "recent_changes": [],
        }
        if state is None:
            return view

        # 从 EvolutionRecord 收集每个 trait 的总变化幅度
        trait_delta_map: Dict[str, float] = self._collect_trait_deltas(records)

        # 当前 trait 快照
        traits: Dict[str, float] = {}
        try:
            if hasattr(state, "snapshot") and callable(state.snapshot):
                snap = state.snapshot()
                traits = dict(snap.get("traits", {}) or {})
            elif isinstance(state, dict):
                traits = dict(state.get("traits", {}) or {})
            elif hasattr(state, "traits"):
                traits = dict(getattr(state, "traits", {}) or {})
        except Exception as exc:  # noqa: BLE001
            logger.debug("[self_model_builder_trait_snapshot_skip] error=%s", exc)

        # 分类 stable vs evolving
        for trait_name, current_val in traits.items():
            try:
                current = round(float(current_val), 6)
            except Exception:  # noqa: BLE001
                continue
            delta = abs(trait_delta_map.get(trait_name, 0.0))
            if delta < self._stable_t:
                view["stable_traits"][trait_name] = current
            elif delta >= self._evolving_t:
                view["evolving_traits"][trait_name] = current
            else:
                # 灰度区：两个都放（但 evolving 优先，放在 stable 前）
                view["evolving_traits"][trait_name] = current

        # recent_changes：按 records 的 timestamp 倒序截取
        view["recent_changes"] = self._build_recent_changes(records)
        return view

    @staticmethod
    def _collect_trait_deltas(records: List[Any]) -> Dict[str, float]:
        """收集每个 trait 在 evolution_records 中的总变化幅度（|after - before|）。"""
        deltas: Dict[str, float] = {}
        for rec in records:
            # 可能是 dict、EvolutionRecord TypedDict、或任何 duck-typed
            try:
                if isinstance(rec, dict):
                    before = rec.get("before") or {}
                    after = rec.get("after") or {}
                else:
                    before = getattr(rec, "before", None) or {}
                    after = getattr(rec, "after", None) or {}
                before_dict = dict(before)
                after_dict = dict(after)
            except Exception:  # noqa: BLE001
                continue
            for key, after_val in after_dict.items():
                try:
                    after_v = float(after_val)
                    before_v = float(before_dict.get(key, after_v))
                except Exception:  # noqa: BLE001
                    continue
                delta = abs(after_v - before_v)
                k = str(key)
                # 剥离 trait. / interests. 前缀
                for prefix in ("trait.", "interests.", "interest."):
                    if k.startswith(prefix):
                        k = k[len(prefix):]
                        break
                deltas[k] = deltas.get(k, 0.0) + delta
        return deltas

    def _build_recent_changes(self, records: List[Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for rec in records:
            try:
                if isinstance(rec, dict):
                    rid = str(rec.get("record_id") or "")
                    ts = str(rec.get("timestamp") or rec.get("created_at") or "")
                    reasons = list(rec.get("reasons") or [])
                    change_type = str(rec.get("change_type") or "")
                    before = rec.get("before") or {}
                    after = rec.get("after") or {}
                    proposal_id = str(rec.get("proposal_id") or "")
                    approval_id = str(rec.get("approval_id") or "")
                else:
                    rid = str(getattr(rec, "record_id", "") or "")
                    ts = str(getattr(rec, "timestamp", "") or getattr(rec, "created_at", "") or "")
                    reasons = list(getattr(rec, "reasons", None) or [])
                    change_type = str(getattr(rec, "change_type", "") or "")
                    before = dict(getattr(rec, "before", None) or {})
                    after = dict(getattr(rec, "after", None) or {})
                    proposal_id = str(getattr(rec, "proposal_id", "") or "")
                    approval_id = str(getattr(rec, "approval_id", "") or "")
                # 计算 trait level deltas
                affected = []
                before_d = dict(before)
                after_d = dict(after)
                for k, v in after_d.items():
                    try:
                        bv = float(before_d.get(k, v))
                        av = float(v)
                        delta = round(av - bv, 6)
                        if abs(delta) < 1e-9:
                            continue
                        short_k = str(k)
                        for p in ("trait.", "interests.", "interest."):
                            if short_k.startswith(p):
                                short_k = short_k[len(p):]
                                break
                        affected.append({
                            "trait": short_k,
                            "delta": delta,
                            "before": round(bv, 6),
                            "after": round(av, 6),
                        })
                    except Exception:  # noqa: BLE001
                        continue
                if not affected and not rid:
                    continue
                rows.append({
                    "record_id": rid,
                    "timestamp": ts,
                    "reason_summary": reasons,
                    "change_type": change_type,
                    "proposal_id": proposal_id,
                    "approval_id": approval_id,
                    "affected_traits": affected,
                })
            except Exception as exc:  # noqa: BLE001
                logger.debug("[self_model_builder_recent_skip] error=%s", exc)
                continue

        # 按 timestamp 倒序（缺失 "" 排最后）
        rows.sort(
            key=lambda r: (r["timestamp"] == "", r["timestamp"]),
            reverse=True,
        )
        return rows[: self._recent_changes_limit]

    # ============================================================
    # View 3: development_view（来源：EvolutionRecord list，只读）
    # ============================================================
    def _build_development_view(self, records: List[Any]) -> Dict[str, Any]:
        view: Dict[str, Any] = {
            "evolution_history": [],
            "important_turning_points": [],
        }
        # 把每一条 evolution record 摘要化为 history entry
        for rec in records:
            entry = self._rec_to_history_entry(rec)
            if entry is not None:
                view["evolution_history"].append(entry)

        # 按 timestamp 排序（最老在前 = 时间线顺序）
        view["evolution_history"].sort(
            key=lambda e: (e["timestamp"] == "", e["timestamp"]),
            reverse=False,
        )

        # turning points：所有 change_type != "trait_delta" 的记录（兴趣迁移 / 新兴趣）
        # + 任意大的变化（单一 trait delta >= 0.2）
        for entry in view["evolution_history"]:
            is_turning = False
            if entry["change_type"] in ("interest_transition", "new_trait"):
                is_turning = True
            else:
                for at in entry["affected_traits"]:
                    if abs(float(at.get("delta", 0.0))) >= 0.2:
                        is_turning = True
                        break
            if is_turning:
                view["important_turning_points"].append({
                    "record_id": entry["record_id"],
                    "timestamp": entry["timestamp"],
                    "change_type": entry["change_type"],
                    "reason_summary": entry["reason_summary"],
                    "affected_traits": entry["affected_traits"],
                    "status": "detected",  # ❌ 不自动解决
                })

        return view

    @staticmethod
    def _rec_to_history_entry(rec: Any) -> Optional[Dict[str, Any]]:
        try:
            if isinstance(rec, dict):
                rid = str(rec.get("record_id") or "")
                ts = str(rec.get("timestamp") or rec.get("created_at") or "")
                reasons = list(rec.get("reasons") or [])
                ct = str(rec.get("change_type") or "trait_delta")
                before = rec.get("before") or {}
                after = rec.get("after") or {}
            else:
                rid = str(getattr(rec, "record_id", "") or "")
                ts = str(getattr(rec, "timestamp", "") or getattr(rec, "created_at", "") or "")
                reasons = list(getattr(rec, "reasons", None) or [])
                ct = str(getattr(rec, "change_type", "") or "trait_delta")
                before = dict(getattr(rec, "before", None) or {})
                after = dict(getattr(rec, "after", None) or {})

            affected = []
            before_d = dict(before)
            after_d = dict(after)
            for k, v in after_d.items():
                try:
                    bv = float(before_d.get(k, v))
                    av = float(v)
                    delta = round(av - bv, 6)
                    if abs(delta) < 1e-9:
                        continue
                    short_k = str(k)
                    for p in ("trait.", "interests.", "interest."):
                        if short_k.startswith(p):
                            short_k = short_k[len(p):]
                            break
                    affected.append({
                        "trait": short_k,
                        "delta": delta,
                        "before": round(bv, 6),
                        "after": round(av, 6),
                    })
                except Exception:  # noqa: BLE001
                    continue
            if not rid and not affected:
                return None
            return {
                "record_id": rid,
                "timestamp": ts,
                "reason_summary": reasons,
                "change_type": ct,
                "affected_traits": affected,
            }
        except Exception:  # noqa: BLE001
            return None

    # ============================================================
    # View 4: contradiction_view（结构检测，不消解）
    # ============================================================
    def _build_contradiction_view(self, state: Any) -> Dict[str, Any]:
        view: Dict[str, Any] = {"detected_tensions": []}
        if state is None:
            return view
        # 取当前 trait
        traits: Dict[str, float] = {}
        try:
            if hasattr(state, "snapshot") and callable(state.snapshot):
                snap = state.snapshot()
                traits = dict(snap.get("traits", {}) or {})
            elif isinstance(state, dict):
                traits = dict(state.get("traits", {}) or {})
            elif hasattr(state, "traits"):
                traits = dict(getattr(state, "traits", {}) or {})
        except Exception as exc:  # noqa: BLE001
            logger.debug("[self_model_builder_contradiction_skip] error=%s", exc)
            return view

        for a_key, b_key, desc in KNOWN_TENSION_PAIRS:
            a_val = traits.get(a_key)
            b_val = traits.get(b_key)
            if a_val is None or b_val is None:
                continue
            try:
                a = float(a_val)
                b = float(b_val)
            except Exception:  # noqa: BLE001
                continue
            # 张力检测：两个值都高（>= contradiction_threshold 以上水平）
            baseline = 0.5
            if a >= baseline + self._contradiction_t and b >= baseline + self._contradiction_t:
                view["detected_tensions"].append({
                    "trait_a": a_key,
                    "trait_b": b_key,
                    "value_a": round(a, 4),
                    "value_b": round(b, 4),
                    "description": desc,
                    "status": "detected",  # ❌ 永远 detected，不自动解决
                })
        return view

    # ============================================================
    # View 5: capability_view（来源：PersonalityState metadata + IdentityAnchor）
    # ============================================================
    @staticmethod
    def _build_capability_view(state: Any, iam: Any) -> Dict[str, Any]:
        # 静态注册的系统能力（不会变化的部分）
        strengths: List[str] = [
            "long_term_memory",
            "structured_reflection",
            "evidence_driven_growth",
            "identity_anchor_protection",
            "evolution_record_audit",
            "transition_protection",
        ]
        limitations: List[str] = [
            "cannot_access_unknown_information",
            "cannot_change_identity_core",
            "no_real_time_experience",
            "growth_requires_multiple_observations",
        ]

        # 从 IdentityAnchor core 推导：权重最高的 2 个 anchor → strengths
        if iam is not None:
            try:
                all_anchors = list(iam.get_all_anchors() or [])
                all_anchors.sort(
                    key=lambda a: -float(getattr(a, "weight", 0.0) or 0.0),
                )
                for a in all_anchors[:2]:
                    n = getattr(a, "name", None)
                    if n:
                        strengths.append(f"core_strength:{n}")
            except Exception:  # noqa: BLE001
                pass

        # 从 PersonalityState 推导：trait > 0.7 算作 known_strengths
        if state is not None:
            try:
                if hasattr(state, "snapshot") and callable(state.snapshot):
                    snap = state.snapshot()
                    traits = dict(snap.get("traits", {}) or {})
                elif isinstance(state, dict):
                    traits = dict(state.get("traits", {}) or {})
                elif hasattr(state, "traits"):
                    traits = dict(getattr(state, "traits", {}) or {})
                else:
                    traits = {}
                for tn, tv in traits.items():
                    try:
                        if float(tv) >= 0.7:
                            strengths.append(f"trait_advantage:{tn}")
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                pass

        # 去重保序
        def _dedup(xs: List[str]) -> List[str]:
            seen: set = set()
            out = []
            for x in xs:
                if x not in seen:
                    seen.add(x)
                    out.append(x)
            return out

        return {
            "known_strengths": _dedup(strengths),
            "limitations": _dedup(limitations),
        }

    # ============================================================
    # 降级快照：build 崩溃时返回的合法空模型
    # ============================================================
    def _build_degraded_snapshot(self, *, generated_at: Optional[str]) -> SelfModelSnapshot:
        self._version_counter += 1
        ts = generated_at or datetime.now(timezone.utc).isoformat()
        model: SelfModel = {
            "identity_view": {"origin": "", "core_values": [], "stable_identity_markers": []},
            "personality_view": {"stable_traits": {}, "evolving_traits": {}, "recent_changes": []},
            "development_view": {"evolution_history": [], "important_turning_points": []},
            "contradiction_view": {"detected_tensions": []},
            "capability_view": {"known_strengths": [], "limitations": []},
            "version": self._version_counter,
            "generated_at": ts,
        }
        validate_self_model_shape(model)
        return build_snapshot(model)
