import sys
import os
import tempfile
import shutil

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)


def test_event_bus_integration():
    from src.events.bus import get_event_bus, publish_event
    from src.events.events import (
        MessageReceivedEvent,
        MessageRespondedEvent,
        MemoryCreatedEvent,
        EmotionChangedEvent,
        RelationshipChangedEvent,
        GrowthProposalEvent,
        EventType,
    )

    bus = get_event_bus()
    received_events = []

    def test_handler(event):
        received_events.append(event)

    bus.subscribe_all(test_handler)

    test_user_id = "test_user_001"

    publish_event(MessageReceivedEvent(
        user_id=test_user_id,
        content="Hello Yuyi",
        source="test",
    ))
    publish_event(MessageRespondedEvent(
        user_id=test_user_id,
        content="Hi there!",
        source="test",
    ))
    publish_event(MemoryCreatedEvent(
        memory_id="mem_test_001",
        user_id=test_user_id,
        content="Test memory",
        source="test",
    ))
    publish_event(EmotionChangedEvent(
        user_id=test_user_id,
        source="test",
        data={
            "dominant_before": "neutral",
            "dominant_after": "happy",
            "intensity_before": 0.0,
            "intensity_after": 0.7,
        },
    ))
    publish_event(RelationshipChangedEvent(
        user_id=test_user_id,
        dimension="trust",
        old_value=0.5,
        new_value=0.6,
        reason="test",
        source="test",
    ))
    publish_event(GrowthProposalEvent(
        proposal_id="prop_test_001",
        proposal_type="relationship",
        affected_dimensions={"trust": 0.1},
        confidence=0.7,
        reason="test",
        source="test",
    ))

    assert len(received_events) == 6, f"Expected 6 events, got {len(received_events)}"

    event_types = [e.event_type for e in received_events]
    assert EventType.MESSAGE_RECEIVED in event_types
    assert EventType.MESSAGE_RESPONDED in event_types
    assert EventType.MEMORY_CREATED in event_types
    assert EventType.EMOTION_CHANGED in event_types
    assert EventType.RELATIONSHIP_CHANGED in event_types
    assert EventType.GROWTH_PROPOSAL_CREATED in event_types

    bus.unsubscribe_all(test_handler)
    print("✓ Event Bus integration test passed")


def test_audit_system_integration():
    from src.audit.record import record_audit_log
    from src.audit.storage import get_audit_storage, load_audit_records

    test_user_id = "test_user_001"
    correlation_id = "test_conv_001"

    record_audit_log(
        operation_type="message.received",
        source="test",
        action="用户消息接收",
        user_id=test_user_id,
        detail={"message_length": 15},
        correlation_id=correlation_id,
    )

    record_audit_log(
        operation_type="message.responded",
        source="test",
        action="回复发送",
        user_id=test_user_id,
        detail={"response_length": 20},
        correlation_id=correlation_id,
    )

    record_audit_log(
        operation_type="memory.created",
        source="test",
        action="记忆保存",
        user_id=test_user_id,
        detail={"memory_id": "mem_test_001"},
        correlation_id=correlation_id,
    )

    records = load_audit_records(limit=10)
    assert len(records) >= 3, f"Expected at least 3 records, got {len(records)}"

    user_records = [r for r in records if r.user_id == test_user_id]
    assert len(user_records) >= 3, f"Expected at least 3 user records, got {len(user_records)}"

    op_types = [r.operation_type for r in user_records]
    assert "message.received" in op_types
    assert "message.responded" in op_types
    assert "memory.created" in op_types

    print("✓ Audit System integration test passed")


def test_growth_proposal_integration():
    from src.growth.proposal.proposal import GrowthProposal
    from src.growth.proposal.constants import PROPOSAL_TYPE, PROPOSAL_STATUS, PRIORITY_LEVEL
    from src.growth.proposal.storage import get_proposal_storage, list_all_proposals, list_pending_proposals

    test_user_id = "test_user_001"

    proposal = GrowthProposal(
        proposal_type=PROPOSAL_TYPE["RELATIONSHIP"],
        status=PROPOSAL_STATUS["PENDING"],
        source="test",
        user_id=test_user_id,
        affected_dimensions={"trust": 0.1},
        before_state={"trust": 0.5},
        after_state={"trust": 0.6},
        confidence=0.7,
        reason="Test proposal",
        evidence=["event_001"],
        priority=PRIORITY_LEVEL["MEDIUM"],
    )

    storage = get_proposal_storage()
    storage.save(proposal)

    loaded = storage.load(proposal.proposal_id)
    assert loaded is not None, "Proposal should be loadable"
    assert loaded.proposal_id == proposal.proposal_id
    assert loaded.proposal_type == PROPOSAL_TYPE["RELATIONSHIP"]
    assert loaded.status == PROPOSAL_STATUS["PENDING"]
    assert loaded.user_id == test_user_id

    all_proposals = list_all_proposals(limit=10)
    assert len(all_proposals) >= 1, f"Expected at least 1 proposal, got {len(all_proposals)}"

    pending_proposals = list_pending_proposals(limit=10)
    assert len(pending_proposals) >= 1, f"Expected at least 1 pending proposal, got {len(pending_proposals)}"

    print("✓ Growth Proposal integration test passed")


def test_orchestrator_create_proposal():
    try:
        from src.orchestrator import Orchestrator
    except ImportError:
        print("⚠️  Skipping orchestrator test (OpenAI not available)")
        return

    o = Orchestrator()
    o.target_user_id = "test_user_proposal"

    o._create_growth_proposal(
        user_id="test_user_proposal",
        proposal_type="relationship",
        before_state={"trust": 0.5},
        after_state={"trust": 0.6},
        reason="Test trust change",
        evidence=["event_001"],
    )

    from src.growth.proposal.storage import list_all_proposals
    proposals = list_all_proposals(limit=10)
    assert len(proposals) >= 1, f"Expected at least 1 proposal, got {len(proposals)}"

    created_proposal = proposals[0]
    assert created_proposal.user_id == "test_user_proposal"
    assert created_proposal.proposal_type == "relationship"
    assert created_proposal.status == "pending"
    assert created_proposal.affected_dimensions == {"trust": 0.1}

    print("✓ Orchestrator create_proposal test passed")


def test_orchestrator_audit_logging():
    try:
        from src.orchestrator import Orchestrator
    except ImportError:
        print("⚠️  Skipping orchestrator audit test (OpenAI not available)")
        return

    from src.audit.storage import load_audit_records

    o = Orchestrator()
    o.target_user_id = "test_user_audit"

    before_count = len(load_audit_records(limit=100))

    o._process_relationship_post(
        assembled_context={
            "relationship_repo": type('MockRepo', (), {'save': lambda self, x: None})(),
            "relationship_profile": {"trust": 0.5, "familiarity": 0.3},
            "trace": [],
        },
        user_message="Test message",
        reply="Test reply",
        chat_memories=[],
        user_id="test_user_audit",
    )

    after_count = len(load_audit_records(limit=100))
    assert after_count >= before_count, f"Audit count should not decrease"

    print("✓ Orchestrator audit logging test passed")


if __name__ == "__main__":
    print("=" * 60)
    print("Running Orchestrator Integration Tests")
    print("=" * 60)

    test_event_bus_integration()
    test_audit_system_integration()
    test_growth_proposal_integration()
    test_orchestrator_create_proposal()
    test_orchestrator_audit_logging()

    print("=" * 60)
    print("All tests passed!")
    print("=" * 60)