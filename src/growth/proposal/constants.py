PROPOSAL_STATUS = {
    "PENDING": "pending",
    "APPROVED": "approved",
    "REJECTED": "rejected",
    "APPLIED": "applied",
    "CANCELLED": "cancelled",
}

PROPOSAL_TYPE = {
    "PERSONALITY": "personality",
    "RELATIONSHIP": "relationship",
    "IDENTITY": "identity",
    "SELF_MODEL": "self_model",
}

AUTO_APPROVE_THRESHOLD = {
    "personality": 0.05,
    "trust": 0.10,
    "familiarity": 0.10,
    "bond_strength": 0.08,
}

PRIORITY_LEVEL = {
    "LOW": "low",
    "MEDIUM": "medium",
    "HIGH": "high",
}

PROPOSAL_EXPIRY_HOURS = 72

MAX_PROPOSALS = 500