# -*- coding: utf-8 -*-
"""
src/runtime/adapters/impl/memory_runtime_adapter.py

Phase C.2 Memory Runtime Integration —— MemoryRuntimeAdapter

职责:
  - 实现 Phase C.1 CycleAdapter 协议(duck-type)
  - 在 Runtime Cycle 开始时:
      读取 ctx.input_event
      调用 MemoryStore.load() / VectorMemory.search()
      检索结果写入 ctx.memory_output
  - 在 Runtime Cycle 结束时(collect_candidate):
      基于 ctx 构造 MemoryCandidate
      交给现有 Memory Consolidation 流程
  - 全部 fail-soft:
      空 Memory / Memory 异常 / VectorMemory 不可用
      均不抛异常,仅标记 ctx.adapter_results["memory"] = {"ok": False, ...}

不修改:
  - MemoryStore 核心
  - VectorMemory 核心
  - MemoryConsolidationEngine 核心
  - RuntimeCycleOrchestrator
  - B.4-B.13

设计原则:
  - 与已有 MemoryAdapterImpl 共存,只新增文件,不改旧文件
  - 所有依赖都用 Optional 注入,缺省时自建或降级
  - MemoryCandidate 是 dataclass,直接挂在 ctx.metadata["memory_candidates"]
    不写 MemoryStore(由 caller 决定怎么递交 consolidation 流程)
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.runtime.cycle_adapter import (
    CYCLE_ADAPTER_SCHEMA_VERSION,
    STANDARD_ADAPTER_MEMORY,
)
from src.runtime.cycle_context import RuntimeCycleContext

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本
# ============================================================

MEMORY_RUNTIME_ADAPTER_SCHEMA_VERSION = "1.0"
PHASE_C2_NAME = "phase_c2"
PHASE_C2_VERSION = "1.0.0"

# Persistence stage(append-only)
PHASE_C2_STAGE_MEMORY_RETRIEVED = "phase_c2_memory_retrieved"
PHASE_C2_STAGE_CANDIDATE_GENERATED = "phase_c2_memory_candidate_generated"
PHASE_C2_STAGE_VECTOR_FALLBACK = "phase_c2_vector_memory_fallback"
PHASE_C2_STAGE_STORE_FALLBACK = "phase_c2_memory_store_fallback"

ALL_PHASE_C2_STAGES = (
    PHASE_C2_STAGE_MEMORY_RETRIEVED,
    PHASE_C2_STAGE_CANDIDATE_GENERATED,
    PHASE_C2_STAGE_VECTOR_FALLBACK,
    PHASE_C2_STAGE_STORE_FALLBACK,
)


# ============================================================
# MemoryCandidate
# ============================================================

@dataclass
class MemoryCandidate:
    """候选记忆:从一次 Runtime Cycle 提炼出的可落盘候选。

    字段:
      candidate_id       候选 ID
      cycle_id           来源 cycle ID
      user_id            用户 ID
      content            候选正文(str)
      role               role(user / system / assistant)
      source             来源描述(从 event 提取的 user_input 等)
      tags               标签列表
      confidence         置信度[0, 1]
      metadata           额外元数据
      created_at         ISO 8601 UTC 时间戳
      schema_version     schema 版本
    """
    candidate_id: str = field(default_factory=lambda: f"mcand_{uuid.uuid4().hex[:12]}")
    cycle_id: str = ""
    user_id: str = "yuyi"
    content: str = ""
    role: str = "user"
    source: str = ""
    tags: List[str] = field(default_factory=list)
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    schema_version: str = MEMORY_RUNTIME_ADAPTER_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_memory_entry(self) -> Dict[str, Any]:
        """转换为 MemoryStore.add() 接受的 dict(不直接调用 store.add)。"""
        return {
            "user_id": str(self.user_id or "yuyi"),
            "content": str(self.content or ""),
            "role": str(self.role or "user"),
            "metadata": {
                "type": "memory_candidate",
                "candidate_id": self.candidate_id,
                "cycle_id": self.cycle_id,
                "source": self.source,
                "tags": list(self.tags or []),
                "confidence": float(self.confidence or 0.0),
                "created_at": self.created_at,
                "schema_version": self.schema_version,
                **(self.metadata or {}),
            },
        }


# ============================================================
# MemoryRetrievalResult
# ============================================================

@dataclass
class MemoryRetrievalResult:
    """Memory 检索结果(写到 ctx.memory_output)。"""
    query: str = ""
    matched: List[Dict[str, Any]] = field(default_factory=list)
    store_count: int = 0
    vector_count: int = 0
    used_vector: bool = False
    used_store: bool = False
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    schema_version: str = MEMORY_RUNTIME_ADAPTER_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def is_empty(self) -> bool:
        return len(self.matched) == 0

    @property
    def has_results(self) -> bool:
        return len(self.matched) > 0


# ============================================================
# 内部工具
# ============================================================

def _extract_query_text(event: Any) -> str:
    """从 event 提取 query 文本(失败返回空串)。"""
    try:
        if event is None:
            return ""
        if isinstance(event, str):
            return event.strip()
        if isinstance(event, dict):
            return str(
                event.get("user_input", "")
                or event.get("content", "")
                or event.get("text", "")
                or event.get("input", "")
                or event.get("query", "")
                or ""
            ).strip()
        if hasattr(event, "user_input"):
            v = getattr(event, "user_input", None)
            if v:
                return str(v).strip()
        if hasattr(event, "content"):
            v = getattr(event, "content", None)
            if v:
                return str(v).strip()
        payload = getattr(event, "payload", None)
        if isinstance(payload, dict):
            return str(
                payload.get("text", "")
                or payload.get("content", "")
                or payload.get("user_input", "")
                or payload.get("query", "")
                or ""
            ).strip()
        return ""
    except Exception:  # noqa: BLE001
        return ""


def _safe_match(memory: Any) -> Optional[Dict[str, Any]]:
    """把 store 里的 memory dict 转换为统一 match 格式(失败返回 None)。"""
    try:
        if memory is None:
            return None
        if isinstance(memory, dict):
            return {
                "id": str(memory.get("id", "")),
                "content": str(memory.get("content", "")),
                "role": str(memory.get("role", "user")),
                "user_id": str(memory.get("user_id", "")),
                "metadata": dict(memory.get("metadata", {}) or {}),
                "timestamp": str(memory.get("timestamp", "")),
                "relevance": 1.0,  # store 命中默认 1.0(vector 命中覆盖)
                "source": "memory_store",
            }
        return None
    except Exception:  # noqa: BLE001
        return None


def _safe_vector_match(item: Any) -> Optional[Dict[str, Any]]:
    """把 vector.search 返回的 item 转 match 格式。"""
    try:
        if item is None:
            return None
        if isinstance(item, dict):
            relevance = float(item.get("relevance", 1.0) or 1.0)
            return {
                "id": str(item.get("mem_id", "") or item.get("id", "")),
                "content": str(item.get("content", "")),
                "role": str(item.get("role", "user")),
                "metadata": dict(item.get("metadata", {}) or {}),
                "timestamp": str(item.get("timestamp", "")),
                "relevance": relevance,
                "source": "vector_memory",
            }
        return None
    except Exception:  # noqa: BLE001
        return None


# ============================================================
# MemoryRuntimeAdapter(主类)
# ============================================================

class MemoryRuntimeAdapter:
    """
    Memory 系统 Runtime Cycle Adapter(Phase C.2 / v1.0)

    实现 CycleAdapter 协议(duck-type):
      name             = "memory"
      schema_version   = "1.0"
      attach()         - 初始化 MemoryStore / VectorMemory 引用
      detach()         - 解除引用
      health_check()   - 健康检查
      process_cycle()  - 一次 cycle 处理
      snapshot()       - 快照

    业务行为:
      - 读 ctx.input_event 提取 query
      - 优先用 VectorMemory.search(query)(若可用)
      - 否则退化到 MemoryStore.load() 做内存匹配
      - 检索结果写 ctx.memory_output(MemoryRetrievalResult)
      - 同时保留 last retrieval snapshot
      - collect_candidate(ctx) - 生成 MemoryCandidate(不写 store)

    全部 fail-soft:任何异常 / 不可用均降级返回空结果,不抛。
    """

    name: str = STANDARD_ADAPTER_MEMORY
    schema_version: str = MEMORY_RUNTIME_ADAPTER_SCHEMA_VERSION

    def __init__(
        self,
        memory_store: Any = None,
        vector_memory: Any = None,
        user_id: str = "yuyi",
        top_k: int = 5,
        enable_vector: bool = True,
        enable_store: bool = True,
    ) -> None:
        self._user_id = str(user_id or "yuyi")
        self._top_k = max(1, int(top_k or 5))
        self._enable_vector = bool(enable_vector)
        self._enable_store = bool(enable_store)

        # 外部注入优先
        self._store = memory_store
        self._vector = vector_memory

        # 内部状态
        self._attached: bool = False
        self._lock = threading.RLock()
        self._process_count: int = 0
        self._retrieve_count: int = 0
        self._candidate_count: int = 0
        self._last_retrieval: Optional[Dict[str, Any]] = None
        self._last_health: Optional[Dict[str, Any]] = None
        self._last_candidates: List[Dict[str, Any]] = []
        self._fallback_vector_count: int = 0
        self._fallback_store_count: int = 0

    # --------------------------------------------------------
    # CycleAdapter 协议
    # --------------------------------------------------------

    def attach(self) -> bool:
        """接入 Memory 系统(可选自建 store / vector)。"""
        with self._lock:
            if self._store is None and self._enable_store:
                try:
                    from src.memory.memory_store import MemoryStore
                    self._store = MemoryStore()
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"[phase_c2] MemoryStore 自建失败: {exc}")
                    self._store = None
            if self._vector is None and self._enable_vector:
                try:
                    from src.memory.vector import VectorMemory
                    self._vector = VectorMemory()
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"[phase_c2] VectorMemory 自建失败: {exc}")
                    self._vector = None
            self._attached = True
        return True

    def detach(self) -> bool:
        with self._lock:
            self._attached = False
        return True

    def is_attached(self) -> bool:
        with self._lock:
            return self._attached

    def health_check(self) -> Dict[str, Any]:
        try:
            with self._lock:
                # 检测 store 可用性
                store_ok = False
                store_count = 0
                if self._store is not None:
                    try:
                        if hasattr(self._store, "load"):
                            store_count = len(self._store.load() or [])
                        store_ok = True
                    except Exception:  # noqa: BLE001
                        store_ok = False
                # 检测 vector 可用性
                vector_ok = False
                vector_count = 0
                if self._vector is not None:
                    try:
                        if hasattr(self._vector, "count"):
                            vector_count = int(self._vector.count() or 0)
                        vector_ok = True
                    except Exception:  # noqa: BLE001
                        vector_ok = False
                result: Dict[str, Any] = {
                    "healthy": True,
                    "name": self.name,
                    "schema_version": self.schema_version,
                    "attached": bool(self._attached),
                    "store_available": bool(store_ok),
                    "vector_available": bool(vector_ok),
                    "store_count": int(store_count),
                    "vector_count": int(vector_count),
                    "process_count": int(self._process_count),
                    "retrieve_count": int(self._retrieve_count),
                    "candidate_count": int(self._candidate_count),
                    "user_id": str(self._user_id),
                }
            self._last_health = result
            return result
        except Exception as exc:  # noqa: BLE001
            return {
                "healthy": False,
                "name": self.name,
                "schema_version": self.schema_version,
                "error": repr(exc),
            }

    def process_cycle(self, ctx: Any) -> Any:
        """一次 Runtime Cycle 处理(被 orchestrator 调用)。

        流程:
          1) 提取 query
          2) 调 retrieve
          3) 把结果写 ctx.memory_output
          4) 调 collect_candidate 生成 MemoryCandidate 挂到 ctx.metadata
          5) 统计 + 快照
          6) 返回 ctx
        """
        with self._lock:
            self._process_count += 1
        try:
            if not isinstance(ctx, RuntimeCycleContext):
                # 非 RuntimeCycleContext 也支持 dict 形式,直接返回
                return ctx
            query = _extract_query_text(ctx.input_event)
            retrieval = self._retrieve(query)
            ctx.memory_output = retrieval.to_dict() if isinstance(retrieval, MemoryRetrievalResult) else retrieval

            # 收集 candidate
            candidates = self.collect_candidate(ctx)
            if candidates:
                # 挂到 ctx.metadata 供 consolidation 流程使用
                ctx.metadata.setdefault("memory_candidates", [])
                if isinstance(ctx.metadata["memory_candidates"], list):
                    ctx.metadata["memory_candidates"].extend([c.to_dict() for c in candidates])

            # 暴露 adapter_results 字段(供 orchestrator 标记)
            with self._lock:
                self._last_retrieval = retrieval.to_dict() if isinstance(retrieval, MemoryRetrievalResult) else None
                self._last_candidates = [c.to_dict() for c in (candidates or [])]
            return ctx
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_c2] process_cycle 异常(已隔离): {exc}")
            # 不抛,返回原 ctx(orchestrator 会通过 record_adapter 标记)
            return ctx

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self.name,
                "schema_version": self.schema_version,
                "attached": bool(self._attached),
                "user_id": str(self._user_id),
                "top_k": int(self._top_k),
                "enable_vector": bool(self._enable_vector),
                "enable_store": bool(self._enable_store),
                "process_count": int(self._process_count),
                "retrieve_count": int(self._retrieve_count),
                "candidate_count": int(self._candidate_count),
                "fallback_vector_count": int(self._fallback_vector_count),
                "fallback_store_count": int(self._fallback_store_count),
                "last_retrieval": dict(self._last_retrieval or {}),
                "last_health": dict(self._last_health or {}),
                "last_candidate_count": len(self._last_candidates or []),
            }

    # --------------------------------------------------------
    # 业务:检索
    # --------------------------------------------------------

    def retrieve(self, query: str, user_id: str = None) -> MemoryRetrievalResult:
        """公开检索接口(供 C.1 之外调用)。

        Phase 4.1: 新增可选 user_id（多用户隔离，章程：禁止跨用户召回）；
        不传时回落到构造时的 self._user_id，行为与旧版一致。

        Returns:
            MemoryRetrievalResult
        """
        return self._retrieve(str(query or ""), user_id=user_id)

    def _retrieve(self, query: str, user_id: str = None) -> MemoryRetrievalResult:
        """内部检索:优先 vector → 退化 store → 空结果。"""
        with self._lock:
            self._retrieve_count += 1
        query = str(query or "").strip()
        if not query:
            return MemoryRetrievalResult(query="")
        # Phase 4.1: 有效用户 = 调用方显式传入 > 构造默认值
        effective_uid = str(user_id or self._user_id or "").strip() or None

        # 1) Vector 优先
        if self._enable_vector and self._vector is not None:
            try:
                # Phase 4.1: vector.search 已支持 user_id 过滤（P3）；
                # 对其他 duck-typed vector 实现保持兼容（TypeError 时回退旧签名）
                try:
                    results = self._vector.search(
                        query, top_k=self._top_k, user_id=effective_uid
                    )
                except TypeError:
                    results = self._vector.search(query, top_k=self._top_k)
                if results is None:
                    results = []
                matched: List[Dict[str, Any]] = []
                for item in results:
                    m = _safe_vector_match(item)
                    if m is not None:
                        matched.append(m)
                # 统计 vector 集合大小
                try:
                    vector_count = int(self._vector.count() or 0) if hasattr(self._vector, "count") else 0
                except Exception:  # noqa: BLE001
                    vector_count = 0
                with self._lock:
                    self._fallback_vector_count = 0
                return MemoryRetrievalResult(
                    query=query,
                    matched=matched,
                    store_count=0,
                    vector_count=vector_count,
                    used_vector=True,
                    used_store=False,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[phase_c2] vector 检索失败,降级 store: {exc}")
                with self._lock:
                    self._fallback_vector_count += 1

        # 2) 退化到 store
        if self._enable_store and self._store is not None:
            try:
                if hasattr(self._store, "load"):
                    all_memories = self._store.load() or []
                else:
                    all_memories = []
                # Phase 4.1: 多用户隔离——指定有效用户时仅匹配该用户的记忆
                # （章程：禁止跨用户召回；与 P3 get_by_user 严格一致口径）
                if effective_uid:
                    all_memories = [
                        m for m in all_memories
                        if isinstance(m, dict)
                        and str(m.get("user_id") or "") == effective_uid
                    ]
                store_count = len(all_memories)
                # 简单子串匹配(全 fail-soft)
                q_lower = query.lower()
                matched = []
                for m in all_memories:
                    content = str(m.get("content", "") or "").lower()
                    if q_lower and q_lower in content:
                        mm = _safe_match(m)
                        if mm is not None:
                            mm["relevance"] = 0.8  # store 命中给一个固定分
                            matched.append(mm)
                # 截断到 top_k
                matched = matched[:self._top_k]
                with self._lock:
                    self._fallback_store_count = 0
                return MemoryRetrievalResult(
                    query=query,
                    matched=matched,
                    store_count=store_count,
                    vector_count=0,
                    used_vector=False,
                    used_store=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[phase_c2] store 检索失败: {exc}")
                with self._lock:
                    self._fallback_store_count += 1

        # 3) 全部不可用,返回空
        return MemoryRetrievalResult(query=query)

    # --------------------------------------------------------
    # 业务:生成 candidate
    # --------------------------------------------------------

    def collect_candidate(self, ctx: Any) -> List[MemoryCandidate]:
        """根据 ctx 生成 MemoryCandidate(不写 store)。

        来源:
          - ctx.input_event 的 user_input 文本
          - ctx.metadata["user_input"]
          - ctx.memory_output 中的 query
        """
        try:
            with self._lock:
                self._candidate_count += 1

            if not isinstance(ctx, RuntimeCycleContext):
                return []

            candidates: List[MemoryCandidate] = []
            # 1) 从 input_event 提取
            text = _extract_query_text(ctx.input_event)
            if not text and isinstance(ctx.metadata, dict):
                text = str(ctx.metadata.get("user_input", "") or "")
            if not text and isinstance(ctx.memory_output, dict):
                text = str(ctx.memory_output.get("query", "") or "")

            if text:
                cand = MemoryCandidate(
                    cycle_id=str(ctx.cycle_id or ""),
                    user_id=str(ctx.user_id or self._user_id),
                    content=text,
                    role="user",
                    source="runtime_cycle_input",
                    tags=["runtime_cycle", "phase_c2"],
                    confidence=0.7,
                    metadata={
                        "session_id": str(ctx.session_id or ""),
                        "cycle_id": str(ctx.cycle_id or ""),
                        "timestamp": str(ctx.timestamp or ""),
                    },
                )
                candidates.append(cand)

            # 2) 若 retrieval 有命中,生成补充 candidate
            memory_output = ctx.memory_output
            if isinstance(memory_output, dict):
                matched = memory_output.get("matched", [])
                if isinstance(matched, list) and len(matched) > 0:
                    summary = MemoryCandidate(
                        cycle_id=str(ctx.cycle_id or ""),
                        user_id=str(ctx.user_id or self._user_id),
                        content=f"Retrieved {len(matched)} related memories for cycle {ctx.cycle_id}",
                        role="system",
                        source="runtime_cycle_retrieval",
                        tags=["retrieval_summary", "phase_c2"],
                        confidence=0.5,
                        metadata={
                            "matched_count": len(matched),
                            "used_vector": bool(memory_output.get("used_vector", False)),
                            "used_store": bool(memory_output.get("used_store", False)),
                            "session_id": str(ctx.session_id or ""),
                        },
                    )
                    candidates.append(summary)

            return candidates
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_c2] collect_candidate 异常(已隔离): {exc}")
            return []

    # --------------------------------------------------------
    # 状态查询
    # --------------------------------------------------------

    @property
    def last_retrieval(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._last_retrieval) if self._last_retrieval else None

    @property
    def last_candidates(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._last_candidates or [])

    @property
    def process_count(self) -> int:
        with self._lock:
            return self._process_count

    @property
    def retrieve_count(self) -> int:
        with self._lock:
            return self._retrieve_count

    @property
    def candidate_count(self) -> int:
        with self._lock:
            return self._candidate_count


# ============================================================
# 工厂
# ============================================================

def create_memory_runtime_adapter(
    memory_store: Any = None,
    vector_memory: Any = None,
    user_id: str = "yuyi",
    top_k: int = 5,
    enable_vector: bool = True,
    enable_store: bool = True,
) -> MemoryRuntimeAdapter:
    """工厂函数:创建一个 MemoryRuntimeAdapter。

    Args:
        memory_store: 可选 MemoryStore 实例(不传则 attach 时自建)
        vector_memory: 可选 VectorMemory 实例(不传则 attach 时自建)
        user_id: 用户 ID
        top_k: 检索 top_k
        enable_vector: 是否启用 vector 检索
        enable_store: 是否启用 store 检索
    """
    return MemoryRuntimeAdapter(
        memory_store=memory_store,
        vector_memory=vector_memory,
        user_id=user_id,
        top_k=top_k,
        enable_vector=enable_vector,
        enable_store=enable_store,
    )


def safe_get_memory_adapter_summary(adapter: Any) -> Dict[str, Any]:
    """全局安全 summary。"""
    empty = {
        "name": STANDARD_ADAPTER_MEMORY,
        "schema_version": MEMORY_RUNTIME_ADAPTER_SCHEMA_VERSION,
        "attached": False,
        "process_count": 0,
    }
    if adapter is None:
        return empty
    try:
        return adapter.snapshot() or empty
    except Exception:  # noqa: BLE001
        return empty


# ============================================================
# 公共 API
# ============================================================

__all__ = [
    "MEMORY_RUNTIME_ADAPTER_SCHEMA_VERSION",
    "PHASE_C2_NAME",
    "PHASE_C2_VERSION",
    "PHASE_C2_STAGE_MEMORY_RETRIEVED",
    "PHASE_C2_STAGE_CANDIDATE_GENERATED",
    "PHASE_C2_STAGE_VECTOR_FALLBACK",
    "PHASE_C2_STAGE_STORE_FALLBACK",
    "ALL_PHASE_C2_STAGES",
    "MemoryCandidate",
    "MemoryRetrievalResult",
    "MemoryRuntimeAdapter",
    "create_memory_runtime_adapter",
    "safe_get_memory_adapter_summary",
    "_extract_query_text",
]
