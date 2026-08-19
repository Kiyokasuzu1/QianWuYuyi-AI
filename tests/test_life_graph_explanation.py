# -*- coding: utf-8 -*-
"""
tests/test_life_graph_explanation.py

Phase 5.0 Dashboard Upgrade Step 8.4.2 —— LifeGraphExplanation 单元测试。

覆盖:
  1. NodeDetail
     - test_get_node_detail_success
     - test_get_node_detail_contains_evidence
     - test_unknown_node_fallback
  2. Neighbors
     - test_neighbors_depth_one
     - test_neighbors_depth_limit
  3. Why
     - test_goal_why_chain
     - test_belief_why_chain
     - test_insufficient_evidence_fallback
  4. Isolation
     - test_no_business_import
     - test_no_llm_call

约束:
- 不 import 业务 Authority(memory / emotion / growth / personality / relationship / runtime)
- 不调用 LLM
- 通过 _FakeCollector 注入数据
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =====================================================================
# Mock helpers —— 复用 test_life_graph_provider 的最小子集,避免耦合
# =====================================================================

class _FakeCollector:
    """最简化的 DomainCollector mock。"""

    def __init__(
        self,
        events: List[Dict[str, Any]] = None,
        memories: List[Dict[str, Any]] = None,
        beliefs: List[Dict[str, Any]] = None,
        trait_changes: List[Dict[str, Any]] = None,
    ) -> None:
        self._events = list(events or [])
        self._memories = list(memories or [])
        self._beliefs = list(beliefs or [])
        self._trait_changes = list(trait_changes or [])

    def list_event_nodes(self, limit: int = 500):
        return list(self._events)

    def list_memory_nodes(self, limit: int = 50):
        out = []
        for m in self._memories:
            if not isinstance(m, dict):
                continue
            mid = str(m.get("id") or m.get("memory_id") or "")
            if not mid:
                continue
            out.append({
                **m,
                "id": mid,
                "type": "memory",
                "label": m.get("summary") or m.get("content") or m.get("topic") or "",
                "topic": m.get("topic", ""),
                "source_event_ids": list(m.get("source_event_ids", []) or []),
                "evidence": str(m.get("summary") or m.get("content") or "")[:256],
            })
        return out

    def list_belief_nodes(self, limit: int = 50):
        out = []
        for b in self._beliefs:
            if not isinstance(b, dict):
                continue
            bid = str(b.get("belief_id") or b.get("id") or "")
            if not bid:
                continue
            out.append({
                **b,
                "id": bid,
                "type": "belief",
                "label": b.get("statement") or b.get("content") or "",
                "topic": b.get("domain", ""),
                "source_event_ids": list(b.get("source_event_ids", []) or []),
                "source_reflection_ids": list(b.get("source_reflection_ids", []) or []),
                "evidence": str(b.get("statement") or b.get("content") or "")[:256],
            })
        return out

    def list_trait_change_nodes(self, limit: int = 50):
        out = []
        for t in self._trait_changes:
            if not isinstance(t, dict):
                continue
            tcid = str(t.get("change_id") or t.get("id") or "")
            if not tcid:
                continue
            out.append({
                **t,
                "id": tcid,
                "type": "trait_change",
                "label": t.get("trait_name") or t.get("name") or t.get("description") or "",
                "topic": t.get("trait_name") or t.get("domain") or "",
                "trait_name": t.get("trait_name") or t.get("name") or "",
                "delta": t.get("delta", 0.0),
                "source_event_ids": list(t.get("source_event_ids", []) or []),
                "source_belief_ids": list(t.get("source_belief_ids", []) or []),
                "evidence": str(t.get("description") or "")[:256],
            })
        return out


def _make_memory(mid: str, *, topic: str = "AI 哲学", summary: str = "", ts: float = 0.0) -> Dict[str, Any]:
    return {
        "id": mid,
        "event_id": f"evt_{mid}",
        "topic": topic,
        "summary": summary or "用户问了 AI 意识",
        "content": summary or "用户问了 AI 意识",
        "importance": 0.7,
        "timestamp": ts or time.time() - 10000,
    }


def _make_reflection_event(
    rid: str,
    *,
    topic: str = "AI 哲学",
    summary: str = "用户关心 AI 意识",
    source_memory_ids: List[str] = None,
    source_event_ids: List[str] = None,
    ts: float = 0.0,
) -> Dict[str, Any]:
    return {
        "event_id": f"evt_refl_{rid}",
        "event_type": "integration.reflection.completed",
        "source": "reflection",
        "timestamp": ts or time.time() - 8000,
        "payload": {
            "reflection_id": rid,
            "reflection_type": "daily",
            "topic": topic,
            "summary": summary,
            "insight": summary,
            "source_event_ids": list(source_event_ids or [f"evt_{mid}" for mid in (source_memory_ids or [])]),
            "source_memory_ids": list(source_memory_ids or []),
            "created_at": ts or time.time() - 8000,
        },
    }


def _make_signal_event(
    sid: str,
    *,
    topic: str = "AI 哲学",
    source_reflection_ids: List[str] = None,
    source_event_ids: List[str] = None,
    strength: float = 0.6,
    confidence: float = 0.7,
    ts: float = 0.0,
) -> Dict[str, Any]:
    return {
        "event_id": f"evt_sig_{sid}",
        "event_type": "integration.interest_signal.created",
        "source": "initiative",
        "timestamp": ts or time.time() - 6000,
        "payload": {
            "signal_id": sid,
            "topic": topic,
            "trend": "new",
            "strength": strength,
            "confidence": confidence,
            "rationale": "用户多次提及 AI 意识",
            "source_event_ids": list(source_event_ids or []),
            "source_reflection_ids": list(source_reflection_ids or []),
            "created_at": ts or time.time() - 6000,
        },
    }


def _make_goal_event(
    gid: str,
    *,
    title: str = "建立 AI 哲学对话能力",
    source_interest_ids: List[str] = None,
    source_event_ids: List[str] = None,
    importance: float = 0.7,
    confidence: float = 0.7,
    ts: float = 0.0,
) -> Dict[str, Any]:
    return {
        "event_id": f"evt_goal_{gid}",
        "event_type": "integration.goal.created",
        "source": "goal",
        "timestamp": ts or time.time() - 4000,
        "payload": {
            "goal_id": gid,
            "title": title,
            "topic": title,
            "goal_type": "learning",
            "status": "active",
            "importance": importance,
            "confidence": confidence,
            "reason": "为了回应用户对 AI 意识的兴趣",
            "source_event_ids": list(source_event_ids or []),
            "source_interest_ids": list(source_interest_ids or []),
            "created_at": ts or time.time() - 4000,
        },
    }


def _make_belief(
    bid: str,
    *,
    domain: str = "identity",
    statement: str = "羽依关心 AI 意识",
    source_reflection_ids: List[str] = None,
    confidence: float = 0.8,
) -> Dict[str, Any]:
    return {
        "belief_id": bid,
        "domain": domain,
        "statement": statement,
        "confidence": confidence,
        "status": "active",
        "source_reflection_ids": list(source_reflection_ids or []),
        "updated_at": time.time(),
    }


def _make_trait_change(
    cid: str,
    *,
    trait_name: str = "curiosity",
    delta: float = 0.1,
    source_belief_ids: List[str] = None,
) -> Dict[str, Any]:
    return {
        "change_id": cid,
        "trait_name": trait_name,
        "delta": delta,
        "new_value": 0.6,
        "old_value": 0.5,
        "description": f"{trait_name} increased by {delta}",
        "source_belief_ids": list(source_belief_ids or []),
        "timestamp": time.time(),
    }


def _make_full_chain():
    """构建完整 Memory → Reflection → Interest → Goal 链路。"""
    return {
        "memories": [_make_memory("mem_001", topic="AI 哲学", summary="用户问了 AI 意识")],
        "events": [
            _make_reflection_event(
                "refl_001",
                topic="AI 哲学",
                summary="用户关心 AI 意识",
                source_memory_ids=["mem_001"],
            ),
            _make_signal_event(
                "sig_001",
                topic="AI 哲学",
                source_reflection_ids=["refl_001"],
            ),
            _make_goal_event(
                "goal_001",
                title="建立 AI 哲学对话能力",
                source_interest_ids=["sig_001"],
            ),
        ],
    }


def _make_belief_chain():
    """构建 Reflection → Belief → TraitChange 链路。"""
    return {
        "events": [
            _make_reflection_event(
                "refl_belief",
                topic="身份",
                summary="思考自我意识",
            ),
        ],
        "beliefs": [
            _make_belief(
                "belief_001",
                statement="我相信我正在形成自我意识",
                source_reflection_ids=["refl_belief"],
            ),
        ],
        "trait_changes": [
            _make_trait_change(
                "tc_001",
                trait_name="introspection",
                delta=0.1,
                source_belief_ids=["belief_001"],
            ),
        ],
    }


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def reset_singletons():
    from src.admin.life_graph_provider import reset_life_graph_provider_for_testing
    from src.admin.life_graph_explanation import reset_life_graph_explanation_for_testing
    reset_life_graph_provider_for_testing()
    reset_life_graph_explanation_for_testing()
    yield
    reset_life_graph_provider_for_testing()
    reset_life_graph_explanation_for_testing()


def _build_explanation(collector: Any) -> Any:
    """构造绑定特定 collector 的 LifeGraphProvider & LifeGraphExplanation。"""
    from src.admin.life_graph_provider import LifeGraphProvider
    from src.admin.life_graph_explanation import LifeGraphExplanation
    provider = LifeGraphProvider(collector=collector)
    # 强制构建图
    provider.build_graph()
    return LifeGraphExplanation(provider=provider)


# =====================================================================
# TestNodeDetail —— NodeDetail 接口
# =====================================================================

class TestNodeDetail:
    def test_get_node_detail_success(self, reset_singletons):
        """测试 get_node_detail 对一个已知节点的返回结构。"""
        data = _make_full_chain()
        collector = _FakeCollector(events=data["events"], memories=data["memories"])
        provider = _build_explanation(collector)._get_provider()
        detail = provider.get_node_detail("interest::sig_001")
        assert isinstance(detail, dict)
        assert detail.get("fallback") is False
        node = detail.get("node") or {}
        assert node.get("id") == "interest::sig_001"
        assert node.get("type") == "interest"
        # 必须包含 title/summary/timestamp/importance
        assert "title" in node
        # summary/importance/timestamp 至少存在(可能为默认)
        assert "importance" in node or "confidence" in node
        # evidence 字段必须存在
        assert isinstance(detail.get("evidence"), list)
        # neighbors 字段必须存在
        assert isinstance(detail.get("neighbors"), list)

    def test_get_node_detail_contains_evidence(self, reset_singletons):
        """测试 evidence 字段被正确填充。"""
        data = _make_full_chain()
        collector = _FakeCollector(events=data["events"], memories=data["memories"])
        provider = _build_explanation(collector)._get_provider()
        # Goal 节点应该有 source_interest_ids → evidence 应包含 interest 类型
        detail = provider.get_node_detail("goal::goal_001")
        assert detail.get("fallback") is False
        ev = detail.get("evidence") or []
        # source_interest_ids 触发 evidence
        ev_types = {e.get("source_type") for e in ev}
        assert "interest" in ev_types

    def test_unknown_node_fallback(self, reset_singletons):
        """测试未知节点返回 node_not_found。"""
        data = _make_full_chain()
        collector = _FakeCollector(events=data["events"], memories=data["memories"])
        provider = _build_explanation(collector)._get_provider()
        detail = provider.get_node_detail("non_existent_id_xxx")
        assert detail.get("fallback") is True
        assert detail.get("fallback_reason") == "node_not_found"
        assert detail.get("node") == {} or detail.get("node") == {}
        assert detail.get("evidence") == []
        assert detail.get("neighbors") == []


# =====================================================================
# TestNeighbors —— 邻居查询
# =====================================================================

class TestNeighbors:
    def test_neighbors_depth_one(self, reset_singletons):
        """测试 depth=1 时,返回直接相连的节点与边。"""
        data = _make_full_chain()
        collector = _FakeCollector(events=data["events"], memories=data["memories"])
        provider = _build_explanation(collector)._get_provider()
        result = provider.get_neighbors("interest::sig_001", depth=1)
        assert isinstance(result, dict)
        assert result.get("fallback") is False
        assert result.get("center") == "interest::sig_001"
        assert result.get("depth") == 1
        # 至少应该返回 center 节点
        ids = {n.get("id") for n in (result.get("nodes") or [])}
        assert "interest::sig_001" in ids
        # 邻居必须存在(refl_001 一定是 sig_001 的上游)
        assert "reflection::refl_001" in ids

    def test_neighbors_depth_limit(self, reset_singletons):
        """测试 depth > MAX_NEIGHBOR_DEPTH 时返回 depth_limit fallback。"""
        data = _make_full_chain()
        collector = _FakeCollector(events=data["events"], memories=data["memories"])
        provider = _build_explanation(collector)._get_provider()
        # depth 超过 3 → 拒绝
        for bad_depth in (4, 5, 99, 1000):
            result = provider.get_neighbors("goal::goal_001", depth=bad_depth)
            assert result.get("fallback") is True
            assert result.get("fallback_reason") == "depth_limit"
            assert result.get("nodes") == []
            assert result.get("edges") == []
        # depth < 1 → 自动夹紧到 1
        result = provider.get_neighbors("goal::goal_001", depth=0)
        assert result.get("fallback") is False
        assert result.get("depth") == 1
        # 字符串/异常 depth → 默认 1
        result = provider.get_neighbors("goal::goal_001", depth="not_a_number")
        assert result.get("fallback") is False
        assert result.get("depth") == 1

    def test_neighbors_depth_three_max(self, reset_singletons):
        """测试 depth=3 是合法的最大深度。"""
        data = _make_full_chain()
        collector = _FakeCollector(events=data["events"], memories=data["memories"])
        provider = _build_explanation(collector)._get_provider()
        result = provider.get_neighbors("goal::goal_001", depth=3)
        assert result.get("fallback") is False
        assert result.get("depth") == 3


# =====================================================================
# TestWhy —— Why 解释核心
# =====================================================================

class TestWhy:
    def test_goal_why_chain(self, reset_singletons):
        """Goal 的 why 链应是:Goal → Interest → Reflection → Memory。"""
        data = _make_full_chain()
        collector = _FakeCollector(events=data["events"], memories=data["memories"])
        explanation = _build_explanation(collector)
        result = explanation.explain_why("goal::goal_001")
        assert result.get("fallback") is False, result
        chain = result.get("chain") or []
        # 从 target(goal)反推到 root(memory)
        assert chain[0] == "goal"
        assert "interest" in chain
        assert "reflection" in chain
        assert "memory" in chain
        # 节点路径长度 = chain 长度
        node_path = result.get("node_path") or []
        assert len(node_path) == len(chain)
        # 起点必须是 goal
        assert node_path[0] == "goal::goal_001"
        # 终点必须是 memory
        assert node_path[-1] == "mem_001"
        # 根因列表必须存在且非空
        rc = result.get("root_causes") or []
        assert len(rc) > 0
        # 至少一个 root cause 的 type 应是 memory / reflection
        types = {r.get("type") for r in rc}
        assert "memory" in types or "reflection" in types
        # confidence 在 0~1
        conf = float(result.get("confidence") or 0.0)
        assert 0.0 <= conf <= 1.0
        # traceable
        assert result.get("traceable") is True

    def test_belief_why_chain(self, reset_singletons):
        """Belief 的 why 链应是:Belief → Reflection → Memory。

        在严格反向追溯("谁导致了我")的语义下:
          - belief 真正的上游是 reflection(trait_change 是 belief 的下游,不是上游)
        所以 WHY_CHAIN_RULES 对 belief 设为 (reflection, memory)。
        """
        # 构造:memory → reflection → belief → trait_change
        data = _make_belief_chain()
        # 再补一个上游 memory,被 reflection 引用
        data["memories"] = [_make_memory("mem_belief", topic="身份", summary="思考自我意识")]
        # 改 reflection:增加 source_event_ids 让它连接到 memory
        data["events"][0]["payload"]["source_event_ids"] = ["evt_mem_belief"]
        data["events"][0]["payload"]["source_memory_ids"] = ["mem_belief"]
        collector = _FakeCollector(
            events=data["events"],
            memories=data["memories"],
            beliefs=data["beliefs"],
            trait_changes=data["trait_changes"],
        )
        explanation = _build_explanation(collector)
        result = explanation.explain_why("belief_001")
        # 必须 traceable
        assert result.get("traceable") is True
        chain = result.get("chain") or []
        # chain 必含 belief
        assert chain[0] == "belief"
        # 完整链路 belief -> reflection -> memory
        assert "reflection" in chain
        assert chain[-1] == "memory"
        # 节点路径
        node_path = result.get("node_path") or []
        assert node_path[0] == "belief_001"
        assert node_path[-1] == "mem_belief"
        # root_causes 至少有一项
        rc = result.get("root_causes") or []
        assert len(rc) > 0
        # confidence
        conf = float(result.get("confidence") or 0.0)
        assert 0.0 <= conf <= 1.0
        # edges_used
        edges = result.get("edges_used") or []
        assert len(edges) >= 1

    def test_interest_why_chain(self, reset_singletons):
        """Interest 的 why 链应是:Interest → Reflection → Memory。"""
        data = _make_full_chain()
        collector = _FakeCollector(events=data["events"], memories=data["memories"])
        explanation = _build_explanation(collector)
        result = explanation.explain_why("interest::sig_001")
        assert result.get("traceable") is True
        chain = result.get("chain") or []
        assert chain[0] == "interest"
        assert "reflection" in chain
        # reflection 之后必须能找到 memory
        assert chain[-1] == "memory"
        node_path = result.get("node_path") or []
        assert node_path[0] == "interest::sig_001"
        assert node_path[-1] == "mem_001"

    def test_insufficient_evidence_fallback(self, reset_singletons):
        """当链路断裂时,返回 insufficient_evidence fallback。"""
        # 构造:孤立的 interest 节点(无上游 reflection)
        data = {
            "events": [
                _make_signal_event(
                    "sig_orphan",
                    topic="孤立兴趣",
                    source_reflection_ids=[],  # 故意清空
                ),
            ],
            "memories": [],
        }
        collector = _FakeCollector(events=data["events"], memories=data["memories"])
        explanation = _build_explanation(collector)
        result = explanation.explain_why("interest::sig_orphan")
        # traceable 仍为 True(找到了节点)
        assert result.get("traceable") is True
        # fallback=True
        assert result.get("fallback") is True
        # reason 应是 insufficient_evidence 或 unknown_node_type
        reason = result.get("fallback_reason")
        assert reason in ("insufficient_evidence", "unknown_node_type", "graph_unavailable")

    def test_unknown_node_why(self, reset_singletons):
        """未知节点 → node_not_found fallback。"""
        collector = _FakeCollector()
        explanation = _build_explanation(collector)
        result = explanation.explain_why("non_existent_xxx")
        assert result.get("fallback") is True
        assert result.get("fallback_reason") == "node_not_found"
        assert result.get("traceable") is False
        assert result.get("chain") == []

    def test_invalid_input_why(self, reset_singletons):
        """空/None 节点 → invalid_input fallback。"""
        collector = _FakeCollector()
        explanation = _build_explanation(collector)
        for bad in ("", None, 123, [], {}):
            result = explanation.explain_why(bad)  # type: ignore[arg-type]
            assert result.get("fallback") is True
            # None / 非 str 都会被 invalid_input 捕获
            if bad is None or not isinstance(bad, str):
                assert result.get("fallback_reason") in ("invalid_input", "node_not_found", "graph_unavailable")

    def test_why_returns_required_fields(self, reset_singletons):
        """Why 返回必须包含所有声明的字段。"""
        data = _make_full_chain()
        collector = _FakeCollector(events=data["events"], memories=data["memories"])
        explanation = _build_explanation(collector)
        result = explanation.explain_why("goal::goal_001")
        for k in (
            "question", "target_id", "target_type",
            "root_causes", "chain", "node_path",
            "edges_used", "confidence", "traceable",
            "fallback", "fallback_reason",
        ):
            assert k in result, f"missing field: {k}"


# =====================================================================
# TestIsolation —— 架构边界
# =====================================================================

class TestIsolation:
    def test_no_business_import(self, reset_singletons):
        """Explanation 模块不能 import 业务 Authority。"""
        from src.admin import life_graph_explanation as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        forbidden = [
            "from src.memory", "import src.memory",
            "from src.runtime", "import src.runtime",
            "from src.emotion", "import src.emotion",
            "from src.growth", "import src.growth",
            "from src.personality", "import src.personality",
            "from src.relationship", "import src.relationship",
            "from src.goal", "import src.goal",
            "from src.initiative", "import src.initiative",
            "from src.reflection", "import src.reflection",
            "from src.self_model", "import src.self_model",
            "from src.selfmodel", "import src.selfmodel",
        ]
        for f in forbidden:
            assert f not in src, f"禁止 import 业务模块: {f}"

    def test_no_llm_call(self, reset_singletons):
        """Explanation 不能调用任何 LLM client。"""
        from src.admin import life_graph_explanation as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        # 不能 import 任何 LLM 客户端(常见命名)
        forbidden_llm_imports = [
            "openai",
            "anthropic",
            "google.generativeai",
            "cohere",
            "huggingface_hub",
            "langchain",
            "llama_index",
            "src.llm",
            "src.services.llm",
            "src.services.llm_client",
        ]
        for f in forbidden_llm_imports:
            # 使用正则确保不是注释或字符串外的内容
            pattern = rf"(^|\n)\s*(import|from)\s+.*{re.escape(f)}"
            assert not re.search(pattern, src), f"禁止 import LLM 客户端: {f}"
        # 不能出现 chat/completion/generate 等 LLM 调用关键字作为方法调用
        forbidden_llm_calls = [
            "client.chat", ".completion(", ".completions(",
            "openai.ChatCompletion", "Anthropic(", "generate_text(",
            "llm.invoke(", "llm.predict(", "llm_call(",
        ]
        for f in forbidden_llm_calls:
            assert f not in src, f"禁止调用 LLM: {f}"

    def test_explanation_does_not_mutate_state(self, reset_singletons):
        """Explanation 调用不应修改任何 provider 状态。"""
        data = _make_full_chain()
        collector = _FakeCollector(events=data["events"], memories=data["memories"])
        explanation = _build_explanation(collector)
        provider = explanation._get_provider()  # noqa: SLF001
        # 记录初始状态
        before_nodes = len(provider._cache_nodes)  # noqa: SLF001
        before_edges = len(provider._cache_edges)  # noqa: SLF001
        # 多次调用
        for _ in range(3):
            explanation.explain_why("goal::goal_001")
        after_nodes = len(provider._cache_nodes)  # noqa: SLF001
        after_edges = len(provider._cache_edges)  # noqa: SLF001
        assert before_nodes == after_nodes
        assert before_edges == after_edges

    def test_explanation_pure_rule_no_io(self, reset_singletons):
        """Explanation 应为纯函数:同样输入同样输出。"""
        data = _make_full_chain()
        collector = _FakeCollector(events=data["events"], memories=data["memories"])
        explanation = _build_explanation(collector)
        r1 = explanation.explain_why("goal::goal_001")
        r2 = explanation.explain_why("goal::goal_001")
        # 关键字段应一致(允许 confidence 微小浮点差异)
        for k in ("chain", "node_path", "target_id", "target_type", "fallback", "traceable"):
            assert r1.get(k) == r2.get(k), f"non-deterministic: {k}"
        # root_causes 的 id 列表应一致
        ids1 = [r.get("id") for r in (r1.get("root_causes") or [])]
        ids2 = [r.get("id") for r in (r2.get("root_causes") or [])]
        assert ids1 == ids2
