# -*- coding: utf-8 -*-
"""
src/runtime/adapters/context_normalizer.py

P2.3-A.3.2 —— RuntimeContext 唯一归一化入口。

统一两个旧入口（均保留原函数签名作为 wrapper 委托本模块，调用点零改动）：
- runtime_core.py:_normalize_runtime_ctx（RuntimeCore.process 第 2 步）
- lifecycle_executor.py:_normalize_mutable_ctx（LifecycleExecutor.execute 第 0 步）

支持输入：
- mutable RuntimeContext（context/runtime_context.py）→ 原对象直通
- lifecycle_context.RuntimeContext v1（frozen）→ 字段投影 + 动态字段补齐
- request_context.RuntimeContext v2（frozen）→ 字段投影 + 动态字段补齐
- legacy dict → from_dict 重建 + 动态字段补齐
- None → 全新 mutable
- 其余无法识别输入 → ContextNormalizerError（wrapper 层捕获并回退全新
  mutable，保持旧宽容行为不变）

语义基准：现 runtime_core 版归一化器（superset）。漂移项裁决见
docs/architecture/runtime_context_normalization_plan.md §3.2：
- D1/D3 user_id 双源拷贝、D2 recent_history→history/_recent_history：
  以 core 版为准（本入口统一补齐，executor 直调路径由此获得同能力）。
- D4 失效守卫（mutable.timestamp == mutable.timestamp 自比较）删除，
  效果与无条件覆盖等价。
- D5 user_input 回填前置统一为 core 版语义（先十字段、后回填）。

设计裁决（与 plan §5.1 upcast_to_mutable 的差异）：
现有 adapter 家族（from_lifecycle_context / from_mutable_context /
from_legacy_dict / to_legacy_view）输出方向均为 →v2，无 →mutable 转换体；
经 v2 中转会改变 v1 默认行为（schema_version / 字段面漂移），与本任务
「禁止改变默认 v1 行为」冲突。故本入口直接做纯字段投影，不调用 adapter。

边界：
- 本模块禁止 import runtime_core / lifecycle_executor / orchestrator /
  memory / emotion / growth / personality（保证两个调用点均可安全 import）。
- 只做类型识别 + 字段搬运，禁止任何业务逻辑（理解 / 评估 / 决策）。
- 不产生任何文件 / 网络写操作。
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from src.runtime.context.runtime_context import RuntimeContext

logger = logging.getLogger(__name__)


class ContextNormalizerError(TypeError):
    """输入类型无法归一化（wrapper 层回退全新 mutable，保留旧宽容行为）。"""


#: 十字段拷贝清单（与旧实现一致，冻结：删减即行为变化）
_SAME_NAME_ATTRS = (
    "session_id", "user_input", "timestamp", "schema_version",
    "memory_context", "emotion_state", "personality_snapshot",
    "growth_proposals", "identity_context_text", "identity_snapshot_ref",
)

#: 空值判定（与旧实现一致：None / "" / [] / {} 均视为未携带）
_EMPTY_VALUES = (None, "", [], {})


def _copy_same_name_attrs(mutable: RuntimeContext, external_ctx: Any) -> None:
    """同名字段投影（getattr 兜底 + 空值跳过 + setattr 失败吞掉，旧语义）。"""
    for attr in _SAME_NAME_ATTRS:
        try:
            value = getattr(external_ctx, attr, None)
        except Exception:  # noqa: BLE001
            value = None
        if value not in _EMPTY_VALUES:
            try:
                setattr(mutable, attr, value)
            except Exception:  # noqa: BLE001
                pass


def _apply_inputs(mutable: RuntimeContext, inputs: Dict[str, Any]) -> None:
    """inputs 动态字段补齐（D1/D2/D5，core 版语义）。

    - user_input / content 回填 mutable.user_input
    - user_id → mutable._ctx_user_id（无条件，None 才跳过）
    - recent_history → mutable.history + mutable._recent_history（净化后）
    """
    if not mutable.user_input:
        candidate = inputs.get("user_input") or inputs.get("content")
        if isinstance(candidate, str) and candidate.strip():
            mutable.user_input = candidate
    user_id = inputs.get("user_id")
    if user_id is not None:
        try:
            mutable._ctx_user_id = user_id  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    recent_history = inputs.get("recent_history")
    if isinstance(recent_history, list) and recent_history:
        cleaned = [
            item for item in recent_history
            if isinstance(item, dict) and isinstance(item.get("content"), str)
        ]
        if cleaned:
            try:
                mutable.history = cleaned  # type: ignore[attr-defined]
                mutable._recent_history = cleaned  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass


def _apply_metadata_user_id(mutable: RuntimeContext, metadata: Dict[str, Any]) -> None:
    """metadata.user_id 兜底（D3，仅在 _ctx_user_id 尚未设置时）。"""
    user_id = metadata.get("user_id")
    if user_id is not None and not getattr(mutable, "_ctx_user_id", None):
        try:
            mutable._ctx_user_id = user_id  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def _apply_started_at(mutable: RuntimeContext, started_at: Any) -> None:
    """started_at → mutable.timestamp（D4：无条件覆盖，旧守卫恒真等价）。"""
    if isinstance(started_at, str) and started_at:
        mutable.timestamp = started_at


def _is_context_object(external_ctx: Any) -> bool:
    """识别上下文对象（lifecycle v1 / request v2 / 其他 context-like，duck-typed）。

    判定面与旧实现的「有可拷贝内容」完全一致：任一同名字段非空、或
    inputs / metadata 是 dict、或 started_at 是有效 ISO 串。这样 normalizer
    拒绝的输入恰好是旧实现「抄不到任何东西」的输入，wrapper 回退后行为不变。
    """
    for attr in _SAME_NAME_ATTRS:
        try:
            if getattr(external_ctx, attr, None) not in _EMPTY_VALUES:
                return True
        except Exception:  # noqa: BLE001
            continue
    for attr in ("inputs", "metadata"):
        try:
            if isinstance(getattr(external_ctx, attr, None), dict):
                return True
        except Exception:  # noqa: BLE001
            continue
    try:
        started_at = getattr(external_ctx, "started_at", None)
        if isinstance(started_at, str) and started_at:
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _apply_dict_extras(mutable: RuntimeContext, data: Dict[str, Any]) -> None:
    """legacy dict 的附加键补齐（user_id / metadata.user_id / 历史 / content）。

    from_dict 已承接 session_id / user_input / timestamp / 模块快照 /
    schema_version / identity_context_text；此处补齐 dict 形态下的动态字段。
    """
    inputs_view = {
        "user_input": data.get("user_input"),
        "content": data.get("content"),
        "user_id": data.get("user_id"),
        "recent_history": data.get("recent_history") or data.get("history"),
    }
    _apply_inputs(mutable, inputs_view)
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        _apply_metadata_user_id(mutable, metadata)
    _apply_started_at(mutable, data.get("started_at"))


def normalize_context(external_ctx: Any) -> RuntimeContext:
    """把任意输入归一化为 RuntimeCore 工作上下文（mutable RuntimeContext）。

    输入类型判定顺序（首匹配即返回）：
    1. mutable RuntimeContext → 原对象直通（isinstance fast path）
    2. None → 全新 mutable
    3. legacy dict → from_dict 重建 + 动态字段补齐
    4. 上下文对象（v1 / v2 / context-like）→ 十字段投影 + 动态字段补齐
    5. 其余 → ContextNormalizerError（拒绝）

    Returns:
        mutable RuntimeContext（第 1 种情况为输入对象本身）。

    Raises:
        ContextNormalizerError: 输入无法识别（旧入口 wrapper 捕获后回退
        全新 mutable，保证旧调用点宽容行为不变）。
    """
    # 1. mutable 直通
    if isinstance(external_ctx, RuntimeContext):
        return external_ctx

    # 2. None → 全新
    if external_ctx is None:
        return RuntimeContext()

    # 3. legacy dict
    if isinstance(external_ctx, dict):
        mutable = RuntimeContext.from_dict(external_ctx)
        _apply_dict_extras(mutable, external_ctx)
        return mutable

    # 4. 上下文对象（frozen v1 / v2 / context-like）
    if _is_context_object(external_ctx):
        mutable = RuntimeContext()
        _copy_same_name_attrs(mutable, external_ctx)
        try:
            inputs = getattr(external_ctx, "inputs", None)
            if isinstance(inputs, dict):
                _apply_inputs(mutable, inputs)
        except Exception:  # noqa: BLE001
            pass
        try:
            metadata = getattr(external_ctx, "metadata", None)
            if isinstance(metadata, dict):
                _apply_metadata_user_id(mutable, metadata)
        except Exception:  # noqa: BLE001
            pass
        try:
            _apply_started_at(mutable, getattr(external_ctx, "started_at", None))
        except Exception:  # noqa: BLE001
            pass
        return mutable

    # 5. 拒绝
    raise ContextNormalizerError(
        f"无法归一化的输入类型: {type(external_ctx).__module__}."
        f"{type(external_ctx).__name__}"
    )
