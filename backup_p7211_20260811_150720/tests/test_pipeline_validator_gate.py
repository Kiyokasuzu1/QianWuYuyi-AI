# tests/test_pipeline_validator_gate.py
import copy

def make_event(event_id="evt1", validator_apply=True, is_first=True, source_ids=None):
    return {
        "event_id": event_id,
        "topic": "测试",
        "canonical_topic": "测试",
        "evidence": [{"text":"x","role":"user","source_index":0}],
        "metadata": {"validator_decision": "keep", "validator_apply": validator_apply},
        "is_first_occurrence": is_first,
        "source_ids": source_ids or ["mem_1"]
    }


def test_keep_event_calls_apply(monkeypatch):
    from src.growth.pipeline import GrowthPipeline

    gp = GrowthPipeline()

    # stub extractor/normalizer/validator/matcher to return our event
    ev = make_event(event_id="keep_evt", validator_apply=True)
    gp.extractor.extract = lambda limit: [ev]
    gp.normalizer.normalize = lambda events: events
    gp.validator.validate = lambda events: events
    gp.matcher.track = lambda events, force_first_run: events

    called = {"applied": False}
    def fake_apply(event):
        called["applied"] = True
        return {"status":"applied", "delta":{}}
    gp.growth_engine.apply = fake_apply

    gp.run_full_consolidation()
    assert called["applied"] is True


def test_review_event_skips_apply_and_no_state_change(monkeypatch, tmp_path):
    from src.growth.pipeline import GrowthPipeline

    gp = GrowthPipeline()

    ev = make_event(event_id="review_evt", validator_apply=False)
    gp.extractor.extract = lambda limit: [ev]
    gp.normalizer.normalize = lambda events: events
    gp.validator.validate = lambda events: events
    gp.matcher.track = lambda events, force_first_run: events

    # replace apply with a function that would raise if called
    def bad_apply(event):
        raise RuntimeError("apply should not be called for review events")
    gp.growth_engine.apply = bad_apply

    # snapshot state before
    before_state = copy.deepcopy(gp.growth_engine.state.get())

    res = gp.run_full_consolidation()

    # state should be unchanged
    after_state = gp.growth_engine.state.get()
    assert before_state == after_state

    # ensure event still returned in pipeline output
    assert any(e.get("event_id") == "review_evt" for e in res["events"])


def test_missing_validator_apply_defaults_true(monkeypatch):
    from src.growth.pipeline import GrowthPipeline

    gp = GrowthPipeline()

    ev = make_event(event_id="no_flag_evt", validator_apply=None)
    # remove validator_apply key to simulate old events
    ev["metadata"].pop("validator_apply", None)

    gp.extractor.extract = lambda limit: [ev]
    gp.normalizer.normalize = lambda events: events
    gp.validator.validate = lambda events: events
    gp.matcher.track = lambda events, force_first_run: events

    called = {"applied": False}
    def fake_apply(event):
        called["applied"] = True
        return {"status":"applied", "delta":{}}
    gp.growth_engine.apply = fake_apply

    gp.run_full_consolidation()
    assert called["applied"] is True
