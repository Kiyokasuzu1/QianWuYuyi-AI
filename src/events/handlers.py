import threading
from typing import Any, Optional

from src.events.events import YuyiEvent, EventType
from src.events.bus import subscribe_event

# Phase 2.4: ExperienceBridge 启动绑定一次 —— 共享 Runtime 权威
# GrowthIntegrationService（复用注入的 growth_history / self_model_store），
# 禁止每次 MemoryCreatedEvent 都重建 growth 通路 writer。
_experience_bridge_instance: Optional[Any] = None
_experience_bridge_lock = threading.Lock()


def _get_shared_experience_bridge(record_lookup: Any) -> Any:
    """惰性单例：首次 MemoryCreatedEvent 时绑定一次 ExperienceBridge。

    growth_handler 绑定 RuntimeCore 权威 GrowthIntegrationService.accept_experience
    （实例方法，写入共享 growth_history）；桥接不可用时 growth_handler=None，
    Bridge 走旧默认 accept_experience_static（旧行为保留，fail-soft）。
    """
    global _experience_bridge_instance
    if _experience_bridge_instance is None:
        with _experience_bridge_lock:
            if _experience_bridge_instance is None:
                from src.experience.experience_bridge import ExperienceBridge

                growth_handler = None
                try:
                    from src.runtime.runtime_bridge import get_runtime_bridge

                    _core = get_runtime_bridge().get_runtime_core()
                    _factory = getattr(_core, "_get_growth_integration_service", None)
                    if _factory is not None:
                        _service = _factory()
                        if _service is not None:
                            growth_handler = _service.accept_experience
                except Exception:
                    growth_handler = None
                _experience_bridge_instance = ExperienceBridge(
                    growth_handler=growth_handler,
                    record_lookup=record_lookup,
                )
    return _experience_bridge_instance


def register_builtin_handlers():
    subscribe_event(EventType.MESSAGE_RECEIVED, audit_event_handler)
    subscribe_event(EventType.MESSAGE_RESPONDED, audit_event_handler)
    subscribe_event(EventType.MEMORY_CREATED, audit_event_handler)
    subscribe_event(EventType.PERSONALITY_CHANGED, audit_event_handler)
    subscribe_event(EventType.RELATIONSHIP_CHANGED, audit_event_handler)
    subscribe_event(EventType.GROWTH_EVENT_DETECTED, audit_event_handler)
    subscribe_event(EventType.GROWTH_PROPOSAL_CREATED, audit_event_handler)
    subscribe_event(EventType.GROWTH_PROPOSAL_APPROVED, audit_event_handler)
    subscribe_event(EventType.GROWTH_PROPOSAL_REJECTED, audit_event_handler)
    subscribe_event(EventType.GROWTH_APPLIED, audit_event_handler)

    subscribe_event(EventType.PERSONALITY_CHANGED, create_proposal_from_event)
    subscribe_event(EventType.RELATIONSHIP_CHANGED, create_proposal_from_event)

    # Phase 4.0 R2.5.1: MemoryCreatedEvent -> ExperienceBridge
    subscribe_event(EventType.MEMORY_CREATED, experience_bridge_memory_created_handler)

    # Phase 2.5-D: MemoryCreatedEvent -> RelationshipCandidateBridge
    # 关系候选提取是事件消费者(与 GrowthPipeline 主动调用解耦):
    # 未来 QQ/网页/语音/游戏任何入口发布 MemoryCreatedEvent 都自动生效。
    from src.relationship.relationship_candidate_bridge import (
        relationship_candidate_handler,
    )
    subscribe_event(EventType.MEMORY_CREATED, relationship_candidate_handler)

    # R-1.1: EmotionChangedEvent -> 下游观察接口（空消费者，不实现业务）
    subscribe_event(EventType.EMOTION_CHANGED, emotion_changed_observer_handler)


# ============================================================
# R-1.1: 幂等注册包装
# ============================================================
_registered_once = False
_register_lock = threading.Lock()


def register_builtin_handlers_once():
    """R-1.1: 幂等注册（生产启动点调用；多次调用不会重复 subscribe）。

    内部复用 register_builtin_handlers（行为不变）；EventBus.subscribe 本身
    也按函数对象去重，双层防护。
    """
    global _registered_once
    with _register_lock:
        if _registered_once:
            return
        register_builtin_handlers()
        _registered_once = True


# ============================================================
# R-1.1: EmotionChanged 空消费者接口
# ============================================================
def emotion_changed_observer_handler(event: YuyiEvent) -> None:
    """R-1.1: EMOTION_CHANGED 观察接口（占位消费者）。

    约束（本阶段）：
      - 只接收事件并安全返回；
      - 不修改 EmotionState、不创建 Proposal、不写 Memory、
        不触发 Growth、不触发 Relationship；
      - 后续阶段在此接入真实下游观察逻辑。
    """
    _ = event
    return None


def audit_event_handler(event: YuyiEvent):
    try:
        from src.audit.record import record_audit_log
        record_audit_log(
            operation_type=event.event_type,
            source=event.source,
            detail=event.data,
            user_id=event.data.get("user_id", ""),
        )
    except ImportError:
        pass
    except Exception as e:
        print(f"[EventBus] 审计处理器执行失败: {e}")


def create_proposal_from_event(event: YuyiEvent):
    try:
        from src.growth.proposal.reviewer import create_proposal_from_event as _create_proposal
        return _create_proposal(event)
    except ImportError:
        pass
    except Exception as e:
        print(f"[EventBus] 提案处理器执行失败: {e}")


def experience_bridge_memory_created_handler(event: YuyiEvent):
    """Phase 4.0 R2.5.1: MemoryCreatedEvent -> ExperienceBridge 分诊台。

    设计原则：
      - 异常完全隔离（try/except + pass），绝不影响 Runtime 其他 handler、
        绝不阻止 Memory 写入、绝不 crash。
      - 需要 record 完整 dict 时，从 MemoryProvider.get_store().get_by_id() 懒加载。
        ExperienceBridge 本身不 new 任何 Memory 对象。
      - Bridge.process() 内部已经先 emit AuditEvent 再执行下游，这里只做事件转发。
    """
    try:
        data = event.data or {}
        memory_id = data.get("memory_id") or data.get("id")
        if not memory_id:
            return

        from src.memory.memory_provider import MemoryProvider

        # V1.0-1B: bridge-first 获取 MemoryStore authority（与 InteractionRecorder
        # 一致）；Provider 仅作 fallback，保证 memory_store_path 自定义时
        # 不会出现第二个 authority。
        store = None
        try:
            from src.runtime.runtime_bridge import get_runtime_bridge
            store = get_runtime_bridge().get_memory_store()
        except Exception:
            store = None
        if store is None:
            store = MemoryProvider.get_store()
        bridge = _get_shared_experience_bridge(store.get_by_id)
        bridge.process(str(memory_id), user_id=data.get("user_id"))
    except ImportError as ie:
        # Experience 模块缺失时静默（允许未来按需加载）
        _ = ie
    except Exception as e:  # noqa: BLE001
        # R2.5.1 红线：Bridge 挂了 Runtime 照样跑。
        print(f"[EventBus] ExperienceBridge 处理器执行失败(已隔离): {e}")