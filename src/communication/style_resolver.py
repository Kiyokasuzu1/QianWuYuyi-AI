# -*- coding: utf-8 -*-
"""
Phase 4.2-IMPL-C1: CommunicationStyleResolver (Snapshot Migration)

从 RelationshipSnapshot 推导 CommunicationStyle。

职责：
- 接受 RelationshipSnapshot（双时间尺度关系事实）
- 统一推导出 CommunicationStyle
- 缺失字段使用安全默认值，不崩溃
- 不 import 业务模块，不修改状态

P4.2 迁移变更：
- resolve() 新增 snapshot 参数（RelationshipSnapshot 路径）
- 旧 relationship_state 路径保留为向后兼容（Legacy 路径）
- 移除 trust * 0.7 猜测性推导（snapshot 路径）
- 拆分 activity 为 current.interaction_frequency / long_term.activity_level
- 拆分 familiarity / trust 为 current / long_term 双轨

推导规则（snapshot 路径）：
  AddressingPolicy:
    - addressing_strength = f(long_term.familiarity, long_term.trust, long_term.bond_strength)
    - preferred_name / forbidden_names: 从 user_identity 获取

  TonePolicy:
    - warmth = f(current.familiarity, current.trust)
    - intimacy = f(current.familiarity, long_term.bond_strength)
    - formality = 1 - f(current.familiarity)
    - playfulness = f(current.trust, current.interaction_frequency)

  InteractionPolicy:
    - response_distance = f(current.familiarity, current.trust)
    - emotional_attunement = f(current.trust, current.familiarity)
    - initiative_level = f(current.trust, current.interaction_frequency, long_term.bond_strength)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, TYPE_CHECKING

from src.contracts.communication_style import (
    CommunicationStyle,
    AddressingPolicy,
    TonePolicy,
    InteractionPolicy,
    StyleSource,
)

if TYPE_CHECKING:
    from src.contracts.relationship_snapshot import RelationshipSnapshot


# ============================================================
# 安全默认值
# ============================================================

# 所有推导值被 clamp 到 [0, 1]
_DEFAULT_FAMILIARITY = 0.2
_DEFAULT_TRUST = 0.3
_DEFAULT_BOND = 0.1
_DEFAULT_ACTIVITY = 0.2
_DEFAULT_COLLABORATION = 0.0
_DEFAULT_SHARED_HISTORY = 0.0
_DEFAULT_STAGE = "initial"


# ============================================================
# Resolver 实现
# ============================================================


class CommunicationStyleResolver:
    """Phase 4.2: 从 RelationshipSnapshot 推导 CommunicationStyle。

    支持两种输入路径：
    - snapshot（推荐）：RelationshipSnapshot（双时间尺度）
    - relationship_state（Legacy）：dict 或 v0.6/v3.5.27 对象（向后兼容）
    """

    # ============================================================
    # 公共入口
    # ============================================================

    def resolve(
        self,
        relationship_state: Any = None,
        user_identity: Optional[Dict[str, Any]] = None,
        *,
        snapshot: Optional["RelationshipSnapshot"] = None,
    ) -> CommunicationStyle:
        """从关系状态推导沟通风格。

        Args:
            relationship_state: [Legacy] 关系状态（dict 或 有 .get() 的对象）
            user_identity: 用户身份信息（含 preferred_name, display_name 等）
            snapshot: [推荐] RelationshipSnapshot（双时间尺度关系事实）

        Returns:
            CommunicationStyle: 永不为 None，缺失字段使用安全默认值
        """
        if snapshot is not None:
            return self._resolve_from_snapshot(snapshot, user_identity or {})
        return self._resolve_legacy(relationship_state, user_identity or {})

    # ============================================================
    # P4.2 Snapshot 路径（推荐）
    # ============================================================

    def _resolve_from_snapshot(
        self,
        snapshot: "RelationshipSnapshot",
        identity: Dict[str, Any],
    ) -> CommunicationStyle:
        """从 RelationshipSnapshot 推导 CommunicationStyle。

        关键原则：
        - current.* 用于「此刻表达」公式（warmth, formality, playfulness, response_distance）
        - long_term.* 用于「关系深度」公式（addressing, intimacy_bond, initiative_bond）
        - 不猜测缺失字段：bond_strength=0.0 就是 0.0，不用 trust*0.7 替代
        """
        cur = snapshot.current
        lt = snapshot.long_term

        # ── 当前互动域（current）──
        cur_familiarity = max(0.0, min(1.0, cur.familiarity))
        cur_trust = max(0.0, min(1.0, cur.trust))
        cur_interaction_freq = max(0.0, min(1.0, cur.interaction_frequency))
        cur_collaboration = max(0.0, min(1.0, cur.collaboration))
        cur_stage = cur.relationship_stage or "initial"

        # ── 长期积累域（long_term）──
        lt_familiarity = max(0.0, min(1.0, lt.familiarity))
        lt_trust = max(0.0, min(1.0, lt.trust))
        lt_bond = max(0.0, min(1.0, lt.bond_strength))
        lt_activity = max(0.0, min(1.0, lt.activity_level))
        lt_shared_history = max(0.0, min(1.0, lt.shared_history))

        # 推导 AddressingPolicy（长期关系深度）
        addressing = self._derive_addressing_snapshot(
            identity, lt_familiarity, lt_trust, lt_bond
        )

        # 推导 TonePolicy（当前表达 + 长期羁绊）
        tone = self._derive_tone_snapshot(
            cur_familiarity, cur_trust, lt_bond, cur_interaction_freq
        )

        # 推导 InteractionPolicy（当前表达 + 长期羁绊）
        interaction = self._derive_interaction_snapshot(
            cur_familiarity, cur_trust, lt_bond, cur_interaction_freq
        )

        # 构建 StyleSource
        source = StyleSource(
            relationship_stage=cur_stage,
            familiarity=cur_familiarity,
            trust=cur_trust,
            bond=lt_bond,
            collaboration=cur_collaboration,
            shared_history=lt_shared_history,
            activity_level=cur_interaction_freq,
            source_version="4.2-snapshot",
        )

        return CommunicationStyle(
            addressing=addressing,
            tone=tone,
            interaction=interaction,
            source=source,
            generated_at=datetime.now().isoformat(),
        )

    # ============================================================
    # Snapshot 路径 — 推导逻辑
    # ============================================================

    @staticmethod
    def _derive_addressing_snapshot(
        identity: Dict[str, Any],
        lt_familiarity: float,
        lt_trust: float,
        lt_bond: float,
    ) -> AddressingPolicy:
        """从长期关系推导称呼策略。

        addressing_strength = lt_familiarity × lt_trust × lt_bond_strength
        称呼是长期关系信号，不应被当前互动直接改变。
        """
        strength = lt_familiarity * lt_trust * lt_bond
        strength = round(max(0.0, min(1.0, strength)), 4)

        preferred = identity.get("preferred_name", "")
        display = identity.get("display_name", "")
        forbidden = identity.get("forbidden_names", [])

        if not forbidden and isinstance(display, str) and display and preferred and display != preferred:
            forbidden = [display]

        return AddressingPolicy(
            preferred_name=str(preferred) if preferred else "",
            fallback_name=str(display) if display else "",
            forbidden_names=list(forbidden),
            addressing_strength=strength,
            source="derived" if strength > 0.0 else "default",
        )

    @staticmethod
    def _derive_tone_snapshot(
        cur_familiarity: float,
        cur_trust: float,
        lt_bond: float,
        cur_interaction_freq: float,
    ) -> TonePolicy:
        """推导语气参数。

        warmth:     current.familiarity × 0.7 + current.trust × 0.3
        intimacy:   current.familiarity × 0.5 + long_term.bond_strength × 0.5
        formality:  1 - current.familiarity × 0.8
        playfulness: current.trust × 0.5 + current.interaction_frequency × 0.5
        """
        warmth = round(cur_familiarity * 0.7 + cur_trust * 0.3, 4)
        intimacy = round(cur_familiarity * 0.5 + lt_bond * 0.5, 4)
        formality = round(max(0.0, 1.0 - cur_familiarity * 0.8), 4)
        playfulness = round(cur_trust * 0.5 + cur_interaction_freq * 0.5, 4)

        tags = CommunicationStyleResolver._build_tone_tags(
            warmth, intimacy, formality, playfulness
        )

        return TonePolicy(
            warmth=warmth,
            intimacy=intimacy,
            formality=formality,
            playfulness=playfulness,
            tone_tags=tags,
        )

    @staticmethod
    def _derive_interaction_snapshot(
        cur_familiarity: float,
        cur_trust: float,
        lt_bond: float,
        cur_interaction_freq: float,
    ) -> InteractionPolicy:
        """推导互动行为参数。

        response_distance:     current.familiarity × 0.5 + current.trust × 0.5
        emotional_attunement:  current.trust × 0.7 + current.familiarity × 0.3
        initiative_level:      current.trust × 0.4
                               + current.interaction_frequency × 0.3
                               + long_term.bond_strength × 0.3
        """
        response_distance = round(cur_familiarity * 0.5 + cur_trust * 0.5, 4)
        emotional_attunement = round(cur_trust * 0.7 + cur_familiarity * 0.3, 4)
        initiative_level = round(
            cur_trust * 0.4 + cur_interaction_freq * 0.3 + lt_bond * 0.3, 4
        )

        return InteractionPolicy(
            response_distance=response_distance,
            emotional_attunement=emotional_attunement,
            initiative_level=initiative_level,
        )

    # ============================================================
    # Legacy 路径（向后兼容，使用旧 relationship_state 输入）
    # ============================================================

    def _resolve_legacy(
        self,
        relationship_state: Any,
        identity: Dict[str, Any],
    ) -> CommunicationStyle:
        """从旧 relationship_state 推导（向后兼容）。

        当 snapshot 未提供时使用此路径。
        """
        state = self._normalize(relationship_state)

        source_version = self._detect_version(state)

        familiarity = self._safe_float(state, "familiarity", _DEFAULT_FAMILIARITY)
        trust = self._safe_float(state, "trust", _DEFAULT_TRUST)
        bond = self._safe_float(
            state, "bond_strength",
            self._safe_float(state, "bond", _DEFAULT_BOND)
        )
        activity = self._safe_float(
            state, "interaction_frequency",
            self._safe_float(state, "activity_level", _DEFAULT_ACTIVITY)
        )
        collaboration = self._safe_float(state, "collaboration", _DEFAULT_COLLABORATION)
        shared_history = self._safe_float(state, "shared_history", _DEFAULT_SHARED_HISTORY)
        relationship_stage = self._resolve_stage(state, familiarity, trust, bond)

        addressing = self._derive_addressing(identity, familiarity, trust, bond)
        tone = self._derive_tone(familiarity, trust, bond, activity)
        interaction = self._derive_interaction(familiarity, trust, bond, activity, collaboration)

        source = StyleSource(
            relationship_stage=relationship_stage,
            familiarity=familiarity,
            trust=trust,
            bond=bond,
            collaboration=collaboration,
            shared_history=shared_history,
            activity_level=activity,
            source_version=source_version,
        )

        return CommunicationStyle(
            addressing=addressing,
            tone=tone,
            interaction=interaction,
            source=source,
            generated_at=datetime.now().isoformat(),
        )

    # ============================================================
    # Legacy: 输入归一化
    # ============================================================

    @staticmethod
    def _normalize(raw: Any) -> Dict[str, Any]:
        """将任意 RelationshipState 归一化为 dict。"""
        if isinstance(raw, dict):
            return raw
        if raw is None:
            return {}
        if hasattr(raw, "get") and callable(raw.get):
            try:
                result = raw.get()
                if isinstance(result, dict):
                    return result
            except Exception:
                pass
        if hasattr(raw, "to_dict") and callable(raw.to_dict):
            try:
                result = raw.to_dict()
                if isinstance(result, dict):
                    return result
            except Exception:
                pass
        try:
            result = vars(raw)
            if isinstance(result, dict):
                return result
        except Exception:
            pass
        return {}

    @staticmethod
    def _safe_float(data: Dict[str, Any], key: str, default: float) -> float:
        """安全读取 float，缺失/非数字时返回默认值，clamp 到 [0, 1]"""
        try:
            val = float(data.get(key, default))
            return max(0.0, min(1.0, val))
        except (TypeError, ValueError):
            return max(0.0, min(1.0, default))

    @staticmethod
    def _detect_version(state: Dict[str, Any]) -> str:
        """检测数据源版本。"""
        ver = state.get("version", "")
        if ver:
            return str(ver)
        if "bond_strength" in state or "bond" in state:
            return "0.6"
        if "collaboration" in state:
            return "3.5.27"
        return "unknown"

    # ============================================================
    # Legacy: 关系阶段推断
    # ============================================================

    @staticmethod
    def _resolve_stage(
        state: Dict[str, Any],
        familiarity: float,
        trust: float,
        bond: float,
    ) -> str:
        """推断关系阶段。"""
        stage = state.get("relationship_stage", "")
        if isinstance(stage, str) and stage in (
            "initial", "developing", "stable", "deep_collaboration",
        ):
            return stage

        if trust >= 0.72 and bond >= 0.6 and familiarity >= 0.7:
            return "deep_collaboration"
        if trust >= 0.55 and familiarity >= 0.5:
            return "stable"
        if trust >= 0.25 or familiarity >= 0.25:
            return "developing"
        return "initial"

    # ============================================================
    # Legacy: 推导逻辑（保留 trust*0.7 补丁，仅用于向后兼容）
    # ============================================================

    @staticmethod
    def _derive_addressing(
        identity: Dict[str, Any],
        familiarity: float,
        trust: float,
        bond: float,
    ) -> AddressingPolicy:
        """[Legacy] 推导称呼策略。"""
        effective_bond = max(bond, trust * 0.7)
        strength = familiarity * trust * effective_bond
        strength = round(max(0.0, min(1.0, strength)), 4)

        preferred = identity.get("preferred_name", "")
        display = identity.get("display_name", "")
        forbidden = identity.get("forbidden_names", [])

        if not forbidden and isinstance(display, str) and display and preferred and display != preferred:
            forbidden = [display]

        return AddressingPolicy(
            preferred_name=str(preferred) if preferred else "",
            fallback_name=str(display) if display else "",
            forbidden_names=list(forbidden),
            addressing_strength=strength,
            source="derived" if strength > 0.0 else "default",
        )

    @staticmethod
    def _derive_tone(
        familiarity: float,
        trust: float,
        bond: float,
        activity: float,
    ) -> TonePolicy:
        """[Legacy] 推导语气参数。"""
        effective_bond = max(bond, trust * 0.7)

        warmth = round(familiarity * 0.7 + trust * 0.3, 4)
        intimacy = round(familiarity * 0.5 + effective_bond * 0.5, 4)
        formality = round(max(0.0, 1.0 - familiarity * 0.8), 4)
        playfulness = round(trust * 0.5 + activity * 0.5, 4)

        tags = CommunicationStyleResolver._build_tone_tags(
            warmth, intimacy, formality, playfulness
        )

        return TonePolicy(
            warmth=warmth,
            intimacy=intimacy,
            formality=formality,
            playfulness=playfulness,
            tone_tags=tags,
        )

    @staticmethod
    def _derive_interaction(
        familiarity: float,
        trust: float,
        bond: float,
        activity: float,
        collaboration: float,
    ) -> InteractionPolicy:
        """[Legacy] 推导互动行为参数。"""
        effective_bond = max(bond, collaboration)

        response_distance = round(familiarity * 0.5 + trust * 0.5, 4)
        emotional_attunement = round(trust * 0.7 + familiarity * 0.3, 4)
        initiative_level = round(
            trust * 0.4 + activity * 0.3 + effective_bond * 0.3, 4
        )

        return InteractionPolicy(
            response_distance=response_distance,
            emotional_attunement=emotional_attunement,
            initiative_level=initiative_level,
        )

    # ============================================================
    # 语气标签生成（snapshot 和 legacy 共用）
    # ============================================================

    @staticmethod
    def _build_tone_tags(
        warmth: float,
        intimacy: float,
        formality: float,
        playfulness: float,
    ) -> list:
        """根据数值生成中文语气标签，供 Prompt 渲染使用。"""
        tags = []

        if warmth >= 0.8:
            tags.append("温柔")
        elif warmth >= 0.6:
            tags.append("温暖")

        if intimacy >= 0.8:
            tags.append("贴近")
        elif intimacy >= 0.6:
            tags.append("亲近")

        if formality <= 0.2:
            tags.append("随意")
        elif formality <= 0.4:
            tags.append("自然")

        if playfulness >= 0.7:
            tags.append("活泼")
        elif playfulness >= 0.5:
            tags.append("轻松")

        return tags