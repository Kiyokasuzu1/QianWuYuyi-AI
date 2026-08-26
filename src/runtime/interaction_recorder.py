"""
Phase 4.0-R2.4.1 InteractionRecorder
=====================================

职责（非常克制）：
    一次交互结束后，把它记录成经历。

    → 写入 MemoryProvider.get_store()（R2.3 统一的 Authority 单例）
    → 发布 MemoryCreatedEvent 到全局 EventBus

不做的：
    ❌ 不判断重要性
    ❌ 不触发成长
    ❌ 不修改人格
    ❌ 不调用 GrowthPipeline
    ❌ 不替代 Orchestrator 内部的 Memory 写入（Step 10 保留原样）

设计原则：
    - InteractionRecorder 是一个「生命周期节点」，不是 RuntimePipeline 的内部方法。
      这样 RuntimePipeline 保持纯粹的 Stage 调度职责，不会变成大管家。
    - Pipeline 跑完后调用 recorder.record(ctx)，recorder 独立完成 Memory + Event。
    - 异常完全隔离：recorder 任何失败不影响 Pipeline 返回值。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

# Phase B0: Memory Intake Layer——唯一写入管线（sanitize → classify → truncate）
from src.memory.memory_intake import (
    classify_source,
    sanitize_memory_content,
    truncate_memory_content,
)

logger = logging.getLogger(__name__)


class InteractionRecorder:
    """把一次 RuntimePipeline 交互记录为 Memory 经历。

    使用方式：
        recorder = InteractionRecorder()
        recorder.record(ctx)  # ctx 是 RuntimePipeline.run() 返回的 RuntimeContext

    每次调用 record() 会：
        1. 从 ctx.outputs["snapshot"] 提取 user_message + reply
        2. 构造 memory_record dict
        3. 写入 MemoryProvider.get_store().add(record)
        4. 发布 MemoryCreatedEvent 到全局 EventBus
    """

    def __init__(self) -> None:
        # 延迟导入，避免循环依赖
        from src.memory.memory_provider import MemoryProvider

        self._memory_provider = MemoryProvider
        # R-1.2: 向量索引惰性单例（首次成功写入记忆后才创建；
        # chroma 客户端构造较重，避免每轮重复初始化）
        self._vector_memory = None

    def _resolve_vector(self):
        """R-1.2: 惰性获取向量索引（失败返回 None，不阻断聊天）。"""
        if self._vector_memory is not None:
            return self._vector_memory
        try:
            from src.memory.vector import VectorMemory

            self._vector_memory = VectorMemory()
        except Exception as e:  # noqa: BLE001
            logger.warning("[InteractionRecorder] 向量索引初始化失败（已隔离）: %s", e)
            self._vector_memory = None
        return self._vector_memory

    def _resolve_store(self):
        """V1.0-1B: bridge-first 获取 MemoryStore authority。

        RuntimeCore 配置了 memory_store_path 时，运行时权威 store 与
        MemoryProvider 单例不是同一个对象——必须先经 RuntimeBridge 获取，
        Provider 仅作 fallback（旧行为保留，fail-soft）。
        """
        try:
            from src.runtime.runtime_bridge import get_runtime_bridge
            _store = get_runtime_bridge().get_memory_store()
            if _store is not None:
                return _store
        except Exception:
            pass
        return self._memory_provider.get_store()

    @property
    def store(self):
        """暴露当前使用的 MemoryStore（用于 Identity Gate 断言）。"""
        return self._resolve_store()

    def record(self, ctx: Any) -> Optional[str]:
        """把一次交互记录到 Memory + 发布事件。

        Args:
            ctx: RuntimePipeline.run() 返回的 RuntimeContext（或任何有 outputs 属性的对象）

        Returns:
            成功时返回 memory_id，失败时返回 None。
            任何异常都被隔离，不会向上传播。
        """
        try:
            outputs: Dict[str, Any] = getattr(ctx, "outputs", None) or {}
            snapshot: Dict[str, Any] = outputs.get("snapshot") or {}

            user_message = snapshot.get("user_message", "")
            reply = snapshot.get("reply", "")

            # 空消息不记录（避免无意义空记录）
            if not user_message or not str(user_message).strip():
                return None

            # Phase B0: 接入 Memory Intake Layer。
            # 截断参数与 Orchestrator 完全一致（3500/2500/800）→ 双路径逐字节一致；
            # 3500 留 500 裕量低于 PollutionGuard 上限 4000。
            clean_text, stripped_flags = sanitize_memory_content(str(user_message))
            clean_text = truncate_memory_content(
                clean_text, max_len=3500, head_keep=2500, tail_keep=800
            )
            if not clean_text:
                # 全部为宿主注入块 → 无用户内容，不记录
                logger.warning(
                    "[InteractionRecorder] 记忆内容清洗后为空（疑似注入），跳过记录"
                )
                return None

            # 构造 memory record（dict 格式，与 Orchestrator L866 一致）
            now = datetime.now()
            memory_id = f"mem_{now.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:4]}"

            # Phase 4.0.1 Step 02-B R-1: 从 ctx.inputs 读取 user_id，
            # 缺失时回退默认 owner（保持单用户兼容）
            ctx_inputs: Dict[str, Any] = getattr(ctx, "inputs", None) or {}
            user_id = ctx_inputs.get("user_id") or "366648462"
            # v1.5.5 Governance C1: frontend provenance——请求来源前端。
            # 由 api_server 注入 ctx.inputs["frontend"]（IP 判定：qq/mc/unknown）；
            # 缺失时尝试从快照读，仍缺失 → unknown（不猜测来源）。
            frontend = str(ctx_inputs.get("frontend") or "") or str(
                (getattr(ctx, "outputs", None) or {}).get("frontend") or ""
            )
            if not frontend:
                frontend = "unknown"

            # M1-3: importance 不再恒 0.5——纯规则 scorer（零 LLM、零 IO），
            # 按 content/类别信号给基本区分度；无信号回落 0.5（原行为兼容）。
            try:
                from src.memory.memory_intake import score_importance
                _importance = score_importance(
                    clean_text, memory_type="user_experience", metadata=ctx_inputs,
                )
            except Exception:  # noqa: BLE001
                _importance = 0.5

            memory_record = {
                "id": memory_id,
                "content": clean_text,
                "timestamp": now.isoformat(),
                "user_id": user_id,
                "role": "user",
                "importance": _importance,
                "source_event_id": "",
                "emotion_tag": "",
                "relationship_id": user_id,
                "metadata": {
                    "memory_type": "user_experience",
                    "source": "runtime_pipeline",
                    "reply": str(reply)[:500] if reply else "",  # 截断防止过大
                    # v1.5-T4: 事件 schema 扩展（固定值，语义推断属后续版本）。
                    # origin: 事件来源（本记录器只处理用户交互，恒 user）
                    # event_class: 事件分类（交互类）
                    # self_involvement: 自身卷入（v0 默认 witness，agent/patient 留系统事件）
                    # authored: 是否羽依署名（用户消息非她署名，故 True 表"记录的是用户经历"）
                    "origin": "user",
                    "event_class": "interaction",
                    "self_involvement": "witness",
                    "authored": True,
                    # v1.5.5 Governance C1: 前端来源（qq/mc/unknown）
                    "frontend": frontend,
                },
            }
            # Phase B0: 污染来源打标（无污染时零改动；经既有 metadata dict）
            memory_record = classify_source(
                memory_record,
                writer="runtime_pipeline",
                stripped_flags=stripped_flags,
            )

            # 写入 Memory（V1.0-1B: bridge-first Authority，Provider 仅 fallback）
            store = self._resolve_store()
            saved = store.add(memory_record)
            if saved is None:
                # Phase B0: 写入被拒（guard/权限门）——不发布事件、不记成功日志
                logger.warning(
                    "[InteractionRecorder] 记忆写入被拒（不发布事件）: id=%s", memory_id
                )
                return None

            # R-1.2: 向量索引同步（复用 orchestrator Step 10 的 fail-soft 模式）——
            # 向量不可用时不得阻断聊天；不改变 memory 内容、不改 embedding/schema。
            try:
                vector = self._resolve_vector()
                if vector is not None:
                    vector.add_memory(memory_record)
            except Exception as e:  # noqa: BLE001
                logger.warning("[InteractionRecorder] 向量同步失败（已隔离）: %s", e)

            # 发布 MemoryCreatedEvent（使用全局 EventBus）
            try:
                from src.events.bus import publish_event
                from src.events.events import MemoryCreatedEvent

                publish_event(MemoryCreatedEvent(
                    memory_id=memory_id,
                    user_id=memory_record["user_id"],
                    content=memory_record["content"],
                ))
            except Exception as e:  # noqa: BLE001
                logger.warning("[InteractionRecorder] MemoryCreatedEvent 发布失败: %s", e)

            logger.info(
                "[InteractionRecorder] 交互已记录: id=%s, content=%s...",
                memory_id,
                clean_text[:50],
            )
            return memory_id

        except Exception as e:  # noqa: BLE001
            logger.warning("[InteractionRecorder] record() 异常（已隔离）: %s", e)
            return None
