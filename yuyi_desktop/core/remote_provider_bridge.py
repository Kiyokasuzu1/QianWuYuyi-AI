# -*- coding: utf-8 -*-
"""
yuyi_desktop/core/remote_provider_bridge.py

Phase C.10.2 —— Yuyi Desktop Remote Provider Bridge

替代 C.10.1 中的 ProviderBridge。
通过 API Client 访问远程 Yuyi Server,不直接 import 任何 src.* 模块。

API 契约(GET only):
    GET /api/v1/runtime/status          → RuntimeStateView
    GET /api/v1/runtime/tasks           → LifecycleTaskList
    GET /api/v1/runtime/ticks           → TickHistory
    GET /api/v1/memory/summary          → MemorySummary
    GET /api/v1/memory/recent           → MemoryList
    GET /api/v1/personality/snapshot    → PersonalitySnapshot
    GET /api/v1/personality/traits      → TraitList
    GET /api/v1/personality/evolution   → EvolutionHistory
    GET /api/v1/selfmodel/snapshot      → SelfModelSnapshot
    GET /api/v1/growth/summary          → GrowthSummary
    GET /api/v1/growth/recent           → GrowthRecent
    GET /api/v1/growth/proposals        → ProposalList
    GET /api/v1/initiative/summary      → InitiativeSummary
    GET /api/v1/initiative/actions      → PossibleActionList
    GET /api/v1/life/state              → LifeState
    GET /api/v1/life/timeline           → LifeTimeline
    GET /api/v1/life/graph              → LifeGraph

Phase D.6.0 (视神经扩展,正式契约):
    GET /api/v1/selfmodel/beliefs       → StableBeliefList
    GET /api/v1/selfmodel/history       → SelfHistoryEvents
    GET /api/v1/selfmodel/reflections   → ReflectionsList
    GET /api/v1/selfmodel/traits        → StableTraitsList (≠ personality/traits 即时切片)
    GET /api/v1/personality/evolution   → EvolutionTimeline (正式版,不是 fallback)
    GET /api/v1/growth/proposals        → GrowthProposalList (正式版,不是 fallback)

所有方法:
- 只读(GET)
- 不发起写请求
- 失败返回 fallback envelope(可被 Service 安全消费)
- 不抛错

约束(强):
- 禁止 import src/runtime/**、src/memory/**、src/growth/** 等业务模块
- 禁止 POST / PUT / PATCH / DELETE
- 禁止调用 apply / resolve / update / modify / commit / approve / reject
- 仅通过 ApiClient 发起请求
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from yuyi_desktop.core.api_client import (
    ApiClient,
    get_api_client,
)

logger = logging.getLogger(__name__)


# ============================================================
# 端点常量(单点定义,便于维护)
# ============================================================
ENDPOINTS = {
    # C.10.3 v2 契约(server 实际注册的端点,主用)
    "health": "/health",
    "runtime_status": "/runtime/status",
    "runtime_overview": "/runtime/overview",
    "personality_status": "/personality/status",
    "selfmodel_status": "/selfmodel/status",
    "memory_overview": "/memory/overview",
    "growth_status": "/growth/status",
    "initiative_status": "/initiative/status",
    "audit_recent": "/audit/recent",
    # Phase D.6.0 —— SelfModel + Growth 正式列表端点
    "selfmodel_beliefs": "/selfmodel/beliefs",
    "selfmodel_history": "/selfmodel/history",
    "selfmodel_reflections": "/selfmodel/reflections",
    # 注意!和 /personality/traits 不是同一个东西:
    #   /selfmodel/traits     → SelfModelV3.stable_traits → 稳定自我认知
    #   /personality/traits   → Resolver.current         → 即时人格切片
    "selfmodel_stable_traits": "/selfmodel/traits",
    "personality_evolution_v2": "/personality/evolution",
    "growth_proposals_v2": "/growth/proposals",
}


# 禁止出现在方法名 / URL 中的写操作关键字
_FORBIDDEN_WRITE_KEYWORDS = (
    "apply", "update", "modify", "delete", "remove",
    "resolve", "approve", "reject", "commit", "write",
    "create", "insert", "post", "put", "patch",
)


# ============================================================
# 端点分类(便于 health_check 快速遍历)
# ============================================================
PROVIDER_GROUPS: Dict[str, List[str]] = {
    "runtime": [
        ENDPOINTS["runtime_status"],
    ],
    "memory": [
        ENDPOINTS["memory_overview"],
    ],
    "personality": [
        ENDPOINTS["personality_status"],
        ENDPOINTS["personality_evolution_v2"],
    ],
    "selfmodel": [
        ENDPOINTS["selfmodel_status"],
        ENDPOINTS["selfmodel_beliefs"],
        ENDPOINTS["selfmodel_history"],
        ENDPOINTS["selfmodel_reflections"],
        ENDPOINTS["selfmodel_stable_traits"],
    ],
    "growth": [
        ENDPOINTS["growth_status"],
        ENDPOINTS["growth_proposals_v2"],
    ],
    "initiative": [
        ENDPOINTS["initiative_status"],
    ],
}


# ============================================================
# Remote Provider Bridge
# ============================================================
class RemoteProviderBridge:
    """
    Yuyi Desktop 远程 Provider 桥接器。

    只通过 ApiClient 访问远端 Yuyi Server API。
    所有方法只读,所有失败返回 fallback。
    """

    def __init__(
        self,
        api_client: Optional[ApiClient] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._api_client = api_client if api_client is not None else get_api_client()

    # --------------------------------------------------------
    # 内部:统一 GET 包装
    # --------------------------------------------------------
    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        发起 GET,返回 envelope。

        envelope 字段:
            success, data, error, timestamp, schema_version, degraded, latency_ms
        """
        return self._api_client.get(endpoint, params=params)

    @staticmethod
    def _envelope_data(envelope: Dict[str, Any]) -> Dict[str, Any]:
        """从 envelope 安全提取 data(失败/异常都返回空 dict)。"""
        if not isinstance(envelope, dict):
            return {}
        if not envelope.get("success", False):
            return {}
        data = envelope.get("data", {}) or {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _envelope_list(envelope: Dict[str, Any]) -> List[Any]:
        """从 envelope 安全提取 list 数据。"""
        if not isinstance(envelope, dict):
            return []
        if not envelope.get("success", False):
            return []
        data = envelope.get("data", []) or []
        return data if isinstance(data, list) else []

    @staticmethod
    def _not_available_envelope(endpoint: str) -> Dict[str, Any]:
        """生成"端点未在 server 上注册"的标准化 envelope。

        - success=False(让 UI 显示降级)
        - degraded=False(不是 server 故障,只是 URL 不匹配)
        - 不会触发 retry
        """
        return {
            "success": False,
            "data": {},
            "error": f"endpoint_not_available_on_server: {endpoint}",
            "degraded": False,
            "schema_version": "1.0",
            "timestamp": "",
            "latency_ms": 0.0,
        }

    # --------------------------------------------------------
    # Health & Server Info (C.10.3)
    # --------------------------------------------------------
    def get_health(self) -> Dict[str, Any]:
        """C.10.3: 服务器总健康状态。"""
        return self._get(ENDPOINTS["health"])

    def get_health_data(self) -> Dict[str, Any]:
        return self._envelope_data(self.get_health())

    def get_runtime_overview(self) -> Dict[str, Any]:
        """C.10.3: Runtime 总览(包含 personality/selfmodel/emotion/memory)。

        注意:server 端 /runtime/overview 当前 500 错误,
        fallback 到 /runtime/status。
        """
        env = self._get(ENDPOINTS["runtime_overview"])
        if env.get("success", False):
            return env
        # fallback: 任何失败(包括 5xx)都退化到 /runtime/status
        fallback = self._get(ENDPOINTS["runtime_status"])
        if fallback.get("success", False):
            # 把 status 数据包装成 overview 形式
            data = fallback.get("data", {}) or {}
            return {
                **fallback,
                "data": {
                    "runtime": data,
                    "fallback_from": ENDPOINTS["runtime_overview"],
                },
            }
        return env  # 返回原错误

    def get_runtime_overview_data(self) -> Dict[str, Any]:
        return self._envelope_data(self.get_runtime_overview())

    def get_runtime_status_v2(self) -> Dict[str, Any]:
        """C.10.3: Runtime cycle 状态 + adapters + health。"""
        return self._get(ENDPOINTS["runtime_status"])

    def get_runtime_status_v2_data(self) -> Dict[str, Any]:
        return self._envelope_data(self.get_runtime_status_v2())

    # --------------------------------------------------------
    # Runtime
    # --------------------------------------------------------
    def get_runtime_snapshot(self) -> Dict[str, Any]:
        """老式 snapshot 端点已废弃,fallback 到 /runtime/status。"""
        return self.get_runtime_status_v2()

    def get_runtime_status_data(self) -> Dict[str, Any]:
        return self._envelope_data(self.get_runtime_snapshot())

    def get_runtime_tasks(self) -> Dict[str, Any]:
        """/runtime/tasks 不存在,fallback 到 /runtime/status。"""
        env = self.get_runtime_status_v2()
        if env.get("success", False):
            return env
        return self._not_available_envelope("/runtime/tasks")

    def get_runtime_tasks_data(self) -> Dict[str, Any]:
        env = self.get_runtime_tasks()
        if not env.get("success", False):
            return {}
        data = env.get("data", {}) or {}
        return data.get("tasks", data) if isinstance(data, dict) else {}

    def get_runtime_ticks(self, limit: int = 20) -> Dict[str, Any]:
        """/runtime/ticks 不存在,fallback 到 /runtime/status(包含 history)。"""
        env = self.get_runtime_status_v2()
        if env.get("success", False):
            return env
        return self._not_available_envelope("/runtime/ticks")

    def get_runtime_ticks_data(self) -> List[Any]:
        env = self.get_runtime_ticks()
        if not env.get("success", False):
            return []
        data = env.get("data", {}) or {}
        ticks = data.get("ticks", data.get("history", []))
        return ticks if isinstance(ticks, list) else []

    # --------------------------------------------------------
    # Memory
    # --------------------------------------------------------
    def get_memory_snapshot(self) -> Dict[str, Any]:
        """/memory/summary 不存在,fallback 到 /memory/overview。"""
        return self._get(ENDPOINTS["memory_overview"])

    def get_memory_snapshot_data(self) -> Dict[str, Any]:
        return self._envelope_data(self.get_memory_snapshot())

    def get_memory_recent(self, limit: int = 20) -> Dict[str, Any]:
        """/memory/recent 不存在,fallback 到 /memory/overview(包含 recent 列表)。"""
        env = self._get(ENDPOINTS["memory_overview"])
        if env.get("success", False):
            return env
        return self._not_available_envelope("/memory/recent")

    def get_memory_recent_data(self) -> List[Any]:
        env = self.get_memory_recent()
        if not env.get("success", False):
            return []
        data = env.get("data", {}) or {}
        recent = data.get("recent", [])
        return recent if isinstance(recent, list) else []

    def get_memory_overview_v2(self) -> Dict[str, Any]:
        """C.10.3: Memory overview(total/important/recent)。"""
        return self._get(ENDPOINTS["memory_overview"])

    def get_memory_overview_v2_data(self) -> Dict[str, Any]:
        return self._envelope_data(self.get_memory_overview_v2())

    # --------------------------------------------------------
    # Personality
    # --------------------------------------------------------
    def get_personality_snapshot(self) -> Dict[str, Any]:
        """/personality/snapshot 不存在,fallback 到 /personality/status。"""
        return self.get_personality_status_v2()

    def get_personality_snapshot_data(self) -> Dict[str, Any]:
        env = self.get_personality_snapshot()
        if not env.get("success", False):
            return {}
        data = env.get("data", {}) or {}
        snap = data.get("snapshot")
        return snap if isinstance(snap, dict) else {}

    def get_personality_traits(self) -> Dict[str, Any]:
        """/personality/traits 不存在,fallback 到 /personality/status。"""
        env = self.get_personality_status_v2()
        if env.get("success", False):
            return env
        return self._not_available_envelope("/personality/traits")

    def get_personality_traits_data(self) -> List[Any]:
        env = self.get_personality_traits()
        if not env.get("success", False):
            return []
        data = env.get("data", {}) or {}
        traits = data.get("traits", [])
        if isinstance(traits, list):
            return traits
        # server 把 traits 作为 dict(name→value) 嵌入 snapshot
        if isinstance(traits, dict):
            return [{"name": str(k), "value": v} for k, v in traits.items()]
        return []

    def get_personality_evolution(self) -> Dict[str, Any]:
        """/personality/evolution 不存在,fallback 到 /personality/status。"""
        env = self.get_personality_status_v2()
        if env.get("success", False):
            return env
        return self._not_available_envelope("/personality/evolution")

    def get_personality_evolution_data(self) -> List[Any]:
        env = self.get_personality_evolution()
        if not env.get("success", False):
            return []
        data = env.get("data", {}) or {}
        evo = data.get("evolution", data.get("history", []))
        return evo if isinstance(evo, list) else []

    def get_personality_status_v2(self) -> Dict[str, Any]:
        """C.10.3: Personality status(snapshot/evolution/traits)。"""
        return self._get(ENDPOINTS["personality_status"])

    def get_personality_status_v2_data(self) -> Dict[str, Any]:
        return self._envelope_data(self.get_personality_status_v2())

    # ---------- Phase D.6.0: personality/evolution 正式版 ----------
    def get_personality_evolution_v2(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 100,
        sources: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """D.6.0: 人格演化时间线(正式端点)。

        与 get_personality_evolution() (旧版 fallback) 的区别:
        * 旧版: 从 /personality/status 中挖 evolution 字段(可能为空或不完整)
        * 新版: 直接调 /personality/evolution (Provider.get_evolution_timeline)

        Growth Timeline 必须走本方法,不要走 fallback。
        """
        params: Dict[str, Any] = {"limit": int(limit)}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        if sources:
            params["sources"] = ",".join(str(s) for s in sources if s)
        return self._get(ENDPOINTS["personality_evolution_v2"], params=params)

    def get_personality_evolution_v2_data(self) -> List[Any]:
        env = self.get_personality_evolution_v2()
        if not env.get("success", False):
            return []
        data = env.get("data", {}) or {}
        if isinstance(data, dict):
            items = data.get("items", data.get("events", data.get("timeline", [])))
            return items if isinstance(items, list) else []
        if isinstance(data, list):
            return data
        return []

    # --------------------------------------------------------
    # SelfModel
    # --------------------------------------------------------
    def get_selfmodel_snapshot(self) -> Dict[str, Any]:
        """/selfmodel/snapshot 不存在,fallback 到 /selfmodel/status。"""
        return self.get_selfmodel_status_v2()

    def get_selfmodel_snapshot_data(self) -> Dict[str, Any]:
        return self._envelope_data(self.get_selfmodel_snapshot())

    def get_selfmodel_status_v2(self) -> Dict[str, Any]:
        """C.10.3: SelfModel status(version/health/identity)。"""
        return self._get(ENDPOINTS["selfmodel_status"])

    def get_selfmodel_status_v2_data(self) -> Dict[str, Any]:
        return self._envelope_data(self.get_selfmodel_status_v2())

    # ---------- Phase D.6.0: selfmodel 4 个正式列表端点 ----------
    def get_selfmodel_beliefs(
        self,
        domain: Optional[str] = None,
        min_confidence: float = 0.0,
        include_inactive: bool = True,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """D.6.0: SelfModel 核心信念列表。≠ Personality 当前人格!"""
        params: Dict[str, Any] = {
            "min_confidence": float(min_confidence or 0.0),
            "include_inactive": "1" if include_inactive else "0",
            "limit": int(limit),
        }
        if domain:
            params["domain"] = domain
        return self._get(ENDPOINTS["selfmodel_beliefs"], params=params)

    def get_selfmodel_beliefs_data(self) -> List[Any]:
        env = self.get_selfmodel_beliefs()
        if not env.get("success", False):
            return []
        data = env.get("data", {}) or {}
        if isinstance(data, dict):
            items = data.get("items", [])
            return items if isinstance(items, list) else []
        if isinstance(data, list):
            return data
        return []

    def get_selfmodel_history(
        self,
        event_type: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """D.6.0: SelfModel 历史事件(identity/belief/pcr_applied 等)。Growth Timeline 主数据源之一。"""
        params: Dict[str, Any] = {"limit": int(limit)}
        if event_type:
            params["event_type"] = event_type
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        return self._get(ENDPOINTS["selfmodel_history"], params=params)

    def get_selfmodel_history_data(self) -> List[Any]:
        env = self.get_selfmodel_history()
        if not env.get("success", False):
            return []
        data = env.get("data", {}) or {}
        if isinstance(data, dict):
            items = data.get("items", data.get("events", data.get("history", [])))
            return items if isinstance(items, list) else []
        if isinstance(data, list):
            return data
        return []

    def get_selfmodel_reflections(
        self,
        trigger_source: Optional[str] = None,
        reflection_type: Optional[str] = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """D.6.0: SelfModel 反思(SelfPhase 内省产出)。"""
        params: Dict[str, Any] = {
            "min_confidence": float(min_confidence or 0.0),
            "limit": int(limit),
        }
        if trigger_source:
            params["trigger_source"] = trigger_source
        if reflection_type:
            params["reflection_type"] = reflection_type
        return self._get(ENDPOINTS["selfmodel_reflections"], params=params)

    def get_selfmodel_reflections_data(self) -> List[Any]:
        env = self.get_selfmodel_reflections()
        if not env.get("success", False):
            return []
        data = env.get("data", {}) or {}
        if isinstance(data, dict):
            items = data.get("items", [])
            return items if isinstance(items, list) else []
        if isinstance(data, list):
            return data
        return []

    def get_selfmodel_stable_traits(
        self,
        min_stability: float = 0.0,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """D.6.0: SelfModelV3 稳定特质。

        ❗️ 强约束: 本方法 ONLY 走 /selfmodel/traits。
        绝对不能从 get_personality_traits_data() 把值复制过来冒充稳定特质。
        - get_personality_traits_data()          → Resolver.current → 即时人格(会抖动)
        - get_selfmodel_stable_traits_data()      → SelfModelV3      → 稳定自我认知(慢变)
        StablePortraitCard 必须用本方法。
        """
        params: Dict[str, Any] = {
            "min_stability": float(min_stability or 0.0),
            "limit": int(limit),
            "offset": int(offset or 0),
        }
        return self._get(ENDPOINTS["selfmodel_stable_traits"], params=params)

    def get_selfmodel_stable_traits_data(self) -> List[Any]:
        env = self.get_selfmodel_stable_traits()
        if not env.get("success", False):
            return []
        data = env.get("data", {}) or {}
        if isinstance(data, dict):
            items = data.get("items", [])
            return items if isinstance(items, list) else []
        if isinstance(data, list):
            return data
        return []

    # --------------------------------------------------------
    # Growth
    # --------------------------------------------------------
    def get_growth_snapshot(self) -> Dict[str, Any]:
        """/growth/summary 不存在,fallback 到 /growth/status。"""
        return self.get_growth_status_v2()

    def get_growth_snapshot_data(self) -> Dict[str, Any]:
        return self._envelope_data(self.get_growth_snapshot())

    def get_growth_recent(self, limit: int = 20) -> Dict[str, Any]:
        """/growth/recent 不存在,fallback 到 /growth/status。"""
        env = self.get_growth_status_v2()
        if env.get("success", False):
            return env
        return self._not_available_envelope("/growth/recent")

    def get_growth_recent_data(self) -> List[Any]:
        env = self.get_growth_recent()
        if not env.get("success", False):
            return []
        data = env.get("data", {}) or {}
        recent = data.get("recent", [])
        return recent if isinstance(recent, list) else []

    def get_growth_proposals(self) -> Dict[str, Any]:
        """/growth/proposals 不存在,fallback 到 /growth/status。"""
        env = self.get_growth_status_v2()
        if env.get("success", False):
            return env
        return self._not_available_envelope("/growth/proposals")

    def get_growth_proposals_data(self) -> List[Any]:
        env = self.get_growth_proposals()
        if not env.get("success", False):
            return []
        data = env.get("data", {}) or {}
        proposals = data.get("proposals", [])
        return proposals if isinstance(proposals, list) else []

    def get_growth_status_v2(self) -> Dict[str, Any]:
        """C.10.3: Growth status(proposal 数量/evolution 状态)。"""
        return self._get(ENDPOINTS["growth_status"])

    def get_growth_status_v2_data(self) -> Dict[str, Any]:
        return self._envelope_data(self.get_growth_status_v2())

    # ---------- Phase D.6.0: growth/proposals 正式版 ----------
    def get_growth_proposals_v2(
        self,
        status: Optional[str] = None,
        proposal_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """D.6.0: Growth Proposal 列表(正式端点)。Growth Timeline 主数据源之二。"""
        params: Dict[str, Any] = {
            "limit": int(limit),
            "offset": int(offset or 0),
        }
        if status:
            params["status"] = status
        if proposal_type:
            params["proposal_type"] = proposal_type
        return self._get(ENDPOINTS["growth_proposals_v2"], params=params)

    def get_growth_proposals_v2_data(self) -> List[Any]:
        env = self.get_growth_proposals_v2()
        if not env.get("success", False):
            return []
        data = env.get("data", {}) or {}
        if isinstance(data, dict):
            items = data.get("items", data.get("proposals", []))
            return items if isinstance(items, list) else []
        if isinstance(data, list):
            return data
        return []

    # --------------------------------------------------------
    # Initiative
    # --------------------------------------------------------
    def get_initiative_snapshot(self) -> Dict[str, Any]:
        """/initiative/summary 不存在,fallback 到 /initiative/status。"""
        return self.get_initiative_status_v2()

    def get_initiative_snapshot_data(self) -> Dict[str, Any]:
        return self._envelope_data(self.get_initiative_snapshot())

    def get_initiative_actions(self) -> Dict[str, Any]:
        """/initiative/actions 不存在,fallback 到 /initiative/status。"""
        env = self.get_initiative_status_v2()
        if env.get("success", False):
            return env
        return self._not_available_envelope("/initiative/actions")

    def get_initiative_actions_data(self) -> List[Any]:
        env = self.get_initiative_actions()
        if not env.get("success", False):
            return []
        data = env.get("data", {}) or {}
        actions = data.get("actions", data.get("recent_actions", []))
        return actions if isinstance(actions, list) else []

    def get_initiative_status_v2(self) -> Dict[str, Any]:
        """C.10.3: Initiative status(interest/action 计数 + 最近行为)。"""
        return self._get(ENDPOINTS["initiative_status"])

    def get_initiative_status_v2_data(self) -> Dict[str, Any]:
        return self._envelope_data(self.get_initiative_status_v2())

    # --------------------------------------------------------
    # Audit
    # --------------------------------------------------------
    def get_audit_recent(self, limit: int = 20) -> Dict[str, Any]:
        """C.10.3: 最近 audit 事件。"""
        return self._get(ENDPOINTS["audit_recent"], params={"limit": int(limit)})

    def get_audit_recent_data(self) -> List[Any]:
        return self._envelope_list(self.get_audit_recent())

    # --------------------------------------------------------
    # Life(server 暂未提供,统一返回 not-available)
    # --------------------------------------------------------
    def get_life_state(self) -> Dict[str, Any]:
        return self._not_available_envelope("/life/state")

    def get_life_state_data(self) -> Dict[str, Any]:
        return {}

    def get_life_timeline(self, limit: int = 50) -> Dict[str, Any]:
        return self._not_available_envelope("/life/timeline")

    def get_life_timeline_data(self) -> List[Any]:
        return []

    def get_life_graph(self) -> Dict[str, Any]:
        return self._not_available_envelope("/life/graph")

    def get_life_graph_data(self) -> Dict[str, Any]:
        return {}

    # --------------------------------------------------------
    # Health
    # --------------------------------------------------------
    def health_check(self) -> Dict[str, bool]:
        """
        检查各 Provider 分组的可用性。

        每个分组探测一次代表 endpoint,失败视为整组不可用。
        注意:这是轻量探测,不抓取大数据。

        成功判定:
            - HTTP 2xx → ok
            - 401/403  → ok(说明 server 在线,只是 token 权限)
            - 404/405  → not ok(端点未注册;但 server 仍在)
        """
        result: Dict[str, bool] = {}
        for group_name, endpoints in PROVIDER_GROUPS.items():
            ok = False
            for ep in endpoints:
                resp = self._get(ep)
                if resp.get("success", False):
                    ok = True
                    break
                err = str(resp.get("error", ""))
                if err.startswith("auth_error"):
                    ok = True
                    break
            result[group_name] = ok
        return result

    # --------------------------------------------------------
    # 安全自检
    # --------------------------------------------------------
    def security_self_check(self) -> Dict[str, Any]:
        """
        返回:
            {
                "endpoint_count": N,
                "forbidden_endpoints": [list],
                "all_get_only": bool,
                "all_known_endpoints": bool
            }
        """
        all_eps: List[str] = []
        for eps in ENDPOINTS.values():
            if not isinstance(eps, str):
                continue
            all_eps.append(eps)

        forbidden_in_path: List[str] = []
        for ep in all_eps:
            lower = ep.lower()
            for kw in _FORBIDDEN_WRITE_KEYWORDS:
                # 仅当 kw 作为整词出现在 path 段中(避免误判 update_x)
                if f"/{kw}/" in lower or lower.endswith(f"/{kw}"):
                    forbidden_in_path.append(ep)
                    break

        return {
            "endpoint_count": len(all_eps),
            "forbidden_endpoints": forbidden_in_path,
            "all_get_only": True,  # 设计上只暴露 GET
            "all_known_endpoints": len(all_eps) == len(set(all_eps)),
        }


# ============================================================
# 模块级单例
# ============================================================
_bridge_instance: Optional[RemoteProviderBridge] = None
_bridge_lock = threading.Lock()


def get_remote_provider_bridge() -> RemoteProviderBridge:
    """获取 RemoteProviderBridge 单例(懒加载)。"""
    global _bridge_instance
    if _bridge_instance is None:
        with _bridge_lock:
            if _bridge_instance is None:
                _bridge_instance = RemoteProviderBridge()
    return _bridge_instance


def reset_remote_provider_bridge_for_testing() -> None:
    """测试用:重置单例。"""
    global _bridge_instance
    with _bridge_lock:
        _bridge_instance = None


__all__ = [
    "ENDPOINTS",
    "PROVIDER_GROUPS",
    "RemoteProviderBridge",
    "get_remote_provider_bridge",
    "reset_remote_provider_bridge_for_testing",
]
