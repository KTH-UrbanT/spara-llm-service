"""The prediction check decides whether the pre-registration held, so its verdict logic
needs its own check — a silently-inverted comparison would report PASS on a failed study."""
import json
from pathlib import Path

from scripts.check_predictions import (_fix5, _judged, _n77, _verdicts, verdicts,
                                       FIX5_GUARDS, FIX5_TARGETS, FIX5_TRACKED)


def _case(cid, **kw):
    return {"case_id": cid, "expected_route": kw.pop("route", "generic"), **kw}


def test_n77_drops_only_combined():
    rows = [_case("a"), _case("b", route="combined"), _case("c", route="clarification")]
    assert [r["case_id"] for r in _n77(rows)] == ["a", "c"]


def test_judged_excludes_short_circuits_and_fail_opens():
    att = [{"evaluator_axes_json": {"faithfulness": 8}},
           {"evaluator_axes_json": {"_fail_open": True}},
           {"evaluator_axes_json": {"_short_circuit": True}},
           {"evaluator_axes_json": {}}]
    assert len(_judged(att)) == 1


def test_fix5_counts_only_conversions_not_prior_passes():
    """A case A_open already passed is not a conversion. Counting it as one would let
    prediction 4 pass on cases the loop never touched."""
    open_rows = [_case("EKR_GEN_017", case_pass=False),
                 _case("EKR_GEN_018", case_pass=True)]
    full_rows = [_case("EKR_GEN_017", case_pass=True, total_attempts=2),
                 _case("EKR_GEN_018", case_pass=True, total_attempts=1)]
    r = _fix5({"A_open": open_rows, "A_full": full_rows})
    assert r["fired"] == ["EKR_GEN_017"]
    assert r["converted"] == ["EKR_GEN_017"]


def test_fix5_guards_must_still_request_address():
    full = [_case(c, request_address_fired=True) for c in FIX5_GUARDS]
    assert len(_fix5({"A_open": [], "A_full": full})["guards_held"]) == 2
    full[0]["request_address_fired"] = False
    assert len(_fix5({"A_open": [], "A_full": full})["guards_held"]) == 1


def test_fix5_tracks_ekr_gen_030_without_counting_it():
    """v21 reclassification: EKR_GEN_030 fired in only 1 of 3 single-shot replicates, so the
    k=3 vote is expected to suppress it. It must be reported, and must not move a verdict."""
    assert "EKR_GEN_030" in FIX5_TRACKED and "EKR_GEN_030" not in FIX5_TARGETS
    full = [_case("EKR_GEN_030", case_pass=True, total_attempts=2)]
    r = _fix5({"A_open": [_case("EKR_GEN_030", case_pass=False)], "A_full": full})
    assert r["tracked_fired"] == ["EKR_GEN_030"] and r["tracked_converted"] == ["EKR_GEN_030"]
    assert r["fired"] == [] and r["converted"] == []   # counted nowhere that gates


def _rep(**kw):
    arm = {"evidence_absent": kw.get("ev", 61), "pass_77": (kw.get("p77", 34), 77),
           "pass_88": (kw.get("p88", 34), 88), "late_rewind": kw.get("lr", 5),
           "attrib_fail": kw.get("attr", {"router": 3})}
    full = dict(arm, pass_88=(kw.get("full88", 40), 88))
    return {"run_dir": "r", "arms": {"A_open": arm, "A_full": full},
            "fix5": {"fired": ["a", "b", "c"], "converted": ["a", "b"],
                     "guards_held": FIX5_GUARDS, "guards_total": 2,
                     "tracked_fired": [], "tracked_converted": [],
                     "forced_route_applied": []},
            "mcnemar": {}}


def _verdict_for(n, reps):
    return [v for v in verdicts(reps) if v["n"] == n][0]["verdict"] == "PASS"


def test_all_five_pass_on_a_conforming_replicate():
    reps = [_rep(), _rep(), _rep()]
    assert all(_verdict_for(n, reps) for n in (1, 2, 3, 4, 5))


def test_prediction_2_fails_outside_the_band():
    assert not _verdict_for(2, [_rep(p77=49)])


def test_prediction_3_fails_when_attribution_is_still_summarizer():
    """The half that actually moved on the real run — a conjunction, not an either-or."""
    assert not _verdict_for(3, [_rep(lr=5, attr={"summarizer": 5})])


def test_prediction_5_needs_two_of_three():
    assert _verdict_for(5, [_rep(full88=40), _rep(full88=40), _rep(full88=10)])
    assert not _verdict_for(5, [_rep(full88=40), _rep(full88=10), _rep(full88=10)])


def test_prediction_5_is_pending_not_failed_before_three_replicates():
    """One replicate cannot reach "2 of 3". Calling that FAIL would report a failed study
    at the 1/3 mark of every run."""
    v = [x for x in verdicts([_rep(full88=40)]) if x["n"] == 5][0]["verdict"]
    assert "PENDING" in v and "FAIL" not in v
    # ...but two losses settle it early, with no third replicate needed.
    settled = [x for x in verdicts([_rep(full88=10), _rep(full88=10)]) if x["n"] == 5][0]
    assert settled["verdict"] == "FAIL"


def test_markdown_and_json_report_the_same_verdicts():
    """The two renderings must not be able to drift: --out-json exists so the thesis can
    cite a machine-readable dump, and it is worthless if it disagrees with the markdown."""
    reps = [_rep(), _rep(), _rep()]
    structured = verdicts(reps)
    rendered = _verdicts(reps)
    assert json.loads(json.dumps(structured, default=str))     # dumpable as-is
    for v in structured:
        line = [l for l in rendered if l.startswith(f"| {v['n']} |")][0]
        assert line.rstrip().endswith(f"{v['verdict']} |")


def test_reverse_direction_counts_lost_generics_and_gained_clarifications():
    """v21 P1 clause 2. Prompt v2's criterion is symmetric, so it can move cases both ways:
    CLAR_001 (clarification, misrouted generic) is the intended gain, and a correctly-generic
    case dragged onto the building route is the cost that must stay at zero."""
    from scripts.check_predictions import _reverse
    open_rows = [_case("G_KEEP", route="generic", case_pass=True),
                 _case("G_LOST", route="generic", case_pass=True),
                 _case("CLAR_001", route="clarification", case_pass=False)]
    full_rows = [_case("G_KEEP", route="generic", case_pass=True, final_route_taken="generic"),
                 _case("G_LOST", route="generic", case_pass=False, final_route_taken="building"),
                 _case("CLAR_001", route="clarification", case_pass=True,
                       final_route_taken="building")]
    r = _reverse({"A_open": open_rows, "A_full": full_rows})
    assert r["generic_lost_to_building"] == ["G_LOST"]
    assert r["clarification_gained"] == ["CLAR_001"]


def test_reverse_direction_ignores_a_generic_that_failed_in_both_arms():
    """Only a case the treatment LOST counts as a cost; one that never passed is not one."""
    from scripts.check_predictions import _reverse
    r = _reverse({"A_open": [_case("G", route="generic", case_pass=False)],
                  "A_full": [_case("G", route="generic", case_pass=False,
                                   final_route_taken="building")]})
    assert r["generic_lost_to_building"] == []


def test_consensus_majority_cancels_churn_but_keeps_reproducible_flips(tmp_path, monkeypatch):
    """The pre-registered primary interpretive statistic (§4). A case that flips in one
    replicate only must NOT count; one that flips in a majority must."""
    from scripts import check_predictions as m

    # STEADY passes everywhere in both arms; CHURN passes A_full once (noise);
    # REAL passes A_full in 2 of 3 (a reproducible gain); LOST goes the other way.
    per_rep = [
        {"A_open": {"STEADY": 1, "LOST": 1}, "A_full": {"STEADY": 1, "CHURN": 1, "REAL": 1}},
        {"A_open": {"STEADY": 1, "LOST": 1}, "A_full": {"STEADY": 1, "REAL": 1}},
        {"A_open": {"STEADY": 1, "LOST": 1}, "A_full": {"STEADY": 1}},
    ]
    ids = ["STEADY", "CHURN", "REAL", "LOST"]

    def fake_traces(rd, arm, kind):
        idx = int(str(rd))
        return [{"case_id": c, "case_pass": bool(per_rep[idx][arm].get(c))} for c in ids]

    monkeypatch.setattr(m, "_traces", fake_traces)
    out = m.consensus_mcnemar([Path("0"), Path("1"), Path("2")])
    assert out["majority_needed"] == 2
    assert out["gained"] == ["REAL"]        # 2 of 3 survives
    assert out["lost"] == ["LOST"]          # and a loss is not hidden
    assert "CHURN" not in out["gained"]     # 1 of 3 cancels
