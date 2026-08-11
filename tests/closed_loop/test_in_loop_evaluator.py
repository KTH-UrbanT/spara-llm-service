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
    assert r.answer_quality("q?", {}, "answer").verdict == "pass"

def test_answer_fail():
    r = _ev(json.dumps({"verdict":"fail","axes":{"faithfulness":3,"answer_relevance":8,"question_coverage":7,"calibration":8},"composite":6.5,"stage_attribution":"summarizer","corrective_hint":"Fix numbers."}))
    v = r.answer_quality("q?", {}, "wrong")
    assert v.verdict == "fail" and v.stage_attribution_rule == "summarizer"

def test_attribution_router():
    r = _ev(json.dumps({"verdict":"fail","axes":{"faithfulness":8,"answer_relevance":2,"question_coverage":7,"calibration":8},"composite":6.25,"stage_attribution":"router","corrective_hint":"Off topic."}))
    assert r.answer_quality("q?", {}, "a").stage_attribution_rule == "router"

def test_attribution_specialists():
    r = _ev(json.dumps({"verdict":"fail","axes":{"faithfulness":8,"answer_relevance":7,"question_coverage":2,"calibration":8},"composite":6.25,"stage_attribution":"specialists","corrective_hint":"Missing."}))
    assert r.answer_quality("q?", {}, "a").stage_attribution_rule == "specialists"

def test_axis_floor_forces_fail():
    """composite 7.5 >= 7.0 but one axis (3) below the floor 4.0 -> fail."""
    r = _ev(json.dumps({"verdict":"pass","axes":{"faithfulness":9,"answer_relevance":9,"question_coverage":9,"calibration":3},"composite":7.5,"stage_attribution":"summarizer","corrective_hint":None}))
    assert r.answer_quality("q?", {}, "overclaim").verdict == "fail"

def test_attribution_unknown_when_no_axis_floored():
    """fail on composite with all axes >= floor -> attribution 'unknown'."""
    r = _ev(json.dumps({"verdict":"fail","axes":{"faithfulness":6,"answer_relevance":6,"question_coverage":6,"calibration":6},"composite":6.0,"stage_attribution":"unknown","corrective_hint":None}))
    v = r.answer_quality("q?", {}, "mediocre")
    assert v.verdict == "fail" and v.stage_attribution_rule == "unknown"

def test_attribution_missing_axis_not_masked_as_unknown():
    """An omitted axis is treated as 0 -> fail attributed to that axis's stage, not 'unknown'."""
    r = _ev(json.dumps({"verdict":"fail","axes":{"faithfulness":8,"answer_relevance":8,"question_coverage":8},"composite":6.0,"stage_attribution":"summarizer","corrective_hint":"x"}))
    v = r.answer_quality("q?", {}, "a")
    assert v.verdict == "fail" and v.stage_attribution_rule == "summarizer"  # calibration missing -> summarizer

def test_route_empty_axes_downgrades_to_ambiguous():
    """implausible with an empty axes dict must NOT fire (no evidence) -> ambiguous."""
    r = _ev(json.dumps({"verdict":"implausible","axes":{},"corrective_hint":"Reconsider the route entirely now."}))
    assert r.route_plausible("q?", "building", False, None).verdict == "ambiguous"
