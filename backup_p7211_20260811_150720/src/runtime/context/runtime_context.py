# src/runtime/context/runtime_context.py
"""
RuntimeContext dataclass —— Phase 3.7.0

RuntimeContext 是一次"思维循环"中所有模块共享的上下文快照。

字段契约（v1.0 — Phase 3.7.0 冻结）：
- session_id            str
- user_input            str
- timestamp             str
- memory_context        Optional[Any]   # MemoryContext（来自 Memory 模块）
- emotion_state         Optional[Any]   # EmotionState（来自 Emotion 模块）
- personality_snapshot  Optional[Any]   # PersonalitySnapshot（来自 Personality 模块）
- growth_proposals      List[Any]       # List[GrowthProposal]（canonical，来自 Growth 模块）

字段类型使用 Any 是因为 Runtime 不知道也不关心业务模块的精确类型；
Runtime 只持有"快照"或"引用"，不复制 / 缓存模块内部状态。

依赖：仅 stdlib
禁止：import src.memory / src.emotion / src.personality / src.growth

Phase C.10.6: 此文件从 src/runtime/context.py 迁移至 src/runtime/context/runtime_context.py
              行为完全不变,只调整包结构以容纳 control_context 子模块。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from datetime import datetime
import uuid


# Phase 3.7.0: 当前 schema_version
RUNTIME_CONTEXT_SCHEMA_VERSION = "1.0"


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _empty_growth_proposals() -> List[Any]:
    return []


@dataclass
class RuntimeContext:
    """Runtime 共享上下文（v1.0）

    字段说明详见 docs/runtime.md §3。
    """
    session_id: str = field(default_factory=lambda: f"session_{uuid.uuid4().hex[:12]}")
    user_input: str = ""
    timestamp: str = field(default_factory=_now_iso)
    # 模块产出物（引用 / 快照）
    memory_context: Optional[Any] = None
    emotion_state: Optional[Any] = None
    personality_snapshot: Optional[Any] = None
    growth_proposals: List[Any] = field(default_factory=_empty_growth_proposals)
    # Phase 3.7.0: schema 版本
    schema_version: str = RUNTIME_CONTEXT_SCHEMA_VERSION

    # Phase 4.0.3 Identity Context: 身份连续性+锚点+稳定性 生成的自然语言摘要
    # 由 IdentityContextBuilder（Stage6 末尾）写入，Stage14 原样透传给 engine.generate
    # 默认为 ""（不是 None），避免 Stage14 处有类型歧义
    identity_context_text: str = ""
    # Phase 4.0.3 Identity Snapshot Reference（可选）：
    # IdentityContextBuilder 处理时引用的 last IdentitySnapshot（Any 类型防环），
    # 仅用于审计/诊断；from_dict 时不可用，设为 None
    identity_snapshot_ref: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        """序列化(业务对象保持引用,调用方负责转换)。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuntimeContext":
        """反序列化(缺省 schema_version 回退到 v1.0,backward compatibility)。"""
        return cls(
            session_id=data.get("session_id")
                or f"session_{uuid.uuid4().hex[:12]}",
            user_input=data.get("user_input", "") or "",
            timestamp=data.get("timestamp") or _now_iso(),
            memory_context=data.get("memory_context"),
            emotion_state=data.get("emotion_state"),
            personality_snapshot=data.get("personality_snapshot"),
            growth_proposals=data.get("growth_proposals", []) or [],
            schema_version=(
                data.get("schema_version")
                or RUNTIME_CONTEXT_SCHEMA_VERSION
            ),
            # Phase 4.0.3: identity 字段可选；旧快照无此字段时回退安全默认值
            identity_context_text=(data.get("identity_context_text", "") or ""),
            # identity_snapshot_ref: 序列化丢失引用很正常，反序列化时统一置 None
            # （不尝试从字典恢复业务对象引用）
            identity_snapshot_ref=None,
        )

    def has_memory(self) -> bool:
        return self.memory_context is not None

    def has_emotion(self) -> bool:
        return self.emotion_state is not None

    def has_personality(self) -> bool:
        return self.personality_snapshot is not None

    def has_growth(self) -> bool:
        return len(self.growth_proposals) > 0

    def has_identity_context(self) -> bool:
        """Phase 4.0.3: identity_context_text 有非空内容。"""
        text = self.identity_context_text or ""
        return bool(text.strip())
