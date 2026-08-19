"""Proposal event helper factories

These create BaseEvent instances for proposal lifecycle events.
"""
from __future__ import annotations
from typing import Dict, Any
from src.contracts import event_schema, growth_schema


def proposal_created_event(proposal: growth_schema.GrowthProposal) -> event_schema.BaseEvent:
    return event_schema.BaseEvent(type="ProposalCreatedEvent", source="proposal_manager", payload=proposal.to_dict())


def proposal_accepted_event(proposal: growth_schema.GrowthProposal) -> event_schema.BaseEvent:
    return event_schema.BaseEvent(type="ProposalAcceptedEvent", source="proposal_manager", payload=proposal.to_dict())


def proposal_rejected_event(proposal: growth_schema.GrowthProposal) -> event_schema.BaseEvent:
    return event_schema.BaseEvent(type="ProposalRejectedEvent", source="proposal_manager", payload=proposal.to_dict())
