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


def _empty_audit_context() -> Dict[str, Any]:
    """R-1.0: 审计上下文安全默认（三个子槽位，后续阶段填充，本类无业务逻辑）。"""
    return {
        "mutation_entries": [],
        "approval_context": {},
        "proposal_refs": [],
    }


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

    # ============================================================
    # R-1.0 Runtime Integration：生命周期契约冻结新增字段。
    # 全部带默认值（追加在尾部，不破坏旧构造方式）；不引入新状态存储；
    # 本类不写业务逻辑，这些槽位由 Runtime 各阶段/后续阶段填充。
    # ============================================================
    # 当前轮情绪上下文扩展数据（dominant / intensity / response_strategy 等）
    emotion_context: Dict[str, Any] = field(default_factory=dict)
    # 当前生命周期读取的 self model 快照（Stage 9 产出；正式声明此前
    # 动态属性 ctx.self_model_snapshot，纳入序列化契约）
    self_model_snapshot: Optional[Any] = None
    # 当前周期产生的待治理提案引用列表（与 growth_proposals 语义区分：
    # 后者为阶段产出的 canonical 提案对象；本字段为治理域引用，
    # 如 {"proposal_id", "store", "status"}）
    pending_proposals: List[Any] = field(default_factory=list)
    # 当前周期审计上下文（mutation_entries / approval_context / proposal_refs）
    audit_context: Dict[str, Any] = field(default_factory=_empty_audit_context)

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
            # R-1.0: 新字段安全默认（旧快照无此键时回退；类型不合法时回退默认）
            emotion_context=(
                dict(data.get("emotion_context"))
                if isinstance(data.get("emotion_context"), dict)
                else {}
            ),
            self_model_snapshot=data.get("self_model_snapshot"),
            pending_proposals=(
                list(data.get("pending_proposals"))
                if isinstance(data.get("pending_proposals"), list)
                else []
            ),
            audit_context=(
                dict(data.get("audit_context") or _empty_audit_context())
                if isinstance(data.get("audit_context"), dict)
                else _empty_audit_context()
            ),
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
