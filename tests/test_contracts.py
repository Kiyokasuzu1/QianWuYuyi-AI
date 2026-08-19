# Basic unit tests for contracts (unittest-based, no extra deps)
import unittest
from src.contracts import event_schema, audit_schema, state_schema, growth_schema

class TestContracts(unittest.TestCase):
    def test_event_base_roundtrip(self):
        e = event_schema.UserMessageEvent(source="test", payload={"text": "hello"}, related_ids=["m1"])
        d = e.to_dict()
        e2 = event_schema.BaseEvent.from_dict(d)
        self.assertEqual(d["payload"]["text"], e2.payload["text"])
        self.assertTrue(e2.id.startswith("evt_"))

    def test_audit_entry_roundtrip(self):
        a = audit_schema.AuditEntry(component="personality", actor="tester", reason="unit test",
                                    source_event_id="evt_123", evidence_memory_ids=["mem_1"],
                                    before={"traits":"old"}, after={"traits":"new"})
        d = a.to_dict()
        a2 = audit_schema.AuditEntry.from_dict(d)
        self.assertEqual(a.component, a2.component)
        self.assertEqual(a.source_event_id, a2.source_event_id)
        self.assertEqual(a.before, a2.before)

    def test_snapshot_roundtrip(self):
        pd = state_schema.ComponentDigest(name="personality", digest="abc123", metadata={"ver":1})
        smd = state_schema.ComponentDigest(name="self_model", digest="sm-1", metadata={"ver":1})
        snap = state_schema.Snapshot(personality_digest=pd, runtime_status={"mode":"hybrid"}, notes="test snap", self_model_digest=smd)
        d = snap.to_dict()
        snap2 = state_schema.Snapshot.from_dict(d)
        self.assertEqual(snap2.runtime_status.get("mode"), "hybrid")
        self.assertIsNotNone(snap2.snapshot_id)
        self.assertIsNotNone(snap2.self_model_digest)
        self.assertEqual(snap2.self_model_digest.name, "self_model")

    def test_growth_proposal_roundtrip(self):
        ci = growth_schema.ChangeItem(path="personality.traits.shyness", before=0.1, after=0.2, reason="example")
        gp = growth_schema.GrowthProposal(source_event_id="evt_abc", proposed_changes=[ci], confidence=0.75, evidence_ids=["mem_1"])
        # set accepted / rejected timestamps to test fields
        gp.accepted_at = "2026-01-01T00:00:00Z"
        d = gp.to_dict()
        gp2 = growth_schema.GrowthProposal.from_dict(d)
        self.assertEqual(len(gp2.proposed_changes), 1)
        self.assertAlmostEqual(gp2.confidence, 0.75)
        self.assertEqual(gp2.accepted_at, "2026-01-01T00:00:00Z")

if __name__ == "__main__":
    unittest.main()
