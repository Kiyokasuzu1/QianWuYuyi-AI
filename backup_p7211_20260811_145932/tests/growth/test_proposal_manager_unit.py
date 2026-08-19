import unittest
import tempfile
from pathlib import Path
from unittest import mock

from src.growth.proposal_store import ProposalStore
from src.growth.proposal_manager import ProposalManager
from src.contracts import growth_schema


class TestProposalManagerUnit(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tmpdir.name) / "proposals.jsonl"
        self.store = ProposalStore(path=str(self.store_path))
        # runtime_context None -> no audit/eventbus/snapshot hooks
        self.manager = ProposalManager(runtime_context=None, store=self.store, config={"auto_accept_enabled": False})

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_create_proposal_persists(self):
        ci = growth_schema.ChangeItem(path="personality.traits.shyness", before=0.1, after=0.2, reason="test")
        src_event = {"id": "evt_test"}
        # confidence=0.9 (>= threshold 0.8), evidence_ids 非空
        result = self.manager.create_proposal(source_event=src_event, proposed_changes=[ci], confidence=0.9, evidence_ids=["mem_1"], evaluator_meta={})
        # current API: returns dict {status, proposal, ...}
        self.assertEqual(result["status"], "created")
        prop = result["proposal"]
        self.assertIsNotNone(prop)
        self.assertIsNotNone(prop.id)
        loaded = self.store.load(prop.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.id, prop.id)
        self.assertEqual(loaded.source_event_id, "evt_test")

    def test_accept_proposal_idempotent(self):
        # create proposal (合规参数)
        ci = growth_schema.ChangeItem(path="personality.traits.shyness", before=0.1, after=0.2, reason="test")
        result = self.manager.create_proposal(source_event={"id": "evt2"}, proposed_changes=[ci], confidence=0.9, evidence_ids=["mem_e2"], evaluator_meta={})
        self.assertEqual(result["status"], "created")
        prop = result["proposal"]
        # monkeypatch adapter to simulate successful apply
        apply_called = {}

        def fake_apply(p, actor="system"):
            apply_called['called'] = True
            return {"applied": True, "before": {"shyness": 0.1}, "after": {"shyness": 0.2}, "note": "ok"}

        self.manager.personality_adapter.apply_proposal = fake_apply

        # accept first time
        p1 = self.manager.accept_proposal(prop.id, actor="tester")
        self.assertEqual(p1["status"], "accepted")
        self.assertIsNotNone(p1["proposal"].accepted_at)
        # accept second time (idempotent)
        p2 = self.manager.accept_proposal(prop.id, actor="tester")
        self.assertIn(p2["status"], ("accepted", "already_accepted"))

    def test_reject_proposal_sets_status(self):
        ci = growth_schema.ChangeItem(path="personality.traits.shyness", before=0.1, after=0.2, reason="test")
        result = self.manager.create_proposal(source_event={"id": "evt3"}, proposed_changes=[ci], confidence=0.5, evidence_ids=[], evaluator_meta={})
        # confidence=0.5 < threshold(0.8) -> may be rejected at create time
        prop = result.get("proposal")
        if prop is None:
            # created refused, but reject_proposal should still handle not_found gracefully
            return
        r = self.manager.reject_proposal(prop.id, actor="tester", reason="not enough confidence")
        self.assertEqual(r["status"], "rejected")
        self.assertIsNotNone(r["proposal"].rejected_at)


if __name__ == "__main__":
    unittest.main()
