"""
Phase B.2.5 — Relationship Evolution

职责：
从 GrowthHistory 提取长期 relationship 相关的成长事件，
缓慢更新 RelationshipState（trust / familiarity / interaction_pattern）。

调用：
    PersonalityGrowthHistory.records
        ↓
    RelationshipEvolution.evaluate()
        ↓
    RelationshipState update (trust / familiarity / bond / history)

安全约束：
- 不读取原始聊天，只读 GrowthHistory
- 关系维度变化通过 RelationshipState 的成熟度约束保护
- 单次变化限幅（trust_max_delta 等）
- 异常隔离
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# 关系维度的最大单次变化（保守）
MAX_TRUST_DELTA = 0.03
MAX_FAMILIARITY_DELTA = 0.03
MAX_BOND_DELTA = 0.02
MAX_HISTORY_DELTA = 0.03

# 关系 trait 名集合（哪些 GrowthRecord 的 dimension 算作关系信号）
RELATIONSHIP_TRAITS = frozenset({
    "trust", "familiarity", "interaction_pattern",
    "bond", "shared_history", "social_need",
    "warmth", "compassion", "empathy",
})

# 关系事件最低 evidence 数量（防止一次聊天就提升关系）
MIN_RELATIONSHIP_EVIDENCE = 2


class RelationshipEvolution:
    """
    Phase B.2.5 Relationship Evolution
    """

    def __init__(
        self,
        relationship_state: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        Args:
            relationship_state: RelationshipState 实例（可选）
            config: 自定义配置
        """
        self.relationship_state = relationship_state
        self.config = config or {}
        self.max_trust_delta: float = float(self.config.get("max_trust_delta", MAX_TRUST_DELTA))
        self.max_familiarity_delta: float = float(
            self.config.get("max_familiarity_delta", MAX_FAMILIARITY_DELTA)
        )
        self.max_bond_delta: float = float(self.config.get("max_bond_delta", MAX_BOND_DELTA))
        self.max_history_delta: float = float(
            self.config.get("max_history_delta", MAX_HISTORY_DELTA)
        )
        self.min_evidence: int = int(
            self.config.get("min_evidence", MIN_RELATIONSHIP_EVIDENCE)
        )

    # ============================================================
    # 提取 relationship 相关 evidence
    # ============================================================
    def extract_signals(
        self,
        history: Any,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        从 PersonalityGrowthHistory.records 提取与关系相关的信号。
        Returns: {trait: [evidence_dict, ...]}
        """
        signals: Dict[str, List[Dict[str, Any]]] = {}
        try:
            records = history.all() if hasattr(history, "all") else []
        except Exception:
            records = []

        for rec in records:
            try:
                dims = rec.get("affected_dimensions", []) or []
                changes = rec.get("changes", {}) or {}
                confidence = float(rec.get("confidence", 0.0) or 0.0)
                for dim in dims:
                    if dim in RELATIONSHIP_TRAITS:
                        change = changes.get(dim, {}) if isinstance(changes, dict) else {}
                        delta = float(change.get("delta", 0.0) or 0.0)
                        signals.setdefault(dim, []).append({
                            "record_id": rec.get("record_id", ""),
                            "timestamp": rec.get("timestamp", ""),
                            "delta": delta,
                            "confidence": confidence,
                        })
            except Exception:
                continue
        return signals

    # ============================================================
    # compute_deltas: 计算需要应用到 RelationshipState 的 delta
    # ============================================================
    def compute_deltas(
        self,
        signals: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, float]:
        """
        从 signals 计算关系维度的累积 delta（限幅）。
        Returns: {trait: delta}  （trust / familiarity / bond / shared_history）
        """
        deltas: Dict[str, float] = {}
        try:
            # trust
            trust_items = signals.get("trust", [])
            if len(trust_items) >= self.min_evidence:
                d = sum(max(0.0, float(x.get("delta", 0.0))) for x in trust_items)
                deltas["trust"] = round(min(self.max_trust_delta, d), 4)

            # familiarity
            fam_items = signals.get("familiarity", []) + signals.get("social_need", [])
            if len(fam_items) >= self.min_evidence:
                d = sum(max(0.0, float(x.get("delta", 0.0))) for x in fam_items)
                deltas["familiarity"] = round(min(self.max_familiarity_delta, d), 4)

            # bond
            bond_items = (
                signals.get("bond", [])
                + signals.get("warmth", [])
                + signals.get("compassion", [])
                + signals.get("empathy", [])
            )
            if len(bond_items) >= self.min_evidence:
                d = sum(max(0.0, float(x.get("delta", 0.0))) for x in bond_items)
                deltas["bond"] = round(min(self.max_bond_delta, d), 4)

            # shared_history
            hist_items = signals.get("shared_history", []) + signals.get("interaction_pattern", [])
            if len(hist_items) >= self.min_evidence:
                d = sum(max(0.0, float(x.get("delta", 0.0))) for x in hist_items)
                deltas["shared_history"] = round(min(self.max_history_delta, d), 4)
        except Exception as e:
            logger.warning("[relationship_compute_deltas_failed] %s", e)
        return deltas

    # ============================================================
    # evaluate: 整体入口
    # ============================================================
    def evaluate(
        self,
        history: Any,
    ) -> Dict[str, Any]:
        """
        计算 relationship 维度变化量，返回：
        {
            "signals": {...},
            "deltas": {trait: delta},
            "applied": bool,
            "reason": str,
        }
        """
        result: Dict[str, Any] = {
            "signals": {},
            "deltas": {},
            "applied": False,
            "reason": "",
        }
        try:
            signals = self.extract_signals(history)
            deltas = self.compute_deltas(signals)
            result["signals"] = {k: len(v) for k, v in signals.items()}
            result["deltas"] = deltas

            # 如果提供了 relationship_state 则应用
            if self.relationship_state is not None and deltas:
                self._apply_to_state(deltas)
                result["applied"] = True

            if deltas:
                result["reason"] = (
                    "relationship_evolution:" + ";".join(
                        f"{k}+{v:.3f}" for k, v in deltas.items()
                    )
                )
            else:
                result["reason"] = "no_relationship_evidence"

            logger.info(
                "[relationship_evaluated] deltas=%s applied=%s",
                deltas,
                result["applied"],
            )
            return result
        except Exception as e:
            logger.exception("[relationship_evolution_failed] %s", e)
            result["reason"] = f"exception:{e}"
            return result

    # ============================================================
    # 私有：写入 RelationshipState（受其内部约束保护）
    # ============================================================
    def _apply_to_state(self, deltas: Dict[str, float]) -> None:
        rs = self.relationship_state
        if rs is None:
            return
        try:
            if "trust" in deltas and hasattr(rs, "update_trust"):
                rs.update_trust(deltas["trust"])
            if "familiarity" in deltas and hasattr(rs, "update_familiarity"):
                rs.update_familiarity(deltas["familiarity"])
            if "bond" in deltas and hasattr(rs, "update_bond"):
                rs.update_bond(deltas["bond"])
            if "shared_history" in deltas and hasattr(rs, "update_history"):
                rs.update_history(deltas["shared_history"])
        except Exception as e:
            logger.warning("[relationship_apply_failed] %s", e)
