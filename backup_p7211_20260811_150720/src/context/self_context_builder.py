"""
Phase 4.0 — R2.6.4-B: SelfContextBuilder（只读→过滤→格式化）

数据流（严格 SCB-1：来源不重新计算）：
  PersonalityState            ──readonly──→ personality_summary.top_traits + trait_deltas_recent + personality_state_version
  SelfModelSnapshot           ──readonly──→ identity_summary（identity_view.origin/core_values/stable_identity_markers / self_model_version）
                                           + personality_summary.stable_traits（stable_traits.keys）
                                           + growth_summary（SelfModel.development_view.evolution_history 或 personality_view.recent_changes）
  SelfReflectionSnapshot      ──readonly──→ reflection_summary.observed_change_bullets + unresolved_tension_titles + has_identity_tension + reflection_id
  EvolutionRecord[]           ──readonly──→ growth_summary.evolutions_count_last_30 / latest_evolution_at / recent_change_bullets
  IdentityContinuityReport    ──readonly──→ continuity_status（规则映射，不重新计算）

动作：read → filter → format。不 interpret / 不 decide / 不 modify。

红线（AST 遵守 self_context_schema.FORBIDDEN_IMPORTS / FORBIDDEN_CALLS）：
  红线 1: SelfReflection 不直接控制回复 → 不 import prompt_builder/llm/openai/engine/orchestrator；不 build_prompt/generate
  红线 2: Prompt Context 不修改任何状态 → 不 apply_evolution/update_traits/set_trait/create_proposal/accept_proposal/govern/register_anchor
  红线 3: 人格与自我认知分离 → 所有变化都必须经过 Proposal→Approval→Evolution，本模块不写任何可变对象

膨胀保护（SCB-5）：
  recent_change_bullets ≤ max_recent_changes
  top_traits             ≤ default TOP_TRAITS_LIMIT（5）
  trait 数值             ≤ max_trait_detail_digits（四舍五入，0~6 位）

injection_policy 按 mode 默认（SCB-2）：
  minimal          → allow_list = [identity_summary:origin_bullet, continuity_status]；其他 summary 可被外部 Prompt Context 层按 policy 过滤
  summary_only     → allow_list = 4 份 summary 的 summary 级别 tokens（不含 recent_changes/deltas 细节）
  read_only_identity → allow_list = 4 份 summary + continuity_status（默认全允许，但 deny_list 可覆盖）

额外：
  100% 异常隔离（build → try/except → _build_degraded：create_empty_self_context + 连续性标记 continuous_safe）
  assigned_version：build 每次严格 +1（成功或失败都只 +1）
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.context.self_context_schema import (
    CONTINUITY_STATUS_WHITELIST,
    INJECTION_POLICY_MODES,
    validate_self_context_shape,
    create_empty_self_context,
)

logger = logging.getLogger(__name__)


# 白名单（Contract 的 _ALLOWED_TOKENS 与 schema 保持一致，避免魔法字符串分散）
_POLICY_TOKENS: Tuple[str, ...] = (
    "identity_summary:origin_bullet",
    "identity_summary:core_values",
    "identity_summary:trait_anchors",
    "personality_summary:top_traits",
    "personality_summary:stable_traits",
    "personality_summary:deltas",
    "growth_summary:bullets",
    "growth_summary:transitions",
    "growth_summary:count",
    "growth_summary:timestamp",
    "reflection_summary:observed",
    "reflection_summary:tensions",
    "reflection_summary:has_tension",
    "continuity_status",
)

_MODE_DEFAULT_ALLOW: Dict[str, Tuple[str, ...]] = {
    "minimal": (
        "identity_summary:origin_bullet",
        "continuity_status",
    ),
    "summary_only": (
        "identity_summary:origin_bullet",
        "identity_summary:core_values",
        "identity_summary:trait_anchors",
        "personality_summary:top_traits",
        "personality_summary:stable_traits",
        "growth_summary:count",
        "reflection_summary:tensions",
        "continuity_status",
    ),
    "read_only_identity": tuple(_POLICY_TOKENS),
}

TOP_TRAITS_LIMIT_DEFAULT = 5
LEVEL_HIGH_THRESHOLD = 0.67
LEVEL_LOW_THRESHOLD = 0.33
RECENT_DELTAS_LIMIT_DEFAULT = 5
RECENT_INTEREST_TRANSITIONS_LIMIT = 10
REFLECTION_OBSERVED_LIMIT_DEFAULT = 5
REFLECTION_TENSIONS_LIMIT_DEFAULT = 8


def _trait_level(v: float) -> str:
    if v >= LEVEL_HIGH_THRESHOLD:
        return "high"
    if v <= LEVEL_LOW_THRESHOLD:
        return "low"
    return "medium"


def _delta_dir(d: float) -> str:
    if d > 1e-9:
        return "up"
    if d < -1e-9:
        return "down"
    return "flat"


# 内部 trait → 人类可读别名；找不到时回退原词
TRAIT_HUMAN_READABLE_ALIAS: Dict[str, str] = {
    "creativity": "创造力",
    "curiosity": "好奇心",
    "empathy": "共情能力",
    "independence": "独立性",
    "connection_value": "情感联结",
    "social_ease": "社交自在度",
    "stability": "情绪稳定性",
    "resilience": "心理韧性",
    "openness": "开放性",
    "warmth": "温度感",
    "ambition": "进取心",
    "rationality": "理性倾向",
    "idealism": "理想主义倾向",
}


def _human_trait(trait: str) -> str:
    if not trait:
        return trait
    name = str(trait).strip()
    return TRAIT_HUMAN_READABLE_ALIAS.get(name, name)


def _change_type_to_natural(change_type: str, delta: float) -> str:
    """把内部 change_type 转成自然语言描述。

    注意：禁止在对外表达中出现 "trait_delta / interest_transition / new_trait" 这类机器标签。
    """
    ct = str(change_type or "").strip()
    if ct == "interest_transition":
        return "兴趣方向的逐步迁移"
    if ct == "new_trait":
        return "新的兴趣点逐渐形成"
    # 默认 trait_delta / unknown：按 delta 方向
    if delta > 1e-9:
        return "渐进强化"
    if delta < -1e-9:
        return "渐进收敛"
    return "保持稳定"


class SelfContextBuilder:
    """
    R2.6.4-B SelfContextBuilder（read → filter → format，无状态写、无推理、无 LLM 调用）
    """

    def __init__(
        self,
        *,
        default_mode: str = "read_only_identity",
        top_traits_limit: int = TOP_TRAITS_LIMIT_DEFAULT,
        max_recent_changes: int = 5,
        max_trait_detail_digits: int = 3,
        extra_allow_list: Optional[Sequence[str]] = None,
        extra_deny_list: Optional[Sequence[str]] = None,
    ) -> None:
        if default_mode not in INJECTION_POLICY_MODES:
            raise ValueError(f"default_mode={default_mode!r} 非法，允许={INJECTION_POLICY_MODES}")
        self._default_mode = default_mode
        self._top_traits_limit = max(1, int(top_traits_limit))
        self._max_recent_changes = max(0, int(max_recent_changes))
        if not (0 <= int(max_trait_detail_digits) <= 6):
            raise ValueError("max_trait_detail_digits 必须 0~6")
        self._max_trait_digits = int(max_trait_detail_digits)
        self._extra_allow = tuple(extra_allow_list or ())
        self._extra_deny = tuple(extra_deny_list or ())
        self._version_counter: int = 0

    # ============================================================
    # 主入口
    # ============================================================
    def build(
        self,
        *,
        personality_state: Any = None,
        self_model_snapshot: Any = None,
        self_reflection_snapshot: Any = None,
        evolution_records: Optional[Iterable[Any]] = None,
        identity_continuity_report: Optional[Mapping[str, Any]] = None,
        mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        构造 SelfContext（Contract 合法 dict；后续由 schema validate 断言）

        严格 3 步：read → filter → format。任何异常隔离为 create_empty_self_context。
        """
        self._version_counter += 1
        assigned_version = self._version_counter
        actual_mode = mode if mode in INJECTION_POLICY_MODES else self._default_mode
        try:
            return self._build_safe(
                assigned_version=assigned_version,
                actual_mode=actual_mode,
                personality_state=personality_state,
                self_model_snapshot=self_model_snapshot,
                self_reflection_snapshot=self_reflection_snapshot,
                evolution_records=list(evolution_records or []),
                identity_continuity_report=dict(identity_continuity_report or {}),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[self_context_builder_crash] error=%s", exc)
            return self._build_degraded(exc, assigned_version=assigned_version, mode=actual_mode)

    # ============================================================
    # 安全 build
    # ============================================================
    def _build_safe(
        self,
        *,
        assigned_version: int,
        actual_mode: str,
        personality_state: Any,
        self_model_snapshot: Any,
        self_reflection_snapshot: Any,
        evolution_records: List[Any],
        identity_continuity_report: Dict[str, Any],
    ) -> Dict[str, Any]:
        self._version_counter = assigned_version

        # --- read 数据（只读属性、duck-typed；None 安全） ---
        sm: Dict[str, Any] = self._sm_as_dict(self_model_snapshot)
        sm_identity: Dict[str, Any] = dict(sm.get("identity_view") or {})
        sm_personality: Dict[str, Any] = dict(sm.get("personality_view") or {})
        sm_dev: Dict[str, Any] = dict(sm.get("development_view") or {})
        sm_version = int(sm.get("version", 0) or 0)

        state_snap = self._state_snapshot(personality_state)
        state_traits: Dict[str, float] = dict(state_snap.get("traits") or {})
        state_version = int(state_snap.get("version", 0) or 0)

        # observed_changes / unresolved_tensions / reflection_id
        ref_changes: List[Dict[str, Any]] = []
        ref_tensions: List[Dict[str, Any]] = []
        ref_identity_assessment: str = "identity_compatible"
        reflection_id: str = "ref_empty"
        try:
            if self_reflection_snapshot is not None:
                if isinstance(self_reflection_snapshot, Mapping):
                    ref_changes = list(self_reflection_snapshot.get("observed_changes") or [])
                    ref_tensions = list(self_reflection_snapshot.get("unresolved_tensions") or [])
                    ialign = (self_reflection_snapshot.get("identity_alignment") or {}).get("overall_assessment")
                    if isinstance(ialign, str):
                        ref_identity_assessment = ialign
                    rid = self_reflection_snapshot.get("reflection_id")
                    if isinstance(rid, str) and rid.strip():
                        reflection_id = rid
                else:
                    ref_changes = list(getattr(self_reflection_snapshot, "observed_changes", None) or [])
                    ref_tensions = list(getattr(self_reflection_snapshot, "unresolved_tensions", None) or [])
                    ialign = (getattr(self_reflection_snapshot, "identity_alignment", None) or {}).get("overall_assessment")
                    if isinstance(ialign, str):
                        ref_identity_assessment = ialign
                    rid = getattr(self_reflection_snapshot, "reflection_id", None)
                    if isinstance(rid, str) and rid.strip():
                        reflection_id = rid
        except Exception as exc:  # noqa: BLE001
            logger.debug("[self_context_builder_reflect_skip] error=%s", exc)

        # --- 1) identity_summary（严格来自 sm_identity，不做 interpret） ---
        identity_summary = {
            "origin_bullet": str(sm_identity.get("origin") or ""),
            "core_values_bullets": [str(x).strip() for x in list(sm_identity.get("core_values") or []) if str(x).strip()],
            "trait_anchors_present": [
                str(x).strip() for x in list(sm_identity.get("stable_identity_markers") or []) if str(x).strip()
            ],
            "self_model_version": sm_version if sm_version >= 0 else 0,
        }

        # --- 2) personality_summary（personality_state.traits → top K；stable from sm；recent deltas from evo records 或 sm.recent_changes） ---
        top_traits = self._format_top_traits(state_traits, limit=self._top_traits_limit, digits=self._max_trait_digits)
        stable_trait_names: List[str] = []
        try:
            stable_map = dict(sm_personality.get("stable_traits") or {})
            stable_trait_names = sorted([str(k).strip() for k in stable_map.keys() if str(k).strip()])
        except Exception:  # noqa: BLE001
            stable_trait_names = []
        recent_deltas = self._format_recent_deltas(
            sm_recent_changes=list(sm_personality.get("recent_changes") or []),
            evolution_records=evolution_records,
            limit=RECENT_DELTAS_LIMIT_DEFAULT,
            digits=self._max_trait_digits,
        )
        personality_summary = {
            "top_traits": top_traits,
            "stable_traits": stable_trait_names,
            "trait_deltas_recent": recent_deltas,
            "personality_state_version": state_version if state_version >= 0 else 0,
        }

        # --- 3) growth_summary（evolution_records 统计；interest_transition ids） ---
        bullets, transitions, count_latest30, latest_ts = self._format_growth(
            evolution_records=evolution_records,
            sm_recent=list(sm_personality.get("recent_changes") or []),
            sm_evolution_history=list(sm_dev.get("evolution_history") or []),
            max_recent=self._max_recent_changes,
            transitions_limit=RECENT_INTEREST_TRANSITIONS_LIMIT,
            digits=self._max_trait_digits,
        )
        growth_summary = {
            "recent_change_bullets": bullets,
            "recent_interest_transitions": transitions,
            "evolutions_count_last_30": count_latest30,
            "latest_evolution_at": latest_ts,
        }

        # --- 4) reflection_summary（SelfReflection snapshot facts；不重新计算 tension 状态） ---
        obs_bullets: List[str] = []
        for obs in ref_changes[:REFLECTION_OBSERVED_LIMIT_DEFAULT]:
            try:
                trait = str(obs["trait"])
                delta = round(float(obs.get("delta", 0.0)), self._max_trait_digits)
                ct = str(obs.get("change_type") or "trait_delta")
                ht = _human_trait(trait)
                natural = _change_type_to_natural(ct, delta)
                if abs(delta) > 1e-9:
                    direction = "+" if delta > 0 else "-"
                    obs_bullets.append(
                        f"{ht}：{natural}（{direction}{abs(delta):.{self._max_trait_digits}f}）"
                    )
                else:
                    obs_bullets.append(f"{ht}：{natural}")
            except Exception:  # noqa: BLE001
                continue
        tension_titles: List[str] = []
        for tn in ref_tensions[:REFLECTION_TENSIONS_LIMIT_DEFAULT]:
            try:
                title = str(tn["title"]).strip()
                if title:
                    tension_titles.append(title)
            except Exception:  # noqa: BLE001
                continue
        has_tension = ref_identity_assessment != "identity_compatible"
        reflection_summary = {
            "observed_change_bullets": obs_bullets,
            "unresolved_tension_titles": tension_titles,
            "has_identity_tension": has_tension,
            "reflection_id": reflection_id or "ref_empty",
        }

        # --- 5) continuity_status（规则映射，不重新计算） ---
        continuity_status = self._map_continuity_status(identity_continuity_report)

        # --- 6) injection_policy（默认 allow_list 按 mode；去重；deny_list 优先级高于 allow_list） ---
        allow_list_tokens: List[str] = []
        seen: set = set()
        for tok in _MODE_DEFAULT_ALLOW.get(actual_mode, ()):
            if tok in _POLICY_TOKENS and tok not in seen:
                seen.add(tok)
                allow_list_tokens.append(tok)
        for tok in self._extra_allow:
            if tok in _POLICY_TOKENS and tok not in seen:
                seen.add(tok)
                allow_list_tokens.append(tok)
        deny_list_tokens: List[str] = []
        seen_d: set = set()
        for tok in self._extra_deny:
            if tok in _POLICY_TOKENS and tok not in seen_d:
                seen_d.add(tok)
                deny_list_tokens.append(tok)
        # 实际生效 allow：allow_list_tokens 差集 deny_list_tokens（由上游 Prompt 层负责过滤；这里只把 policy 写入 context）
        injection_policy = {
            "mode": actual_mode,
            "allow_list": allow_list_tokens,
            "deny_list": deny_list_tokens,
            "max_recent_changes": self._max_recent_changes,
            "max_trait_detail_digits": self._max_trait_digits,
        }

        ctx: Dict[str, Any] = {
            "identity_summary": identity_summary,
            "personality_summary": personality_summary,
            "growth_summary": growth_summary,
            "reflection_summary": reflection_summary,
            "continuity_status": continuity_status,
            "injection_policy": injection_policy,
            "version": assigned_version,
        }
        validate_self_context_shape(ctx)
        return ctx

    # ============================================================
    # read helpers（都 readonly，不 modify 参数）
    # ============================================================
    @staticmethod
    def _sm_as_dict(sm: Any) -> Dict[str, Any]:
        try:
            if sm is None:
                return {}
            if isinstance(sm, Mapping):
                return {k: sm[k] for k in sm.keys()}
            if hasattr(sm, "to_dict") and callable(sm.to_dict):
                return dict(sm.to_dict() or {})
            # SelfModelSnapshot：6 个 view + version/generated_at 属性
            out: Dict[str, Any] = {}
            for attr in ("identity_view", "personality_view", "development_view",
                         "contradiction_view", "capability_view", "version", "generated_at"):
                try:
                    out[attr] = getattr(sm, attr)
                except Exception:  # noqa: BLE001
                    pass
            return out
        except Exception as exc:  # noqa: BLE001
            logger.debug("[self_context_sm_read_skip] error=%s", exc)
            return {}

    @staticmethod
    def _state_snapshot(state: Any) -> Dict[str, Any]:
        try:
            if state is None:
                return {"traits": {}, "version": 0}
            if isinstance(state, Mapping):
                return {
                    "traits": dict(state.get("traits") or {}),
                    "version": int(state.get("version", 0) or 0),
                }
            if hasattr(state, "snapshot") and callable(state.snapshot):
                s = state.snapshot()
                return {
                    "traits": dict((s or {}).get("traits", {}) or {}),
                    "version": int((s or {}).get("version", 0) or 0),
                }
            if hasattr(state, "traits") and hasattr(state, "version"):
                return {
                    "traits": dict(getattr(state, "traits", None) or {}),
                    "version": int(getattr(state, "version", 0) or 0),
                }
            return {"traits": {}, "version": 0}
        except Exception as exc:  # noqa: BLE001
            logger.debug("[self_context_state_read_skip] error=%s", exc)
            return {"traits": {}, "version": 0}

    def _format_top_traits(self, traits: Dict[str, float], limit: int, digits: int) -> List[Dict[str, Any]]:
        rows: List[Tuple[str, float]] = []
        for k, v in traits.items():
            try:
                fv = float(v)
            except Exception:  # noqa: BLE001
                continue
            rows.append((str(k), fv))
        rows.sort(key=lambda kv: (-kv[1], kv[0]))
        return [
            {
                "trait": name,
                "level": _trait_level(v),
                "value": round(v, digits),
            }
            for name, v in rows[:limit]
        ]

    @staticmethod
    def _strip_prefix(k: str) -> str:
        for p in ("trait.", "interests.", "interest."):
            if k.startswith(p):
                return k[len(p):]
        return k

    def _format_recent_deltas(
        self,
        *,
        sm_recent_changes: List[Dict[str, Any]],
        evolution_records: List[Any],
        limit: int,
        digits: int,
    ) -> List[Dict[str, Any]]:
        # 优先 sm.recent_changes.affected_traits；如果为空则回退 evolution_records 逐条解析
        out: List[Dict[str, Any]] = []
        seen: set = set()
        for rc in sm_recent_changes:
            try:
                affected = list(rc.get("affected_traits") or [])
                for af in affected:
                    try:
                        trait = self._strip_prefix(str(af["trait"]))
                        delta = round(float(af.get("delta", 0.0)), digits)
                    except Exception:  # noqa: BLE001
                        continue
                    if trait in seen:
                        continue
                    seen.add(trait)
                    out.append({"trait": trait, "delta": delta, "direction": _delta_dir(delta)})
                    if len(out) >= limit:
                        return out
            except Exception:  # noqa: BLE001
                continue
        # 回退：从原始 evolution_records 解析
        for rec in evolution_records:
            try:
                before: Dict[str, Any]
                after: Dict[str, Any]
                if isinstance(rec, Mapping):
                    before = dict(rec.get("before") or {})
                    after = dict(rec.get("after") or {})
                else:
                    before = dict(getattr(rec, "before", None) or {})
                    after = dict(getattr(rec, "after", None) or {})
                for k, v in after.items():
                    try:
                        av = float(v)
                        bv = float(before.get(k, av))
                        delta = round(av - bv, digits)
                        trait = self._strip_prefix(str(k))
                        if trait in seen or abs(delta) < 1e-9:
                            continue
                        seen.add(trait)
                        out.append({"trait": trait, "delta": delta, "direction": _delta_dir(delta)})
                        if len(out) >= limit:
                            return out
                    except Exception:  # noqa: BLE001
                        continue
            except Exception:  # noqa: BLE001
                continue
        return out

    def _format_growth(
        self,
        *,
        evolution_records: List[Any],
        sm_recent: List[Dict[str, Any]],
        sm_evolution_history: List[Dict[str, Any]],
        max_recent: int,
        transitions_limit: int,
        digits: int,
    ) -> Tuple[List[str], List[str], int, str]:
        bullets: List[str] = []
        transitions: List[str] = []
        count_latest30 = 0
        latest_ts = ""
        forbidden_markers = (
            "trait_delta", "interest_transition", "new_trait", "proposal_id",
            "approval_id", "record_id", "confidence", "internal_score",
            "evaluator_meta",
        )
        seen_bullet_stems = set()

        def _push_bullet(b: str) -> None:
            if len(bullets) >= max_recent:
                return
            if not b:
                return
            b_clean = str(b).strip()
            if not b_clean:
                return
            # 防止机器标签泄漏（若源数据写了这些）
            for fm in forbidden_markers:
                if fm in b_clean.lower():
                    return
            stem = b_clean[:24]
            if stem in seen_bullet_stems:
                return
            seen_bullet_stems.add(stem)
            bullets.append(b_clean)

        # sm.recent_changes → bullets（已按时间倒序；限制 max_recent）
        try:
            for rc in sm_recent:
                if len(bullets) >= max_recent:
                    break
                try:
                    affected = list(rc.get("affected_traits") or [])
                    ct = str(rc.get("change_type") or "trait_delta")
                except Exception:  # noqa: BLE001
                    continue
                for af in affected:
                    if len(bullets) >= max_recent:
                        break
                    try:
                        trait = self._strip_prefix(str(af["trait"]))
                        delta = round(float(af.get("delta", 0.0)), digits)
                    except Exception:  # noqa: BLE001
                        continue
                    ht = _human_trait(trait)
                    natural = _change_type_to_natural(ct, delta)
                    direction = "+" if delta > 0 else ("-" if delta < 0 else "")
                    if direction:
                        _push_bullet(f"{ht}：{natural}（{direction}{abs(delta):.{digits}f}）")
                    else:
                        _push_bullet(f"{ht}：{natural}")
        except Exception:  # noqa: BLE001
            pass

        # 回退（无 sm_recent）：从 evolution_history 读取
        if len(bullets) < max_recent:
            try:
                for eh in sm_evolution_history:
                    if len(bullets) >= max_recent:
                        break
                    try:
                        trait = self._strip_prefix(str(eh.get("trait") or eh.get("key") or ""))
                        delta = float(eh.get("delta", 0.0))
                        ct = str(eh.get("change_type") or eh.get("kind") or "trait_delta")
                        ts = str(eh.get("timestamp") or "")
                    except Exception:  # noqa: BLE001
                        continue
                    if not trait:
                        continue
                    ht = _human_trait(trait)
                    natural = _change_type_to_natural(ct, delta)
                    direction = "+" if delta > 0 else ("-" if delta < 0 else "")
                    if direction:
                        _push_bullet(f"{ht}：{natural}（{direction}{abs(round(delta, digits)):.{digits}f}）")
                    else:
                        _push_bullet(f"{ht}：{natural}")
                    if not latest_ts:
                        latest_ts = ts
            except Exception:  # noqa: BLE001
                pass

        # 从 evolution_records 的 reasons（中文自然语言）注入 bullets；
        # 若 reason 含机器关键字（proposal_id=xxx、confidence=xxx）会被 _push_bullet 过滤掉
        if len(bullets) < max_recent and evolution_records:
            try:
                for rec in evolution_records:
                    if len(bullets) >= max_recent:
                        break
                    reasons: List[Any] = []
                    if isinstance(rec, Mapping):
                        reasons = list(rec.get("reasons") or [])
                    else:
                        reasons = list(getattr(rec, "reasons", None) or [])
                    for r in reasons:
                        if len(bullets) >= max_recent:
                            break
                        _push_bullet(str(r))
            except Exception:  # noqa: BLE001
                pass

        # count_latest30: 统计 evolution_records 总数或 sm_evolution_history 中“最近 30”（简化：取 len(records) 与 30 较小值；避免时间比较的时区差）
        try:
            count_latest30 = min(len(evolution_records), 30)
        except Exception:  # noqa: BLE001
            count_latest30 = 0

        # latest_ts: evolution_records 中第一条非空 timestamp
        try:
            for rec in evolution_records:
                ts = ""
                if isinstance(rec, Mapping):
                    ts = str(rec.get("timestamp") or rec.get("created_at") or "")
                else:
                    ts = str(getattr(rec, "timestamp", "") or getattr(rec, "created_at", "") or "")
                if ts:
                    latest_ts = ts
                    break
        except Exception:  # noqa: BLE001
            latest_ts = ""

        # transitions：从 evolution_records 中 change_type == "interest_transition" 的 record_id
        try:
            for rec in evolution_records:
                if len(transitions) >= transitions_limit:
                    break
                ct = ""
                rid = ""
                if isinstance(rec, Mapping):
                    ct = str(rec.get("change_type") or "")
                    rid = str(rec.get("record_id") or "")
                else:
                    ct = str(getattr(rec, "change_type", "") or "")
                    rid = str(getattr(rec, "record_id", "") or "")
                if ct == "interest_transition" and rid:
                    transitions.append(rid)
        except Exception:  # noqa: BLE001
            pass

        return bullets, transitions, count_latest30, latest_ts

    @staticmethod
    def _map_continuity_status(icr: Dict[str, Any]) -> str:
        is_cont = icr.get("is_continuous")
        warnings = list(icr.get("warnings") or [])
        has_break = any(
            str(x).startswith("identity_core_anchor_missing") or
            "identity_break" in str(x) or
            "hard_break" in str(x)
            for x in warnings
        )
        if has_break:
            return "identity_break"
        # 仅 narrative_gap / drift / weight_drift_warning / large_changes
        if warnings or (is_cont is False and not has_break):
            if is_cont is False:
                return "tension_warning"
            # is_cont=True 但 warnings > 0（tension 级别）
            return "continuous_with_tension"
        return "continuous_safe"

    # ============================================================
    # 降级
    # ============================================================
    def _build_degraded(
        self,
        exc: BaseException,
        *,
        assigned_version: int,
        mode: str,
    ) -> Dict[str, Any]:
        self._version_counter = assigned_version
        ctx = create_empty_self_context(version=assigned_version, mode=mode if mode in INJECTION_POLICY_MODES else "minimal")
        # 把 degraded 信息仅写入 identity_summary.origin_bullet（结构化），不改连续性状态（仍 continuous_safe：不影响 LLM 决策）
        try:
            ctx["identity_summary"]["origin_bullet"] = f"self_context_builder_degraded: {type(exc).__name__}"
            validate_self_context_shape(ctx)
        except Exception as exc2:  # noqa: BLE001
            logger.warning("[self_context_degraded_shape_fix_needed] error=%s", exc2)
        return ctx
