import inspect, json, os, pytest
from unittest.mock import MagicMock, patch

@pytest.fixture(autouse=True)
def env_vars():
    with patch.dict(os.environ, {
        "OPENAI_API_KEY": "test", "AZURE_ENDPOINT": "https://x.openai.azure.com/",
        "OPENAI_API_VERSION": "2023-05-15",
        "OPENAI_RESPONSE_MODEL_DEPLOYMENT_NAME": "gpt-4o",
    }): yield

def _client(s):
    c = MagicMock()
    c.chat.completions.create.return_value.choices = [MagicMock(message=MagicMock(content=s))]
    return c

def _ev(s):
    from src.evaluation.closed_loop.in_loop_evaluator import InLoopEvaluator
    with patch("src.evaluation.closed_loop.in_loop_evaluator.AzureOpenAI", return_value=_client(s)):
        return InLoopEvaluator()

# Non-empty evidence. The axis rules below are about how a SCORED faithfulness behaves;
# with empty evidence the axis is not applicable and these tests would be measuring the
# not-applicable path instead (see the empty-evidence tests at the bottom).
_EV = {"generic_sql": [{"byggnadsid": "B1", "energy_performance": 100}]}

def test_no_gold_in_public_methods():
    """Structural gold-leak guard: no public method accepts a gold-bearing parameter."""
    from src.evaluation.closed_loop.in_loop_evaluator import InLoopEvaluator
    for name, m in inspect.getmembers(InLoopEvaluator, predicate=inspect.isfunction):
        if name.startswith("_"): continue
        for p in inspect.signature(m).parameters:
            if p == "self": continue
            for prefix in ("expected_", "gold", "case"):
                assert not p.startswith(prefix), f"InLoopEvaluator.{name} has gold param {p!r}"

def test_route_plausible():
    r = _ev(json.dumps({"verdict":"plausible","axes":{"intent_consistency":8,"precondition_satisfied":9},"corrective_hint":None}))
    v = r.route_plausible("q?", "building", True, None)
    assert v.verdict == "plausible" and v.axes["intent_consistency"] == 8

def test_route_implausible():
    r = _ev(json.dumps({"verdict":"implausible","axes":{"intent_consistency":3,"precondition_satisfied":2},"corrective_hint":"Route to generic instead of building."}))
    assert r.route_plausible("q?", "building", False, None).verdict == "implausible"

def test_route_short_hint_downgrades():
    """A hint shorter than the minimum length cannot trigger 'implausible'."""
    r = _ev(json.dumps({"verdict":"implausible","axes":{"intent_consistency":3,"precondition_satisfied":2},"corrective_hint":"short"}))
    assert r.route_plausible("q?", "building", False, None).verdict == "ambiguous"

def test_route_fail_open():
    r = _ev("not json {{")
    v = r.route_plausible("q?", "building", True, None)
    assert v.verdict == "plausible" and v.axes.get("_fail_open")

def test_answer_pass():
    r = _ev(json.dumps({"verdict":"pass","axes":{"faithfulness":9,"answer_relevance":8,"question_coverage":8,"calibration":9},"composite":8.5,"stage_attribution":"summarizer","corrective_hint":None}))
    v = r.answer_quality("q?", _EV, "answer")
    assert v.verdict == "pass" and v.evidence_present is True

def test_answer_fail():
    r = _ev(json.dumps({"verdict":"fail","axes":{"faithfulness":3,"answer_relevance":8,"question_coverage":7,"calibration":8},"composite":6.5,"stage_attribution":"summarizer","corrective_hint":"Fix numbers."}))
    v = r.answer_quality("q?", _EV, "wrong")
    assert v.verdict == "fail" and v.stage_attribution_rule == "summarizer"

def test_attribution_router():
    r = _ev(json.dumps({"verdict":"fail","axes":{"faithfulness":8,"answer_relevance":2,"question_coverage":7,"calibration":8},"composite":6.25,"stage_attribution":"router","corrective_hint":"Off topic."}))
    assert r.answer_quality("q?", _EV, "a").stage_attribution_rule == "router"

def test_attribution_specialists():
    r = _ev(json.dumps({"verdict":"fail","axes":{"faithfulness":8,"answer_relevance":7,"question_coverage":2,"calibration":8},"composite":6.25,"stage_attribution":"specialists","corrective_hint":"Missing."}))
    assert r.answer_quality("q?", _EV, "a").stage_attribution_rule == "specialists"

def test_axis_floor_forces_fail():
    """composite 7.5 >= 7.0 but one axis (3) below the floor 4.0 -> fail."""
    r = _ev(json.dumps({"verdict":"pass","axes":{"faithfulness":9,"answer_relevance":9,"question_coverage":9,"calibration":3},"composite":7.5,"stage_attribution":"summarizer","corrective_hint":None}))
    assert r.answer_quality("q?", _EV, "overclaim").verdict == "fail"

def test_attribution_unknown_when_no_axis_floored():
    """fail on composite with all axes >= floor -> attribution 'unknown'."""
    r = _ev(json.dumps({"verdict":"fail","axes":{"faithfulness":6,"answer_relevance":6,"question_coverage":6,"calibration":6},"composite":6.0,"stage_attribution":"unknown","corrective_hint":None}))
    v = r.answer_quality("q?", _EV, "mediocre")
    assert v.verdict == "fail" and v.stage_attribution_rule == "unknown"

def test_attribution_missing_axis_not_masked_as_unknown():
    """An omitted axis is treated as 0 -> fail attributed to that axis's stage, not 'unknown'."""
    r = _ev(json.dumps({"verdict":"fail","axes":{"faithfulness":8,"answer_relevance":8,"question_coverage":8},"composite":6.0,"stage_attribution":"summarizer","corrective_hint":"x"}))
    v = r.answer_quality("q?", _EV, "a")
    assert v.verdict == "fail" and v.stage_attribution_rule == "summarizer"  # calibration missing -> summarizer

def test_route_empty_axes_downgrades_to_ambiguous():
    """implausible with an empty axes dict must NOT fire (no evidence) -> ambiguous."""
    r = _ev(json.dumps({"verdict":"implausible","axes":{},"corrective_hint":"Reconsider the route entirely now."}))
    assert r.route_plausible("q?", "building", False, None).verdict == "ambiguous"


# --- Fix 5: the pending-hop input reaches the judge ------------------------------------

def test_route_passes_about_to_request_address_to_the_judge():
    """The flag must reach the prompt payload, else the judge cannot see the failure mode."""
    r = _ev(json.dumps({"verdict":"plausible","axes":{"intent_consistency":8,"precondition_satisfied":8},"corrective_hint":None}))
    r.route_plausible("q?", "building", False, None, about_to_request_address=True)
    payload = json.loads(r._client.chat.completions.create.call_args.kwargs["messages"][1]["content"])
    assert payload["about_to_request_address"] is True

def test_route_about_to_request_address_defaults_false():
    """Production callers omit it; the judge must still see the key."""
    r = _ev(json.dumps({"verdict":"plausible","axes":{"intent_consistency":8,"precondition_satisfied":8},"corrective_hint":None}))
    r.route_plausible("q?", "building", True, None)
    payload = json.loads(r._client.chat.completions.create.call_args.kwargs["messages"][1]["content"])
    assert payload["about_to_request_address"] is False


# --- Fix 4: faithfulness is not applicable when there is no evidence -------------------

def test_empty_evidence_marks_faithfulness_not_applicable():
    """No evidence -> faithfulness is null, excluded from the composite, and NOT a fail."""
    r = _ev(json.dumps({"verdict":"pass","axes":{"faithfulness":0,"answer_relevance":9,"question_coverage":9,"calibration":9},"composite":6.75,"stage_attribution":"summarizer","corrective_hint":None}))
    v = r.answer_quality("q?", {}, "Good general advice.")
    assert v.axes["faithfulness"] is None
    assert v.evidence_present is False
    assert v.composite == pytest.approx(9.0)      # mean of the three scored axes
    assert v.verdict == "pass"
    assert "_fail_open" not in v.axes             # the None must not trip the fail-open
    assert v.stage_attribution_rule != "summarizer"  # and must not crash the min()

def test_judge_returned_null_faithfulness_is_not_a_fail_open():
    """int(None) inside the parser would be caught by the fail-open and become a pass."""
    r = _ev(json.dumps({"verdict":"pass","axes":{"faithfulness":None,"answer_relevance":8,"question_coverage":8,"calibration":8},"composite":8.0,"stage_attribution":"unknown","corrective_hint":None}))
    v = r.answer_quality("q?", {}, "advice")
    assert "_fail_open" not in v.axes and v.axes["faithfulness"] is None
    assert v.composite == pytest.approx(8.0)

def test_empty_evidence_still_fails_on_another_axis():
    """Dropping faithfulness must not become a free pass: the floor still applies."""
    r = _ev(json.dumps({"verdict":"pass","axes":{"faithfulness":0,"answer_relevance":9,"question_coverage":9,"calibration":3},"composite":5.25,"stage_attribution":"summarizer","corrective_hint":"Hedge."}))
    v = r.answer_quality("q?", {}, "overclaiming advice")
    assert v.verdict == "fail" and v.stage_attribution_rule == "summarizer"  # calibration -> summarizer

def test_evidence_present_ignores_empty_containers():
    """{'generic_sql': []} is truthy but holds nothing to be faithful to."""
    r = _ev(json.dumps({"verdict":"pass","axes":{"faithfulness":0,"answer_relevance":9,"question_coverage":9,"calibration":9},"composite":6.75,"stage_attribution":"summarizer","corrective_hint":None}))
    v = r.answer_quality("q?", {"generic_sql": []}, "advice")
    assert v.evidence_present is False and v.axes["faithfulness"] is None

def test_faithfulness_still_scored_when_evidence_present():
    """The axis must keep its teeth on the cases it can actually be computed for."""
    r = _ev(json.dumps({"verdict":"pass","axes":{"faithfulness":2,"answer_relevance":9,"question_coverage":9,"calibration":9},"composite":7.25,"stage_attribution":"summarizer","corrective_hint":"Wrong number."}))
    v = r.answer_quality("q?", _EV, "wrong number")
    assert v.evidence_present is True and v.axes["faithfulness"] == 2
    assert v.verdict == "fail" and v.stage_attribution_rule == "summarizer"
