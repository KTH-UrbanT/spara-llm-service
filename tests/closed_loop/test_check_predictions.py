"""The prediction check decides whether the pre-registration held, so its verdict logic
needs its own check — a silently-inverted comparison would report PASS on a failed study."""
from scripts.check_predictions import _fix5, _judged, _n77, _verdicts, FIX5_GUARDS


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


def _rep(**kw):
    arm = {"evidence_absent": kw.get("ev", 61), "pass_77": (kw.get("p77", 34), 77),
           "pass_88": (kw.get("p88", 34), 88), "late_rewind": kw.get("lr", 5),
           "attrib_fail": kw.get("attr", {"router": 3})}
    full = dict(arm, pass_88=(kw.get("full88", 40), 88))
    return {"run_dir": "r", "arms": {"A_open": arm, "A_full": full},
            "fix5": {"fired": ["a", "b", "c"], "converted": ["a", "b"],
                     "guards_held": FIX5_GUARDS, "guards_total": 2},
            "mcnemar": {}}


def _verdict_for(n, reps):
    return [l for l in _verdicts(reps) if l.startswith(f"| {n} |")][0].endswith("PASS |")


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
    line = [l for l in _verdicts([_rep(full88=40)]) if l.startswith("| 5 |")][0]
    assert "PENDING" in line and "FAIL" not in line
    # ...but two losses settle it early, with no third replicate needed.
    settled = [l for l in _verdicts([_rep(full88=10), _rep(full88=10)])
               if l.startswith("| 5 |")][0]
    assert settled.endswith("FAIL |")
