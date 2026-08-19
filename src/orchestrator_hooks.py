from datetime import datetime, timezone
import json
import uuid
from pathlib import Path

from src.memory.atomic_write import atomic_write_json, get_path_lock
from src.memory.vector import VectorMemory
from src.memory.memory_store import MemoryStore
from src.relationship.relationship_event import RelationshipEvent
from src.relationship.relationship_evaluator import RelationshipEvaluator
from src.runtime.runtime_context import RuntimeContext

# Orchestrator 的情绪与关系后处理集成
# 在 process() 中已经在前面集成了 RuntimeContext.assemble_context 的调用，
# 这里我们补充情绪的 pre/post 处理与关系事件评估逻辑，并持久化用户相关状态。

# 我们实现为几个独立函数，易于测试与调试

def _process_emotion_pre(assembled_context, user_message: str, user_id: str):
    """在生成回复之前处理用户消息引起的情绪事件，非阻塞。
    使用 assembled_context 中的 emotion_manager（如果存在），否则降级。
    """
    try:
        em_manager = assembled_context.get("emotion_manager") if assembled_context else None
        if not em_manager:
            return assembled_context
        # 构造简化的 EmotionEvent（项目中应替换为更复杂的情绪事件抽取）
        from src.emotion.emotion_event import EmotionEvent
        ev = EmotionEvent(source="user_message", content=user_message, timestamp=datetime.now().isoformat())
        em_manager.process_event(ev)
        # 更新 trace
        if assembled_context is not None:
            assembled_context["trace"].append(f"emotion_bridge_event: processed user_message at {datetime.now().isoformat()}")
        return assembled_context
    except Exception as e:
        print(f"[Orchestrator] emotion pre-processing failed: {e}")
        return assembled_context


def _process_emotion_post(assembled_context, reply: str, user_id: str):
    """在生成回复后根据回复内容微调情绪并持久化到 data/emotions/{user_id}.json"""
    try:
        em_manager = assembled_context.get("emotion_manager") if assembled_context else None
        if not em_manager:
            return assembled_context
        from src.emotion.emotion_event import EmotionEvent
        ev = EmotionEvent(source="assistant_reply", content=reply, timestamp=datetime.now().isoformat())
        em_manager.process_event(ev)
        # 持久化到 per-user 文件
        user_emotion_path = Path("data/emotions")
        user_emotion_path.mkdir(parents=True, exist_ok=True)
        per_user_file = user_emotion_path / f"{user_id}.json"
        try:
            # 尝试使用 repository 保存（若 repository 支持 filepath）
            repo = getattr(em_manager, "repository", None)
            if repo is not None and hasattr(repo, "save"):
                try:
                    repo.filepath = per_user_file
                    repo.save(em_manager.state)
                except Exception:
                    # 降级：锁 + 原子写 state dict（V1.1 替换裸 open("w")）
                    with get_path_lock(str(per_user_file)):
                        atomic_write_json(
                            str(per_user_file),
                            em_manager.state.to_dict() if hasattr(em_manager.state, "to_dict") else {},
                        )
            else:
                with get_path_lock(str(per_user_file)):
                    atomic_write_json(
                        str(per_user_file),
                        em_manager.state.to_dict() if hasattr(em_manager.state, "to_dict") else {},
                    )
        except Exception as e:
            print(f"[Orchestrator] persist emotion state failed: {e}")
        # 在 trace 中记录
        if assembled_context is not None:
            assembled_context["trace"].append(f"emotion_bridge_event: processed assistant_reply and persisted to {per_user_file}")
        # trigger on_emotion_change if significant change (e.g., dominant label changed)
        try:
            if assembled_context and assembled_context.get("on_emotion_change"):
                # simplified: assume em_manager.state has a 'dominant' attribute
                dominant = getattr(em_manager.state, "dominant", None) or getattr(em_manager.state, "primary_emotion", None)
                intensity = getattr(em_manager.state, "intensity", None) or 0.0
                # 非阻塞触发
                try:
                    cb = assembled_context.get("on_emotion_change")
                    if cb:
                        cb(dominant, intensity)
                except Exception as e:
                    print(f"[Orchestrator] emotion change callback failed: {e}")
        except Exception:
            pass
        return assembled_context
    except Exception as e:
        print(f"[Orchestrator] emotion post-processing failed: {e}")
        return assembled_context


# ============================================================
# P4.2-IMPL-C6A: relationship_profile legacy bypass 写入唯一入口
# ============================================================
# 契约（Write path contract）：
# - 生产代码中禁止在本函数之外直接 `rel_profile["trust"] = ...` 类旁路写入
#   （由 tests/test_phase_4_2_c6a_relationship_write_bypass.py AST 检查强制）；
# - 本入口只写 Phase 7 legacy profile dict（持久化对象：
#   data/relationships/{user_id}.json，经 relationship_repo.save()），
#   不触碰 v0.6 RelationshipState / v3.5.27 RelationshipState 两个时间尺度权威，
#   因此不会污染任一时间尺度；
# - 每次写入记录 audit log（relationship.bypass_write，含 before/after + provenance），
#   可追溯、可回滚；
# - 本入口属于 legacy 兼容路径，禁止新增调用；
#   退役计划：后续阶段迁移到 RelationshipModel.apply_event 事件写入。
# 允许的例外白名单（同样被 AST 测试声明）：
# - src/personality/relationship_evolution.py: 本地 deltas 计算 dict（不落盘，
#   随后经 update_trust/update_familiarity 官方入口写入）
# - src/response_phase4/persistence_manager.py + src/runtime/runtime_controller.py:
#   phase4 第三套 schema（P4.2-C6-3 范围，本阶段明确不动）


def apply_legacy_relationship_profile_delta(
    rel_profile: dict,
    rel_repo,
    *,
    trust_delta: float,
    familiarity_delta: float,
    event: dict = None,
    user_id: str = "",
    source: str = "orchestrator",
    reason: str = "legacy interaction post-processing",
):
    """对 legacy relationship_profile 应用 trust/familiarity 增量并持久化。

    Returns:
        dict: {"old_trust","new_trust","old_familiarity","new_familiarity",
               "trust_delta_applied","familiarity_delta_applied","saved"}
        rel_profile 不是 dict 或 rel_repo 不可用时返回 None（fail-soft）。
    """
    if not isinstance(rel_profile, dict):
        return None
    try:
        old_trust = float(rel_profile.get("trust", 0.5))
        old_familiarity = float(rel_profile.get("familiarity", 0.0))
        new_trust = max(0.0, min(1.0, old_trust + float(trust_delta)))
        new_familiarity = max(0.0, min(1.0, old_familiarity + float(familiarity_delta)))

        # ---- C6A: 生产代码中唯一允许的 relationship raw dict 写入点 ----
        rel_profile["trust"] = new_trust
        rel_profile["familiarity"] = new_familiarity
        if event is not None:
            rel_profile.setdefault("events", []).append(dict(event))

        saved = False
        if rel_repo is not None and hasattr(rel_repo, "save"):
            try:
                rel_repo.save(rel_profile)
                saved = True
            except Exception as e:  # noqa: BLE001
                print(f"[Orchestrator] relationship persist failed: {e}")

        # provenance / audit：旁路写入必须留痕
        try:
            from src.audit.record import record_audit_log
            record_audit_log(
                operation_type="relationship.bypass_write",
                source=source,
                action="legacy relationship_profile 旁路写入",
                user_id=user_id,
                before_state={"trust": old_trust, "familiarity": old_familiarity},
                after_state={"trust": new_trust, "familiarity": new_familiarity},
                detail={
                    "trust_delta_applied": new_trust - old_trust,
                    "familiarity_delta_applied": new_familiarity - old_familiarity,
                    "reason": reason,
                    "provenance": "p4.2-impl-c6a legacy entry",
                    "persisted": saved,
                    "event_id": (event or {}).get("id", ""),
                },
            )
        except Exception:  # noqa: BLE001
            # audit 失败不阻断写入路径本身
            pass

        return {
            "old_trust": old_trust,
            "new_trust": new_trust,
            "old_familiarity": old_familiarity,
            "new_familiarity": new_familiarity,
            "trust_delta_applied": new_trust - old_trust,
            "familiarity_delta_applied": new_familiarity - old_familiarity,
            "saved": saved,
        }
    except Exception as e:  # noqa: BLE001
        print(f"[Orchestrator] relationship persist failed: {e}")
        return None


def _process_relationship_post(assembled_context, user_message: str, reply: str, chat_memories: list, user_id: str):
    """在对话后构造 RelationshipEvent，评估并更新关系画像，持久化到 data/relationships/{user_id}.json

    [LEGACY] P4.2-IMPL-C6A：本函数无生产调用方（Orchestrator 类内同名方法为活跃路径）。
    写入已收敛到 apply_legacy_relationship_profile_delta 唯一入口。
    """
    try:
        rel_repo = assembled_context.get("relationship_repo") if assembled_context else None
        rel_profile = assembled_context.get("relationship_profile") if assembled_context else None
        if not rel_repo:
            return assembled_context
        # 构造简单的 RelationshipEvent（Phase 4.2-A：统一 TypedDict 契约。
        # 注：type="interaction" 不在统一类型枚举内，evaluator 会拒绝——
        # 与断裂前行为一致；通用互动本就不应构成关系事件，待 4.2 后续阶段退役此路径）
        event = RelationshipEvent(
            id=f"rel_{uuid.uuid4().hex[:8]}",
            type="interaction",
            content=f"Interaction length {len(user_message)}",
            source_memory_id="",
            confidence=0.6,
            created_at=datetime.now(timezone.utc).isoformat(),
            status="observed",
            meaning=None,
            memory_type=None,
            user_id=user_id,
            participants=["user", "yuyi"],
            evidence_ids=[m.get("id") for m in chat_memories if m.get("id")],
            potential_dimensions=["trust_building"],
        )
        evaluator = RelationshipEvaluator()
        res = evaluator.evaluate(event)
        # 更新 profile 简单逻辑：通过则 trust 增加，否则小幅下降
        # P4.2-IMPL-C6A：写入收敛到唯一 legacy 入口（含 clamp + audit + provenance）
        try:
            if isinstance(rel_profile, dict):
                result = apply_legacy_relationship_profile_delta(
                    rel_profile,
                    rel_repo,
                    trust_delta=(0.05 if res.passed else -0.02),
                    familiarity_delta=0.02,
                    event=dict(event),
                    user_id=user_id,
                    source="orchestrator_hooks",
                )
                if result:
                    assembled_context["trace"].append(
                        f"relationship_event: {event['id']} evaluated passed={res.passed} "
                        f"trust {result['old_trust']}->{result['new_trust']}"
                    )
        except Exception as e:
            print(f"[Orchestrator] relationship persist failed: {e}")
        return assembled_context
    except Exception as e:
        print(f"[Orchestrator] relationship post-processing failed: {e}")
        return assembled_context
