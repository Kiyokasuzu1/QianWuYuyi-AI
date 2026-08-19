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
        prop = self.manager.create_proposal(source_event=src_event, proposed_changes=[ci], confidence=0.75, evidence_ids=["mem_1"], evaluator_meta={})
        # verify returned proposal has id and stored
        self.assertIsNotNone(prop.id)
        loaded = self.store.load(prop.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.id, prop.id)
        self.assertEqual(loaded.source_event_id, "evt_test")

    def test_accept_proposal_idempotent(self):
        # create proposal
        ci = growth_schema.ChangeItem(path="personality.traits.shyness", before=0.1, after=0.2, reason="test")
        prop = self.manager.create_proposal(source_event={"id": "evt2"}, proposed_changes=[ci], confidence=0.9, evidence_ids=[], evaluator_meta={})
        # monkeypatch adapter to simulate successful apply
        apply_called = {}

        def fake_apply(p, actor="system"):
            apply_called['called'] = True
            return {"applied": True, "before": {"shyness": 0.1}, "after": {"shyness": 0.2}, "note": "ok"}

        self.manager.personality_adapter.apply_proposal = fake_apply

        # accept first time
        p1 = self.manager.accept_proposal(prop.id, actor="tester")
        self.assertEqual(p1.status, "accepted")
        self.assertIsNotNone(p1.accepted_at)
        # accept second time (idempotent)
        p2 = self.manager.accept_proposal(prop.id, actor="tester")
        self.assertEqual(p2.status, "accepted")
        self.assertTrue(apply_called.get('called', False))

    def test_reject_proposal_sets_status(self):
        ci = growth_schema.ChangeItem(path="personality.traits.shyness", before=0.1, after=0.2, reason="test")
        prop = self.manager.create_proposal(source_event={"id": "evt3"}, proposed_changes=[ci], confidence=0.5, evidence_ids=[], evaluator_meta={})
        p = self.manager.reject_proposal(prop.id, actor="tester", reason="not enough confidence")
        self.assertEqual(p.status, "rejected")
        self.assertIsNotNone(p.rejected_at)


if __name__ == "__main__":
    unittest.main()
