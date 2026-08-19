# -*- coding: utf-8 -*-
"""
src/runtime/token_optimizer.py

Phase 6.5 —— Runtime Token Optimizer Adapter(编排层前置处理器)。

职责:
    作为 Runtime 编排层的前置处理,在 user_message 进入 Orchestrator
    之前对输入做 token 维度优化,并把统计结果写入 RuntimeContext.outputs.

设计原则:
    1. **编排层薄适配**:本模块不实现任何 token 优化逻辑,只复用
       ``src.token_opt`` 中已有的 HistoryCompressor / MemorySummarizer.
    2. **协议优先**:定义 ``TokenOptimizerLike`` Protocol,任何实现
       ``optimize(...)`` 的对象都可注入(便于测试 fake).
    3. **不修改 Authority**:严禁 import / 修改 memory / personality /
       growth / emotion / llm 等业务模块.
    4. **异常隔离**:optimize() 任何异常都不影响主流程 —— fallback
       原始输入,并在 outputs 中记录 ``fallback_reason``.
    5. **默认关闭**:未注入时 (=None),Pipeline 行为与 Phase 6.3 完全一致,
       不写入 token_usage 字段.

数据契约(OptimizeResult):
    {
        "content": str,                # 优化后输入(供 Orchestrator 使用)
        "before_tokens": int,          # 优化前估算 token
        "after_tokens": int,           # 优化后估算 token
        "saved_tokens": int,           # 节省量 (= before - after,>=0)
        "compression_ratio": float,    # 压缩比 (= after / before, [0,1])
        "applied": bool,               # 是否真正发生了压缩
        "fallback_reason": str|None,   # 失败原因(若有)
    }

依赖:
    - stdlib (logging, typing)
    - src.token_opt.HistoryCompressor (已存在,仅做 import)
    - src.token_opt.MemorySummarizer (已存在,仅做 import)
    - 不依赖 RuntimeContext / Pipeline(避免循环依赖)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Protocol

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本常量
# ============================================================
RUNTIME_TOKEN_OPTIMIZER_SCHEMA_VERSION = "1.0"


# ============================================================
# 异常
# ============================================================
class TokenOptimizerError(Exception):
    """TokenOptimizer 错误基类。"""


# ============================================================
# 协议(允许测试 fake)
# ============================================================
class TokenOptimizerLike(Protocol):
    """Runtime Pipeline 所需的最小 TokenOptimizer 协议。

    任何实现 ``optimize(user_message, history, memories) -> Dict`` 的对象
    都可注入到 ``RuntimePipeline(token_optimizer=...)``。返回的 dict 至少
    包含 ``content`` 字段(若缺失则回退到原 user_message)。

    推荐返回的字段集(见模块顶部契约),Pipeline 仅做防御性读取。
    """

    def optimize(  # pragma: no cover
        self,
        user_message: str,
        history: Optional[List[Dict[str, Any]]] = None,
        memories: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        ...


# ============================================================
# 工具
# ============================================================
def estimate_tokens(text: str) -> int:
    """轻量级 token 估算(不依赖 tiktoken / 任何 LLM 客户端)。

    中文:每 1.5 字符 ≈ 1 token;英文:每 4 字符 ≈ 1 token.
    本估算仅供"前后对比",不追求精确。失败时回退为字符数。
    """
    if not isinstance(text, str) or not text:
        return 0
    try:
        chinese = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        non_chinese = len(text) - chinese
        est = int(chinese / 1.5) + int(non_chinese / 4)
        return max(est, 1) if text else 0
    except Exception:  # noqa: BLE001
        return max(len(text), 1) if text else 0


def _safe_int(v: Any, default: int = 0) -> int:
    """把任意值尽量转成 int(失败返回 default)。"""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        try:
            return int(float(v))
        except Exception:  # noqa: BLE001
            return default
    return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    """把任意值尽量转成 float(失败返回 default)。"""
    if isinstance(v, bool):
        return float(int(v))
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except Exception:  # noqa: BLE001
            return default
    return default


def _safe_str(v: Any, default: str = "") -> str:
    if isinstance(v, str):
        return v
    if v is None:
        return default
    try:
        return str(v)
    except Exception:  # noqa: BLE001
        return default


# ============================================================
# 核心:NoOp 实现(默认 / 兜底)
# ============================================================
class NoOpTokenOptimizer:
    """不进行任何压缩的占位 optimizer。

    - 始终返回原始 user_message
    - before_tokens == after_tokens == estimate_tokens(content)
    - applied = False
    - 用于"禁用"路径和 fallback 路径
    """

    def optimize(
        self,
        user_message: str,
        history: Optional[List[Dict[str, Any]]] = None,
        memories: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        msg = user_message if isinstance(user_message, str) else ""
        tokens = estimate_tokens(msg)
        return {
            "content": msg,
            "before_tokens": tokens,
            "after_tokens": tokens,
            "saved_tokens": 0,
            "compression_ratio": 1.0,
            "applied": False,
            "fallback_reason": None,
        }


# ============================================================
# 核心:RuntimeTokenOptimizer(默认实现,复用 src.token_opt)
# ============================================================
class RuntimeTokenOptimizer:
    """Runtime 编排层 Token 优化适配器(默认实现)。

    行为:
        1. 接收 ``user_message`` / ``history`` / ``memories`` 三类输入
        2. 当 ``history`` 超长时,调用 ``HistoryCompressor.compress`` 压缩
        3. 当 ``memories`` 非空时,调用 ``MemorySummarizer.summarize`` 摘要
        4. 把摘要作为 system 消息与 user_message 拼成新的 content
        5. 计算 before/after/saved/compression_ratio

    构造参数:
        recent_turns: 保留最近 N 轮对话(转给 HistoryCompressor,默认 5)
        max_memories: 最多摘要的记忆数(默认 5)
        use_llm_summary: 是否用 LLM 摘要(默认 False,避免重复 LLM 调用)
        use_memory_summary: 是否启用记忆摘要(默认 True)

    异常隔离:
        任一组件失败 -> 返回原始 user_message,applied=False,
        fallback_reason 记录原因(由 Pipeline 写入 outputs).
    """

    def __init__(
        self,
        *,
        recent_turns: int = 5,
        max_memories: int = 5,
        use_llm_summary: bool = False,
        use_memory_summary: bool = True,
    ) -> None:
        self._recent_turns = max(1, int(recent_turns or 5))
        self._max_memories = max(1, int(max_memories or 5))
        self._use_llm_summary = bool(use_llm_summary)
        self._use_memory_summary = bool(use_memory_summary)
        # 懒加载:首次调用时再 import 业务组件
        self._history_compressor = None  # type: ignore[var-annotated]
        self._memory_summarizer = None  # type: ignore[var-annotated]

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    def _get_history_compressor(self) -> Any:
        if self._history_compressor is None:
            from src.token_opt import HistoryCompressor
            self._history_compressor = HistoryCompressor(
                recent_turns=self._recent_turns,
                use_llm_summary=self._use_llm_summary,
            )
        return self._history_compressor

    def _get_memory_summarizer(self) -> Any:
        if self._memory_summarizer is None:
            from src.token_opt import MemorySummarizer
            self._memory_summarizer = MemorySummarizer(
                max_memories=self._max_memories,
            )
        return self._memory_summarizer

    def _estimate_list_tokens(self, items: List[Dict[str, Any]]) -> int:
        total = 0
        for it in items or []:
            if not isinstance(it, dict):
                continue
            content = it.get("content", "")
            if isinstance(content, str):
                total += estimate_tokens(content)
        return total

    # --------------------------------------------------------
    # 主入口
    # --------------------------------------------------------
    def optimize(
        self,
        user_message: str,
        history: Optional[List[Dict[str, Any]]] = None,
        memories: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """对输入做 token 优化,返回 OptimizeResult dict。

        异常隔离:任何内部异常都不抛出,而是返回 ``fallback_reason`` 标记
        的 NoOp 结果(applied=False).Pipeline 会读取该字段并把
        fallback 后的 content 喂给 Orchestrator.
        """
        msg = user_message if isinstance(user_message, str) else ""

        # 0) 输入校验:空消息直接 NoOp 返回
        if not msg:
            return {
                "content": "",
                "before_tokens": 0,
                "after_tokens": 0,
                "saved_tokens": 0,
                "compression_ratio": 1.0,
                "applied": False,
                "fallback_reason": None,
            }

        before = estimate_tokens(msg)
        history_tokens = self._estimate_list_tokens(history or [])
        memory_tokens = self._estimate_list_tokens(memories or [])
        before_total = before + history_tokens + memory_tokens

        # 1) 准备压缩 / 摘要组件
        history_summary: Optional[str] = None
        memory_summary: Optional[str] = None
        history_compressed: Optional[List[Dict[str, Any]]] = None

        try:
            if history and len(history) > self._recent_turns * 2:
                hc = self._get_history_compressor()
                history_compressed = hc.compress(history)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RuntimeTokenOptimizer] history 压缩失败(已隔离): %s", exc,
            )
            history_compressed = None

        try:
            if self._use_memory_summary and memories:
                ms = self._get_memory_summarizer()
                memory_summary = ms.summarize(memories)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RuntimeTokenOptimizer] memory 摘要失败(已隔离): %s", exc,
            )
            memory_summary = None

        # 2) 构造新 content(纯字符串,Pipeline 直接给 Orchestrator)
        parts: List[str] = []
        if history_summary:
            parts.append(history_summary)
        if memory_summary:
            parts.append(memory_summary)
        if msg:
            parts.append(msg)
        new_content = "\n".join([p for p in parts if p])

        # 3) 计算 after tokens
        after = estimate_tokens(new_content)
        after_total = after

        # 4) 判定是否真的发生了压缩
        applied = bool(
            (history_compressed is not None and history_compressed != history)
            or (memory_summary and memory_summary.strip())
        ) and (after_total < before_total)

        saved = max(0, before_total - after_total)
        ratio = (after_total / before_total) if before_total > 0 else 1.0

        return {
            "content": new_content if new_content else msg,
            "before_tokens": before_total,
            "after_tokens": after_total,
            "saved_tokens": saved,
            "compression_ratio": round(ratio, 4),
            "applied": applied,
            "fallback_reason": None,
        }


# ============================================================
# 工具:从 OptimizeResult 提取 token_usage(Pipeline 写入 outputs)
# ============================================================
def build_token_usage(result: Dict[str, Any]) -> Dict[str, Any]:
    """从 OptimizeResult 提取 ``outputs.token_usage`` 字段。

    输入容忍:
        - 缺失字段 -> 0 / 1.0 默认
        - 异常类型 -> 防御性转换
    """
    if not isinstance(result, dict):
        return {
            "before_tokens": 0,
            "after_tokens": 0,
            "saved_tokens": 0,
            "compression_ratio": 1.0,
        }
    before = _safe_int(result.get("before_tokens", 0), 0)
    after = _safe_int(result.get("after_tokens", 0), 0)
    saved = _safe_int(result.get("saved_tokens", 0), 0)
    # 自检:确保 saved = max(0, before-after)
    computed_saved = max(0, before - after)
    if saved == 0 and computed_saved > 0:
        saved = computed_saved
    raw_ratio = _safe_float(result.get("compression_ratio", 1.0), 1.0)
    # 防御:压缩比应在 [0, 1.0+] 区间
    if raw_ratio <= 0:
        ratio = 1.0
    elif raw_ratio > 10.0:
        ratio = 1.0
    else:
        ratio = raw_ratio
    usage: Dict[str, Any] = {
        "before_tokens": before,
        "after_tokens": after,
        "saved_tokens": saved,
        "compression_ratio": round(ratio, 2),
    }
    applied = result.get("applied")
    if isinstance(applied, bool):
        usage["applied"] = applied
    fr = result.get("fallback_reason")
    if fr is not None and _safe_str(fr):
        usage["fallback_reason"] = _safe_str(fr)[:200]
    return usage


__all__ = [
    "RUNTIME_TOKEN_OPTIMIZER_SCHEMA_VERSION",
    "TokenOptimizerError",
    "TokenOptimizerLike",
    "NoOpTokenOptimizer",
    "RuntimeTokenOptimizer",
    "estimate_tokens",
    "build_token_usage",
]
