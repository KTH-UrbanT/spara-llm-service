"""v25 additions to spotcheck_closed_loop.py — the v2-rubric audit path.

plan-eil-v24 replaced the judge axis `question_coverage` with `entity_consistency`,
and plan-eil-v25 audits the v2 judge against a human over a frame spanning all nine
v24 arm-runs. Two things had to change and are pinned here:

  * the axis list is derived from the data, not hardcoded, so v1 and v2 artifacts
    are both readable by the same tool (the v1 side is pinned in
    test_spotcheck_closed_loop.py);
  * rows are keyed by `row_id` = `case@run:arm`, because the same `case_id` occurs
    in up to nine arm-runs and the audit deliberately samples the same case from
    different replicates.
"""
import argparse
import csv
import json

import pytest

from scripts.spotcheck_closed_loop import (
    _axes_from_traces, _axes_from_worklist, _label_fields, _worklist_fields,
    _parse_form, _opaque, _row_id, _key, cmd_form, cmd_ingest_md, cmd_kappa, cmd_sample,
)

V2_AXES = ["faithfulness", "answer_relevance", "entity_consistency", "calibration"]


def _sample_ns(**kw):
    base = dict(run_dir=None, arm="A_open", source=None, include_rows=None,
                pad_null=0, pad_scored=0, null_axis="entity_consistency",
                seed=1, all_judged=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _row(cid, axes, run="v24_r1", arm="A_open", verdict="pass", **kw):
    return {"case_id": cid, "run_id": run, "arm": arm, "expected_route": "building_specific",
            "final_answer": f"answer {cid}", "final_building_id_resolved": "19-84-LEOPARDEN5-1",
            "answer_quality_verdict": {"verdict": verdict, "axes": axes, "composite": 8.0},
            "semantic_judge_pass": verdict == "pass", **kw}


def _frame(tmp_path, spec):
    """spec: {(run, arm): [rows]} → (source strings, dataset path)."""
    sources, seen = [], {}
    for (run, arm), rows in spec.items():
        d = tmp_path / run / arm / "traces"
        d.mkdir(parents=True)
        (d / "per_case.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        sources.append(f"{tmp_path / run}:{arm}")
        for r in rows:
            seen[r["case_id"]] = f"q {r['case_id']}"
    ds = tmp_path / "dataset.jsonl"
    ds.write_text("\n".join(json.dumps({"case_id": c, "question": q})
                            for c, q in seen.items()) + "\n", encoding="utf-8")
    return sources, ds


def _worklist_rows(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


# --- axis derivation ------------------------------------------------------------------

def test_axes_follow_the_traces_not_a_hardcoded_list():
    v2 = _axes_from_traces([_row("A", {"faithfulness": 10, "answer_relevance": 9,
                                       "entity_consistency": 0, "calibration": 9})])
    assert v2 == V2_AXES
    v1 = _axes_from_traces([_row("A", {"faithfulness": 10, "answer_relevance": 9,
                                       "question_coverage": 10, "calibration": 9})])
    assert "question_coverage" in v1 and "entity_consistency" not in v1


def test_axis_derivation_ignores_marker_keys():
    """`_fail_open` / `_short_circuit` are markers, not axes."""
    axes = _axes_from_traces([_row("A", {"calibration": 9, "_fail_open": True})])
    assert axes == ["calibration"]


def test_axes_recovered_from_either_vintage_of_worklist_header():
    assert _axes_from_worklist(_worklist_fields(V2_AXES)) == V2_AXES
    assert "question_coverage" in _axes_from_worklist(
        _worklist_fields(["faithfulness", "question_coverage"]))


def test_non_axis_judge_columns_are_not_mistaken_for_axes():
    for f in ("judge_verdict", "judge_composite", "judge_semantic_judge_pass",
              "judge_fail_open", "judge_evidence_present"):
        assert f[len("judge_"):] not in _axes_from_worklist(_worklist_fields(V2_AXES))


# --- row identity ---------------------------------------------------------------------

def test_row_id_separates_the_same_case_in_different_arms():
    a = _row("C1", {"calibration": 9}, run="v24_r1", arm="A_open")
    b = _row("C1", {"calibration": 9}, run="v24_r1", arm="A_full")
    assert _row_id(a) != _row_id(b)
    assert _row_id(a) == "C1@v24_r1:A_open"


def test_key_falls_back_to_case_id_for_pre_v25_files():
    assert _key({"case_id": "C1"}) == "C1"
    assert _key({"case_id": "C1", "row_id": ""}) == "C1"
    assert _key({"case_id": "C1", "row_id": "C1@v24_r2:A_full"}) == "C1@v24_r2:A_full"


# --- sampling -------------------------------------------------------------------------

def test_sample_spans_multiple_arm_runs_without_collapsing_duplicates(tmp_path):
    sources, ds = _frame(tmp_path, {
        ("v24_r1", "A_open"): [_row("C1", {"entity_consistency": 0}, arm="A_open")],
        ("v24_r1", "A_full"): [_row("C1", {"entity_consistency": 10}, arm="A_full")],
    })
    inc = tmp_path / "inc.txt"
    inc.write_text("# mandatory\nC1@v24_r1:A_open\nC1@v24_r1:A_full\n", encoding="utf-8")
    wl = tmp_path / "wl.csv"
    cmd_sample(_sample_ns(source=sources, dataset=ds, worklist=wl, include_rows=inc))

    got = _worklist_rows(wl)
    assert len(got) == 2                                   # same case, two rows
    assert {r["row_id"] for r in got} == {"C1@v24_r1:A_open", "C1@v24_r1:A_full"}
    assert {r["judge_entity_consistency"] for r in got} == {"0", "10"}


def test_missing_mandatory_row_is_a_hard_error(tmp_path):
    sources, ds = _frame(tmp_path, {("v24_r1", "A_open"): [_row("C1", {"calibration": 9})]})
    inc = tmp_path / "inc.txt"
    inc.write_text("C1@v24_r1:A_open\nGHOST@v24_r9:A_full\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="GHOST"):
        cmd_sample(_sample_ns(source=sources, dataset=ds,
                              worklist=tmp_path / "wl.csv", include_rows=inc))


def test_pads_split_on_the_null_axis(tmp_path):
    """pad-null takes not-applicable rows; pad-scored takes judge-pass scored rows."""
    rows = [_row("N1", {"entity_consistency": None, "calibration": 9}),
            _row("N2", {"entity_consistency": None, "calibration": 9}),
            _row("S1", {"entity_consistency": 10, "calibration": 9}),
            _row("S2", {"entity_consistency": 10, "calibration": 9}),
            _row("F1", {"entity_consistency": 0, "calibration": 9}, verdict="fail")]
    sources, ds = _frame(tmp_path, {("v24_r1", "A_open"): rows})
    wl = tmp_path / "wl.csv"
    cmd_sample(_sample_ns(source=sources, dataset=ds, worklist=wl,
                          pad_null=2, pad_scored=2))

    got = {r["case_id"]: r for r in _worklist_rows(wl)}
    assert set(got) == {"N1", "N2", "S1", "S2"}            # judge-fail row not padded in
    assert got["N1"]["judge_entity_consistency"] == ""     # null → blank cell, not "None"


def test_sample_needs_exactly_one_frame_source(tmp_path):
    sources, ds = _frame(tmp_path, {("v24_r1", "A_open"): [_row("C1", {"calibration": 9})]})
    for kw in ({}, {"source": sources, "run_dir": tmp_path / "v24_r1"}):
        with pytest.raises(SystemExit, match="exactly one"):
            cmd_sample(_sample_ns(dataset=ds, worklist=tmp_path / "wl.csv", **kw))


def test_worklist_shows_resolved_building_but_no_gold(tmp_path):
    """The blindness contract: the annotator needs the system's own resolution to judge
    entity_consistency at all, but must never see the gold building id."""
    sources, ds = _frame(tmp_path, {
        ("v24_r1", "A_open"): [_row("C1", {"entity_consistency": 0},
                                    expected_building_id="GOLD-XYZ")]})
    wl = tmp_path / "wl.csv"
    cmd_sample(_sample_ns(source=sources, dataset=ds, worklist=wl, pad_null=0, pad_scored=1))

    row = _worklist_rows(wl)[0]
    assert row["final_building_id_resolved"] == "19-84-LEOPARDEN5-1"
    assert "GOLD-XYZ" not in ",".join(row.values())
    assert not any(k.startswith("expected_building") for k in row)


# --- kappa ----------------------------------------------------------------------------

def _csv(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    return path


def _kappa(tmp_path, wl_rows, lb_rows, axes=V2_AXES):
    out = tmp_path / "kappa.json"
    cmd_kappa(argparse.Namespace(
        worklist=_csv(tmp_path / "wl.csv", _worklist_fields(axes), wl_rows),
        labels=_csv(tmp_path / "lb.csv", _label_fields(axes), lb_rows),
        out=out, n_resamples=50, seed=1))
    return json.loads(out.read_text(encoding="utf-8"))


_JUDGE = {"judge_faithfulness": 10, "judge_answer_relevance": 9,
          "judge_calibration": 9, "judge_semantic_judge_pass": "True",
          "judge_fail_open": False}
_HUMAN = {"faithfulness": 10, "answer_relevance": 9, "calibration": 9, "overall_pass": 1}


def test_kappa_pairs_each_label_with_its_own_arm(tmp_path):
    """The bug row_id exists to prevent: two arms of one case must not collapse."""
    wl = [dict(_JUDGE, row_id="C1@v24_r1:A_open", case_id="C1", judge_entity_consistency=0),
          dict(_JUDGE, row_id="C1@v24_r1:A_full", case_id="C1", judge_entity_consistency=10)]
    lb = [dict(_HUMAN, row_id="C1@v24_r1:A_open", case_id="C1", entity_consistency=0),
          dict(_HUMAN, row_id="C1@v24_r1:A_full", case_id="C1", entity_consistency=10)]
    out = _kappa(tmp_path, wl, lb)
    assert out["n_paired"] == 2
    ec = out["per_axis_kappa_weighted"]["entity_consistency"]
    assert ec["n"] == 2 and ec["binarised_at_floor"]["raw_agreement"] == 1.0


def test_null_entity_scores_drop_out_instead_of_counting_as_zero(tmp_path):
    """v24's None-rule, carried into the audit: an inapplicable axis contributes no pair.
    Reading the blank as 0 would fabricate perfect agreement on the 567 null rows."""
    wl = [dict(_JUDGE, row_id=f"C{i}@v24_r1:A_open", case_id=f"C{i}",
               judge_entity_consistency="") for i in range(4)]
    lb = [dict(_HUMAN, row_id=f"C{i}@v24_r1:A_open", case_id=f"C{i}",
               entity_consistency="") for i in range(4)]
    out = _kappa(tmp_path, wl, lb)
    assert out["n_paired"] == 4
    assert out["per_axis_kappa_weighted"]["entity_consistency"]["n"] == 0
    assert out["per_axis_kappa_weighted"]["calibration"]["n"] == 4


# --- markdown form round-trip ---------------------------------------------------------

def _form_worklist(tmp_path):
    """Two rows of the SAME case from different arms — the collapse hazard."""
    rows = [dict(_JUDGE, row_id="C1@v24_r1:A_open", case_id="C1", question="q1",
                 final_answer="a-open", retrieved_evidence_summary="ev",
                 expected_route="building_specific",
                 final_building_id_resolved="19-84-LEOPARDEN5-1",
                 judge_verdict="fail", judge_entity_consistency=0),
            dict(_JUDGE, row_id="C1@v24_r1:A_full", case_id="C1", question="q1",
                 final_answer="a-full", retrieved_evidence_summary="ev",
                 expected_route="building_specific",
                 final_building_id_resolved="19-84-LEOPARDEN5-1",
                 judge_verdict="pass", judge_entity_consistency=10)]
    return _csv(tmp_path / "wl.csv", _worklist_fields(V2_AXES), rows)


def test_form_headings_are_opaque_and_hide_the_arm(tmp_path):
    """Seeing `A_full` would tell the annotator the loop got a second attempt at this
    answer — which is the very thing the label is supposed to judge independently."""
    wl = _form_worklist(tmp_path)
    form = tmp_path / "form.md"
    cmd_form(argparse.Namespace(worklist=wl, form=form, labels=tmp_path / "lb.csv"))
    text = form.read_text(encoding="utf-8")

    blocks = _parse_form(text)
    assert len(blocks) == 2
    assert {b["heading"] for b in blocks} == {_opaque("C1@v24_r1:A_open"),
                                             _opaque("C1@v24_r1:A_full")}
    assert "A_full" not in text and "A_open" not in text and "v24_r1" not in text
    # ...but the annotator does get the resolved building id, and never a judge score.
    assert "19-84-LEOPARDEN5-1" in text
    assert "judge_entity_consistency" not in text and "judge_verdict" not in text


def test_opaque_form_refuses_to_ingest_without_the_worklist(tmp_path):
    wl = _form_worklist(tmp_path)
    form = tmp_path / "form.md"
    cmd_form(argparse.Namespace(worklist=wl, form=form, labels=tmp_path / "lb.csv"))
    filled = form.read_text(encoding="utf-8").replace("`[ ]`", "`[1]`")
    form.write_text(filled, encoding="utf-8")
    with pytest.raises(SystemExit, match="opaque"):
        cmd_ingest_md(argparse.Namespace(form=form, labels=tmp_path / "lb.csv",
                                         worklist=None, annotator_id="nico"))


def test_form_to_labels_round_trip_keeps_the_two_arms_apart(tmp_path):
    wl = _form_worklist(tmp_path)
    form = tmp_path / "form.md"
    cmd_form(argparse.Namespace(worklist=wl, form=form, labels=tmp_path / "lb.csv"))

    filled = form.read_text(encoding="utf-8")
    # Fill the first block with 0s and the second with 10s.
    head, sep, tail = filled.partition("## Case 2 / 2")
    head = head.replace("`[ ]`", "`[0]`")
    tail = tail.replace("(0–10): `[ ]`", "(0–10): `[10]`").replace(
        "overall_pass (0 or 1): `[ ]`", "overall_pass (0 or 1): `[1]`")
    form.write_text(head + sep + tail, encoding="utf-8")

    labels = tmp_path / "lb.csv"
    ns = argparse.Namespace(form=form, labels=labels, worklist=wl, annotator_id="nico")
    cmd_ingest_md(ns)
    cmd_ingest_md(ns)                      # idempotent: same two rows, not four

    with labels.open(encoding="utf-8") as f:
        got = list(csv.DictReader(f))
    assert len(got) == 2
    by_id = {r["row_id"]: r for r in got}
    assert by_id["C1@v24_r1:A_open"]["entity_consistency"] == "0"
    assert by_id["C1@v24_r1:A_full"]["entity_consistency"] == "10"

    # ...and those labels join back onto the right judge scores.
    out = tmp_path / "k.json"
    cmd_kappa(argparse.Namespace(worklist=wl, labels=labels, out=out,
                                 n_resamples=20, seed=1))
    ec = json.loads(out.read_text(encoding="utf-8"))["per_axis_kappa_weighted"]["entity_consistency"]
    assert ec["n"] == 2 and ec["binarised_at_floor"]["raw_agreement"] == 1.0


def test_a_blank_axis_keeps_the_row_instead_of_silently_dropping_it(tmp_path):
    """15 of the 65 audit rows have no evidence and no identified building, so
    entity_consistency has nothing to score against. If a blank axis invalidated the
    whole block, leaving it empty would delete the row from the audit without a word."""
    wl = _form_worklist(tmp_path)
    form = tmp_path / "form.md"
    cmd_form(argparse.Namespace(worklist=wl, form=form, labels=tmp_path / "lb.csv"))
    filled = (form.read_text(encoding="utf-8")
              .replace("overall_pass (0 or 1): `[ ]`", "overall_pass (0 or 1): `[1]`")
              .replace("faithfulness (0–10): `[ ]`", "faithfulness (0–10): `[9]`")
              .replace("answer_relevance (0–10): `[ ]`", "answer_relevance (0–10): `[9]`")
              .replace("calibration (0–10): `[ ]`", "calibration (0–10): `[9]`"))
    form.write_text(filled, encoding="utf-8")     # entity_consistency left blank

    labels = tmp_path / "lb.csv"
    cmd_ingest_md(argparse.Namespace(form=form, labels=labels, worklist=wl,
                                     annotator_id="nico"))
    with labels.open(encoding="utf-8") as f:
        got = list(csv.DictReader(f))
    assert len(got) == 2                                   # rows kept
    assert all(r["entity_consistency"] == "" for r in got)  # blank, not 0
    assert all(r["calibration"] == "9" for r in got)

    out = tmp_path / "k.json"
    cmd_kappa(argparse.Namespace(worklist=wl, labels=labels, out=out,
                                 n_resamples=20, seed=1))
    axes = json.loads(out.read_text(encoding="utf-8"))["per_axis_kappa_weighted"]
    assert axes["entity_consistency"]["n"] == 0            # dropped from its pool...
    assert axes["calibration"]["n"] == 2                   # ...the others still count


def test_a_block_with_no_axis_scored_is_still_an_error(tmp_path):
    from scripts.spotcheck_closed_loop import _validate_label
    assert _validate_label({"overall_pass": "1"}, V2_AXES) == "no axis scored"


def test_binarised_axis_stats_reported_for_the_v2_fallback_gate(tmp_path):
    """V2-fb: at a skewed marginal weighted κ collapses, so the gate falls back to AC1
    on fired/did-not-fire at the 4.0 floor. Both must be present to choose between them."""
    wl = [dict(_JUDGE, row_id=f"C{i}@v24_r1:A_open", case_id=f"C{i}",
               judge_entity_consistency=(0 if i < 2 else 10)) for i in range(10)]
    lb = [dict(_HUMAN, row_id=f"C{i}@v24_r1:A_open", case_id=f"C{i}",
               entity_consistency=(0 if i < 2 else 10)) for i in range(10)]
    ec = _kappa(tmp_path, wl, lb)["per_axis_kappa_weighted"]["entity_consistency"]
    b = ec["binarised_at_floor"]
    assert b["floor"] == 4
    assert b["n_judge_fired"] == 2 and b["n_human_fired"] == 2
    assert b["ac1"] == pytest.approx(1.0)
    assert set(b) >= {"raw_agreement", "ac1", "ac1_ci95"}
    assert "k_gt0_ci_excludes_0" in ec
