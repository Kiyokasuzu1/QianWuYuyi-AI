# -*- coding: utf-8 -*-
"""
src/admin/life_graph_explanation.py

Phase 5.0 Dashboard Upgrade Step 8.4.2 —— LifeGraph 解释层(Why)。

职责:
- 提供"为什么?"问题的纯规则解释
- 不调用 LLM
- 不写入任何业务状态
- 严格基于 LifeGraphProvider 构建的图,反向追溯 root cause

核心规则(只读 / 纯规则):
    Goal:
        Goal
         ↓
        Interest
         ↓
        Reflection
         ↓
        Memory

    Belief:
        Belief
         ↓
        TraitChange
         ↓
        Reflection
         ↓
        Memory

    Interest:
        Interest
         ↓
        Reflection
         ↓
        Memory

    TraitChange:
        TraitChange
         ↓
        Belief
         ↓
        Reflection
         ↓
        Memory

无法找到完整链路 → fallback=true, fallback_reason="insufficient_evidence"

数据流:
    Frontend
        ↓
    LifeGraphRouter
        ↓
    LifeGraphExplanation.explain_why(node_id)
        ↓
    LifeGraphProvider(只读)
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple

from src.admin.life_graph_provider import (
    ALL_NODE_TYPES,
    NODE_TYPE_ACTION,
    NODE_TYPE_BELIEF,
    NODE_TYPE_GOAL,
    NODE_TYPE_INTEREST,
    NODE_TYPE_MEMORY,
    NODE_TYPE_REFLECTION,
    NODE_TYPE_TRAIT_CHANGE,
    LifeGraphProvider,
    get_life_graph_provider,
)

logger = logging.getLogger(__name__)


# ============================================================
# 链路规则
# ============================================================

# Goal / Belief / Interest / TraitChange / Action / Reflection / Memory 的下游 → 上游期望链路
# 元组:(中心类型, 期望上游类型序列)
# 注:链式追溯的是"上游"(谁导致了我),而不是"下游"(我影响了谁)。
# 因此对 belief 而言,trait_change 是 belief 的下游,不是上游,这里 belief 的上游直接是 (reflection, memory)。
WHY_CHAIN_RULES: Dict[str, Tuple[str, ...]] = {
    NODE_TYPE_GOAL: (NODE_TYPE_INTEREST, NODE_TYPE_REFLECTION, NODE_TYPE_MEMORY),
    NODE_TYPE_BELIEF: (NODE_TYPE_REFLECTION, NODE_TYPE_MEMORY),
    NODE_TYPE_INTEREST: (NODE_TYPE_REFLECTION, NODE_TYPE_MEMORY),
    NODE_TYPE_TRAIT_CHANGE: (NODE_TYPE_BELIEF, NODE_TYPE_REFLECTION, NODE_TYPE_MEMORY),
    NODE_TYPE_ACTION: (NODE_TYPE_GOAL, NODE_TYPE_INTEREST, NODE_TYPE_REFLECTION, NODE_TYPE_MEMORY),
    NODE_TYPE_REFLECTION: (NODE_TYPE_MEMORY,),
    NODE_TYPE_MEMORY: (),  # Memory 是 root
}


# ============================================================
# 工具
# ============================================================

def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        v = float(value)
        return v if v == v else 0.0
    except (TypeError, ValueError):
        return 0.0


def _clip(s: Any, n: int = 256) -> str:
    if s is None:
        return ""
    try:
        v = str(s)
    except Exception:
        return ""
    if len(v) > n:
        return v[:n]
    return v


# ============================================================
# Explanation Provider
# ============================================================

class LifeGraphExplanation:
    """
    纯规则 Why 解释器。
    注入:provider(LifeGraphProvider),测试可注入。
    """

    def __init__(self, provider: Optional[LifeGraphProvider] = None) -> None:
        self._provider = provider

    def _get_provider(self) -> LifeGraphProvider:
        if self._provider is not None:
            return self._provider
        try:
            self._provider = get_life_graph_provider()
        except Exception as exc:  # noqa: BLE001
            logger.debug("LifeGraphExplanation: Provider 不可用: %s", exc)
            self._provider = None
        return self._provider

    def _ensure_graph(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], bool, str]:
        provider = self._get_provider()
        if provider is None:
            return [], [], False, "provider_unavailable"
        try:
            provider._ensure_loaded()  # noqa: SLF001  (内部方法,可接受)
        except Exception as exc:  # noqa: BLE001
            return [], [], False, f"ensure_loaded_error:{type(exc).__name__}"
        try:
            with provider._lock:  # noqa: SLF001
                nodes = list(provider._cache_nodes)  # noqa: SLF001
                edges = list(provider._cache_edges)  # noqa: SLF001
                last_ok = provider._last_build_ok  # noqa: SLF001
                last_err = provider._last_error  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            return [], [], False, f"provider_lock_error:{type(exc).__name__}"
        if not nodes and not last_ok:
            return [], [], False, last_err or "life_graph_unavailable"
        return nodes, edges, True, ""

    def _build_adj(
        self,
        edges: List[Dict[str, Any]],
        nodes: List[Dict[str, Any]],
    ) -> Dict[str, List[Tuple[str, Dict[str, Any]]]]:
        """构建反向邻接表(target → source),即"上游"。"""
        node_ids: Set[str] = {str(n.get("id", "") or "") for n in nodes if isinstance(n, dict)}
        adj: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {nid: [] for nid in node_ids}
        for e in edges or []:
            if not isinstance(e, dict):
                continue
            s = str(e.get("source", "") or "")
            t = str(e.get("target", "") or "")
            if s in node_ids and t in node_ids:
                # t → s(谁促成了 t)
                adj.setdefault(t, []).append((s, e))
        return adj

    def _node_strength(self, node: Dict[str, Any]) -> float:
        """节点 strength 推断:importance/confidence/priority 归一化。"""
        if not isinstance(node, dict):
            return 0.5
        candidates = [
            _to_float(node.get("importance")),
            _to_float(node.get("confidence")),
            _to_float(node.get("priority")),
            _to_float(node.get("delta")),
        ]
        # 取绝对值最大的(表示变化幅度/重要性)
        if not candidates:
            return 0.5
        s = max(candidates, key=lambda x: abs(x))
        if s == 0.0:
            return 0.5
        # 归一化到 [0.1, 1.0]
        norm = abs(s) if abs(s) <= 1.0 else min(1.0, abs(s) / 2.0)
        return max(0.1, min(1.0, norm))

    def _pick_upstream(
        self,
        current_id: str,
        expected_type: str,
        adj: Dict[str, List[Tuple[str, Dict[str, Any]]]],
        id_to_node: Dict[str, Dict[str, Any]],
    ) -> Optional[str]:
        """
        从 current_id 的所有上游中,选择类型匹配 expected_type 的最强节点。
        若多个同类型,选择 strength 最高的;若都不匹配,返回 None。
        """
        ups = adj.get(current_id, [])
        candidates: List[Tuple[float, str]] = []
        for upstream_id, _e in ups:
            n = id_to_node.get(upstream_id)
            if not n:
                continue
            if str(n.get("type", "") or "") != expected_type:
                continue
            candidates.append((self._node_strength(n), upstream_id))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    def explain_why(self, node_id: str) -> Dict[str, Any]:
        """
        解释"为什么?"。
        对中心节点,沿 WHY_CHAIN_RULES 反向追溯 root cause。

        Returns:
            {
                "question": "why",
                "target_id": str,
                "target_type": str,
                "root_causes": [{type, id, summary, strength}, ...],
                "chain": [type names from root to target],
                "node_path": [ids from root to target],
                "edges_used": [{source, target, relation, evidence}],
                "confidence": float,
                "traceable": True,
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        if not isinstance(node_id, str) or not node_id:
            return {
                "question": "why",
                "target_id": "",
                "target_type": "",
                "root_causes": [],
                "chain": [],
                "node_path": [],
                "edges_used": [],
                "confidence": 0.0,
                "traceable": False,
                "fallback": True,
                "fallback_reason": "invalid_input",
            }
        nodes, edges, ok, err = self._ensure_graph()
        if not ok:
            return {
                "question": "why",
                "target_id": node_id,
                "target_type": "",
                "root_causes": [],
                "chain": [],
                "node_path": [],
                "edges_used": [],
                "confidence": 0.0,
                "traceable": False,
                "fallback": True,
                "fallback_reason": err or "graph_unavailable",
            }
        # 解析 id
        provider = self._get_provider()
        try:
            candidates = provider._resolve_id_candidates(node_id, nodes)  # noqa: SLF001
        except Exception:
            candidates = []
        if not candidates:
            return {
                "question": "why",
                "target_id": node_id,
                "target_type": "",
                "root_causes": [],
                "chain": [],
                "node_path": [],
                "edges_used": [],
                "confidence": 0.0,
                "traceable": False,
                "fallback": True,
                "fallback_reason": "node_not_found",
            }
        target_id = candidates[0]
        id_to_node = {n.get("id"): n for n in nodes if isinstance(n, dict) and n.get("id")}
        target = id_to_node.get(target_id) or {}
        target_type = str(target.get("type", "") or "")
        if not target_type or target_type not in ALL_NODE_TYPES:
            return {
                "question": "why",
                "target_id": target_id,
                "target_type": target_type,
                "root_causes": [],
                "chain": [],
                "node_path": [],
                "edges_used": [],
                "confidence": 0.0,
                "traceable": False,
                "fallback": True,
                "fallback_reason": "unknown_node_type",
            }
        # 构造反向邻接表(target → source)
        adj = self._build_adj(edges, nodes)
        # 沿规则反向
        expected_upstream: Tuple[str, ...] = WHY_CHAIN_RULES.get(target_type, ())
        # 链路构建:从 target 反推到 root
        chain_types: List[str] = [target_type]
        node_path: List[str] = [target_id]
        edges_used: List[Dict[str, Any]] = []
        strengths: List[float] = []
        current_id = target_id
        for upstream_type in expected_upstream:
            picked = self._pick_upstream(current_id, upstream_type, adj, id_to_node)
            if picked is None:
                # 链路断裂 → 不足证据
                return {
                    "question": "why",
                    "target_id": target_id,
                    "target_type": target_type,
                    "root_causes": self._build_root_causes(node_path, id_to_node),
                    "chain": chain_types,
                    "node_path": node_path,
                    "edges_used": edges_used,
                    "confidence": 0.3,
                    "traceable": True,
                    "fallback": True,
                    "fallback_reason": "insufficient_evidence",
                }
            # 边(从 picked → current_id)
            edge_used: Optional[Dict[str, Any]] = None
            for sid, e in adj.get(current_id, []):
                if sid == picked and isinstance(e, dict):
                    edge_used = {
                        "source": picked,
                        "target": current_id,
                        "relation": str(e.get("relation", "") or ""),
                        "evidence": str(e.get("evidence", "") or ""),
                        "source_event_ids": list(e.get("source_event_ids", []) or []),
                    }
                    break
            if edge_used is None:
                # 兜底:无原始边(理论上不该出现,因为我们刚通过 adj 找到)
                edge_used = {
                    "source": picked,
                    "target": current_id,
                    "relation": f"upstream_to_{current_id}",
                    "evidence": "reverse_adj",
                    "source_event_ids": [],
                }
            edges_used.append(edge_used)
            node_path.append(picked)
            chain_types.append(upstream_type)
            picked_node = id_to_node.get(picked, {})
            strengths.append(self._node_strength(picked_node))
            current_id = picked
        # 计算 confidence:链路每一步的 strength 几何平均
        if strengths:
            prod = 1.0
            for s in strengths:
                prod *= max(0.05, min(1.0, s))
            confidence = prod ** (1.0 / len(strengths))
        else:
            confidence = 0.5
        # 不足证据兜底:链路 < 1 步(只有 target 自身)
        if len(node_path) < 2:
            return {
                "question": "why",
                "target_id": target_id,
                "target_type": target_type,
                "root_causes": self._build_root_causes(node_path, id_to_node),
                "chain": chain_types,
                "node_path": node_path,
                "edges_used": edges_used,
                "confidence": confidence,
                "traceable": True,
                "fallback": True,
                "fallback_reason": "insufficient_evidence",
            }
        return {
            "question": "why",
            "target_id": target_id,
            "target_type": target_type,
            "root_causes": self._build_root_causes(node_path, id_to_node),
            "chain": chain_types,
            "node_path": node_path,
            "edges_used": edges_used,
            "confidence": float(max(0.0, min(1.0, confidence))),
            "traceable": True,
            "fallback": False,
            "fallback_reason": None,
        }

    def _build_root_causes(
        self,
        node_path: List[str],
        id_to_node: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """从 node_path 提取所有上游节点作为 root causes。"""
        out: List[Dict[str, Any]] = []
        for nid in node_path[:-1]:  # 排除 target 自身
            n = id_to_node.get(nid)
            if not n:
                continue
            out.append({
                "type": str(n.get("type", "") or ""),
                "id": str(nid),
                "summary": _clip(
                    n.get("label") or n.get("topic") or n.get("evidence") or "",
                    256,
                ),
                "strength": self._node_strength(n),
            })
        return out


# ============================================================
# 模块级单例
# ============================================================

_explanation_instance: Optional[LifeGraphExplanation] = None
_explanation_lock = None  # 用 dict 实现简单 lock


def _get_explanation_lock():
    global _explanation_lock
    if _explanation_lock is None:
        import threading
        _explanation_lock = threading.Lock()
    return _explanation_lock


def get_life_graph_explanation() -> LifeGraphExplanation:
    global _explanation_instance
    if _explanation_instance is None:
        with _get_explanation_lock():
            if _explanation_instance is None:
                _explanation_instance = LifeGraphExplanation()
    return _explanation_instance


def reset_life_graph_explanation_for_testing() -> None:
    global _explanation_instance
    with _get_explanation_lock():
        _explanation_instance = None


__all__ = [
    "LifeGraphExplanation",
    "get_life_graph_explanation",
    "reset_life_graph_explanation_for_testing",
    "WHY_CHAIN_RULES",
]
