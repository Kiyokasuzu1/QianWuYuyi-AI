from src.events.events import YuyiEvent, EventType
from src.events.bus import subscribe_event


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

        from src.experience.experience_bridge import ExperienceBridge
        from src.memory.memory_provider import MemoryProvider

        store = MemoryProvider.get_store()
        bridge = ExperienceBridge(record_lookup=store.get_by_id)
        bridge.process(str(memory_id), user_id=data.get("user_id"))
    except ImportError as ie:
        # Experience 模块缺失时静默（允许未来按需加载）
        _ = ie
    except Exception as e:  # noqa: BLE001
        # R2.5.1 红线：Bridge 挂了 Runtime 照样跑。
        print(f"[EventBus] ExperienceBridge 处理器执行失败(已隔离): {e}")