from datetime import datetime
import json
from pathlib import Path

from src.memory.vector import VectorMemory
from src.memory.memory_store import MemoryStore
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
                    # 降级：直接写 state dict
                    with open(per_user_file, "w", encoding="utf-8") as f:
                        json.dump(em_manager.state.to_dict() if hasattr(em_manager.state, "to_dict") else {}, f, ensure_ascii=False, indent=2)
            else:
                with open(per_user_file, "w", encoding="utf-8") as f:
                    json.dump(em_manager.state.to_dict() if hasattr(em_manager.state, "to_dict") else {}, f, ensure_ascii=False, indent=2)
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


def _process_relationship_post(assembled_context, user_message: str, reply: str, chat_memories: list, user_id: str):
    """在对话后构造 RelationshipEvent，评估并更新关系画像，持久化到 data/relationships/{user_id}.json"""
    try:
        rel_repo = assembled_context.get("relationship_repo") if assembled_context else None
        rel_profile = assembled_context.get("relationship_profile") if assembled_context else None
        if not rel_repo:
            return assembled_context
        # 构造简单的 RelationshipEvent
        event = RelationshipEvent(
            event_id=f"rel_{uuid.uuid4().hex[:8]}",
            event_type="interaction",
            evidence_ids=[m.get("id") for m in chat_memories if m.get("id")],
            signal_strength=0.6,
            potential_dimensions=set(["trust_building"]),
            description=f"Interaction length {len(user_message)}",
            timestamp=datetime.now().isoformat()
        )
        evaluator = RelationshipEvaluator()
        res = evaluator.evaluate(event)
        # 更新 profile 简单逻辑：通过则 trust 增加，否则小幅下降
        try:
            if isinstance(rel_profile, dict):
                old_trust = rel_profile.get("trust", 0.5)
                new_trust = max(0.0, min(1.0, old_trust + (0.05 if res.passed else -0.02)))
                rel_profile["trust"] = new_trust
                rel_profile["familiarity"] = rel_profile.get("familiarity", 0.0) + 0.02
                rel_profile.setdefault("events", []).append(event.to_dict())
                rel_repo.save(rel_profile)
                assembled_context["trace"].append(f"relationship_event: {event.event_id} evaluated passed={res.passed} trust {old_trust}->{new_trust}")
        except Exception as e:
            print(f"[Orchestrator] relationship persist failed: {e}")
        return assembled_context
    except Exception as e:
        print(f"[Orchestrator] relationship post-processing failed: {e}")
        return assembled_context
