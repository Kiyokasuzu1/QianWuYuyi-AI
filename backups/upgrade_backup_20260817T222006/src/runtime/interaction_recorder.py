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

    @property
    def store(self):
        """暴露当前使用的 MemoryStore（用于 Identity Gate 断言）。"""
        return self._memory_provider.get_store()

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

            # 构造 memory record（dict 格式，与 Orchestrator L866 一致）
            now = datetime.now()
            memory_id = f"mem_{now.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:4]}"

            # Phase 4.0.1 Step 02-B R-1: 从 ctx.inputs 读取 user_id，
            # 缺失时回退默认 owner（保持单用户兼容）
            ctx_inputs: Dict[str, Any] = getattr(ctx, "inputs", None) or {}
            user_id = ctx_inputs.get("user_id") or "366648462"

            memory_record = {
                "id": memory_id,
                "content": str(user_message),
                "timestamp": now.isoformat(),
                "user_id": user_id,
                "role": "user",
                "importance": 0.5,
                "source_event_id": "",
                "emotion_tag": "",
                "relationship_id": user_id,
                "metadata": {
                    "memory_type": "user_experience",
                    "source": "runtime_pipeline",
                    "reply": str(reply)[:500] if reply else "",  # 截断防止过大
                },
            }

            # 写入 Memory（使用 R2.3 统一的 Authority）
            store = self._memory_provider.get_store()
            store.add(memory_record)

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
                str(user_message)[:50],
            )
            return memory_id

        except Exception as e:  # noqa: BLE001
            logger.warning("[InteractionRecorder] record() 异常（已隔离）: %s", e)
            return None
