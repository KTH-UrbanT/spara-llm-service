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
    # ...and must not crash or be ranked by the min(). Asserted on the mechanism rather than
    # on the stage name: since v24 C5 every scored axis here is 9, and the tie resolves to
    # the safest stage, which is `summarizer` via calibration — a legitimate answer.
    from src.evaluation.closed_loop.in_loop_evaluator import _applicable, _worst_axis
    assert "faithfulness" not in _applicable(v.axes)
    assert _worst_axis(_applicable(v.axes)) != "faithfulness"

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

# --- v21 §2.1: k=3 self-consistency vote on the early checkpoint ----------------------

_IMPL = json.dumps({"verdict": "implausible",
                    "axes": {"intent_consistency": 3, "precondition_satisfied": 2},
                    "corrective_hint": "Route to generic instead of building."})
_PLAUS = json.dumps({"verdict": "plausible",
                     "axes": {"intent_consistency": 9, "precondition_satisfied": 9},
                     "corrective_hint": None})
_SHORT_HINT = json.dumps({"verdict": "implausible",
                          "axes": {"intent_consistency": 3, "precondition_satisfied": 2},
                          "corrective_hint": "short"})


def _ev_seq(*responses):
    """An evaluator whose successive `_call`s return `responses` in order."""
    from src.evaluation.closed_loop.in_loop_evaluator import InLoopEvaluator
    c = MagicMock()
    c.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=MagicMock(content=s))],
                  usage=MagicMock(total_tokens=10, completion_tokens=4)) for s in responses]
    with patch("src.evaluation.closed_loop.in_loop_evaluator.AzureOpenAI", return_value=c):
        return InLoopEvaluator()


def test_vote_fires_on_a_majority():
    r = _ev_seq(_IMPL, _PLAUS, _IMPL)
    v = r.route_plausible_voted("q?", "building", False, None, True, k=3)
    assert v.verdict == "implausible"
    assert v.corrective_hint == "Route to generic instead of building."


def test_vote_does_not_fire_on_a_minority():
    """The whole point: one stochastic 'implausible' in three destroyed a case (§1.4)."""
    r = _ev_seq(_IMPL, _PLAUS, _PLAUS)
    assert r.route_plausible_voted("q?", "building", False, None, True, k=3).verdict == "plausible"


def test_vote_counts_post_guard_verdicts_only():
    """A vote downgraded to 'ambiguous' by the short-hint guard is not an implausible vote,
    so two such votes plus one real one must not reach a majority."""
    r = _ev_seq(_IMPL, _SHORT_HINT, _SHORT_HINT)
    assert r.route_plausible_voted("q?", "building", False, None, True, k=3).verdict != "implausible"


def test_vote_sums_usage_across_calls():
    r = _ev_seq(_PLAUS, _PLAUS, _PLAUS)
    r.route_plausible_voted("q?", "building", False, None, True, k=3)
    assert r.last_usage["total_tokens"] == 30 and r.last_usage["completion_tokens"] == 12


def test_vote_k1_is_the_single_call_path():
    """k=1 must be byte-identical to v20, so A_full's k=3 is the only behaviour change."""
    for payload in (_IMPL, _PLAUS):
        voted = _ev_seq(payload).route_plausible_voted("q?", "building", False, None, True, k=1)
        single = _ev(payload).route_plausible("q?", "building", False, None, True)
        assert voted.verdict == single.verdict and voted.axes == single.axes
        assert voted.corrective_hint == single.corrective_hint


def test_vote_makes_exactly_k_calls():
    r = _ev_seq(_PLAUS, _PLAUS, _PLAUS)
    r.route_plausible_voted("q?", "building", False, None, True, k=3)
    assert r._client.chat.completions.create.call_count == 3


# --- v21 §2.3: the diagnostic attribution column ---------------------------------------

def test_diagnostic_names_the_retrieval_outage():
    """`stage_attribution_rule` blames the summariser for every evidence-absent failure
    because faithfulness and calibration share a stage. The diagnostic separates them."""
    r = _ev(json.dumps({"verdict":"fail","axes":{"faithfulness":0,"answer_relevance":9,"question_coverage":9,"calibration":3},"composite":5.25,"stage_attribution":"summarizer","corrective_hint":"Hedge."}))
    v = r.answer_quality("q?", {}, "overclaiming advice")
    assert v.stage_attribution_rule == "summarizer"      # unchanged — nothing branches on the new column
    assert v.attribution_diagnostic == "retrieval_starved"


def test_diagnostic_keeps_the_stage_name_when_evidence_was_present():
    r = _ev(json.dumps({"verdict":"fail","axes":{"faithfulness":8,"answer_relevance":9,"question_coverage":9,"calibration":3},"composite":7.25,"stage_attribution":"summarizer","corrective_hint":"Hedge."}))
    v = r.answer_quality("q?", _EV, "overclaiming")
    assert v.attribution_diagnostic == "summarizer"


def test_diagnostic_does_not_relabel_a_coverage_failure():
    """Only calibration / answer_relevance mean 'nothing to write from'. A coverage failure
    is still a specialists problem even with no evidence — that is the point of the axis."""
    r = _ev(json.dumps({"verdict":"fail","axes":{"faithfulness":0,"answer_relevance":9,"question_coverage":2,"calibration":9},"composite":6.67,"stage_attribution":"specialists","corrective_hint":"Missing."}))
    v = r.answer_quality("q?", {}, "a")
    assert v.attribution_diagnostic == "specialists"


def test_faithfulness_still_scored_when_evidence_present():
    """The axis must keep its teeth on the cases it can actually be computed for."""
    r = _ev(json.dumps({"verdict":"pass","axes":{"faithfulness":2,"answer_relevance":9,"question_coverage":9,"calibration":9},"composite":7.25,"stage_attribution":"summarizer","corrective_hint":"Wrong number."}))
    v = r.answer_quality("q?", _EV, "wrong number")
    assert v.evidence_present is True and v.axes["faithfulness"] == 2
    assert v.verdict == "fail" and v.stage_attribution_rule == "summarizer"


# --- v24: entity_consistency replaces question_coverage --------------------------------

# The system's own identity resolution. The trailing key is deliberate: it is NOT one of
# the four forwarded fields, and the projection must drop it.
_IDENT = {"matched_building_id": "B1", "matched_address": "Tulegatan 5A",
          "candidate_building_ids": ["B1", "B2"], "multiple_records_same_address": True,
          "expected_building_id": "MUST-NOT-LEAK"}


def _v2(**axes):
    """A v2-rubric judge reply with the given axes."""
    full = {"faithfulness": 9, "answer_relevance": 9, "entity_consistency": 9, "calibration": 9}
    full.update(axes)
    return json.dumps({"verdict": "pass", "axes": full, "composite": 9.0,
                       "stage_attribution": "summarizer", "corrective_hint": None})


def _payload(r):
    return json.loads(r._client.chat.completions.create.call_args.kwargs["messages"][1]["content"])


def test_identified_building_reaches_the_judge():
    """C2: without it the judge cannot tell which of several same-address records is the user's."""
    r = _ev(_v2())
    r.answer_quality("q?", _EV, "a", identified_building=_IDENT)
    assert _payload(r)["identified_building"]["matched_building_id"] == "B1"


def test_identified_building_is_projected_to_the_four_declared_fields():
    """The judge must never see anything but the four fields v24 §C2 declares."""
    r = _ev(_v2())
    r.answer_quality("q?", _EV, "a", identified_building=_IDENT)
    assert set(_payload(r)["identified_building"]) == {
        "matched_building_id", "matched_address", "candidate_building_ids",
        "multiple_records_same_address"}


def test_entity_consistency_scored_when_a_building_was_identified():
    """The point of the axis: a wrong-record citation fails, attributed to the summariser."""
    r = _ev(_v2(entity_consistency=2))
    v = r.answer_quality("q?", _EV, "quotes the other building", identified_building=_IDENT)
    assert v.axes["entity_consistency"] == 2
    assert v.verdict == "fail" and v.stage_attribution_rule == "summarizer"


def test_entity_consistency_not_applicable_without_an_identified_building():
    """No resolved building = nothing to be consistent with. A 0 here would fail every
    general-advice answer, which is the v20 false-negative epidemic (Fix 4's lesson)."""
    r = _ev(_v2(entity_consistency=0))
    v = r.answer_quality("q?", _EV, "General advice.")
    assert v.axes["entity_consistency"] is None
    assert v.verdict == "pass" and v.composite == pytest.approx(9.0)
    assert "_fail_open" not in v.axes


def test_entity_consistency_not_applicable_without_evidence():
    """Empty evidence drops faithfulness AND entity_consistency; the rest still score."""
    r = _ev(_v2(faithfulness=0, entity_consistency=0))
    v = r.answer_quality("q?", {}, "advice", identified_building=_IDENT)
    assert v.axes["faithfulness"] is None and v.axes["entity_consistency"] is None
    assert v.composite == pytest.approx(9.0) and v.verdict == "pass"


def test_judge_returned_null_entity_consistency_is_not_a_fail_open():
    """int(None) inside the parser would be caught by the fail-open and become a pass."""
    r = _ev(_v2(entity_consistency=None))
    v = r.answer_quality("q?", _EV, "a", identified_building=_IDENT)
    assert "_fail_open" not in v.axes and v.axes["entity_consistency"] is None
    assert v.composite == pytest.approx(9.0)


def test_an_omitted_entity_consistency_is_still_read_as_zero():
    """Not-applicable is the judge saying null. Silently dropping the axis is not the same
    thing, and would let a judge skip the one axis v24 exists for."""
    r = _ev(json.dumps({"verdict": "pass",
                        "axes": {"faithfulness": 9, "answer_relevance": 9, "calibration": 9},
                        "composite": 9.0, "stage_attribution": "summarizer",
                        "corrective_hint": None}))
    v = r.answer_quality("q?", _EV, "a", identified_building=_IDENT)
    assert v.verdict == "fail" and v.stage_attribution_rule == "summarizer"


def test_v1_axes_still_score_under_the_v1_rubric():
    """The replay gate rests on this: `replay_rescore` runs `score_axes` over the frozen
    v20/v21 traces, which carry `question_coverage`. Reading the absent `entity_consistency`
    as 0 there would report drift that never happened."""
    from src.evaluation.closed_loop.in_loop_evaluator import score_axes
    axes, composite, verdict = score_axes(
        {"faithfulness": 8, "answer_relevance": 8, "question_coverage": 10, "calibration": 8},
        evidence_present=True)
    assert "entity_consistency" not in axes
    assert composite == pytest.approx(8.5) and verdict == "pass"


def test_a_tie_for_the_lowest_axis_does_not_rewind_the_router():
    """C5, from the v24 smoke: answer_relevance and entity_consistency both 2 attributed to
    `router`, the rewind re-decided the route, and a passing case failed on route_match with
    a BETTER answer. A tie must go to the recoverable stage."""
    r = _ev(_v2(answer_relevance=2, entity_consistency=2))
    v = r.answer_quality("q?", _EV, "cites the other building", identified_building=_IDENT)
    assert v.verdict == "fail" and v.stage_attribution_rule == "summarizer"


def test_a_strictly_lower_answer_relevance_still_reaches_the_router():
    """The tie-break must not disarm genuine routing failures."""
    r = _ev(_v2(answer_relevance=1, entity_consistency=3))
    assert r.answer_quality("q?", _EV, "off topic",
                            identified_building=_IDENT).stage_attribution_rule == "router"


def test_arms_are_wired_to_a_rubric_that_has_the_new_axis():
    """C4. A typo in the filename is a FileNotFoundError 88 times per arm at run time; a
    stale filename is a silently degenerate axis for three replicates."""
    from pathlib import Path
    from scripts.run_arm_closed_loop import _ARM_ENV
    import src.evaluation.closed_loop.in_loop_evaluator as m
    for arm, env in _ARM_ENV.items():
        p = Path(m.__file__).parent / "prompts" / env["ANSWER_QUALITY_PROMPT_VERSION"]
        assert p.exists(), f"{arm} points at a missing rubric: {p.name}"
        assert "entity_consistency" in p.read_text(), f"{arm} still runs the retired axis"


def test_v1_question_coverage_keeps_its_stage():
    """Frozen traces must attribute the way they did when they were written."""
    r = _ev(json.dumps({"verdict":"fail","axes":{"faithfulness":8,"answer_relevance":7,"question_coverage":2,"calibration":8},"composite":6.25,"stage_attribution":"specialists","corrective_hint":"Missing."}))
    assert r.answer_quality("q?", _EV, "a").stage_attribution_rule == "specialists"
