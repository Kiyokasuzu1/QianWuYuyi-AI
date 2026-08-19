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