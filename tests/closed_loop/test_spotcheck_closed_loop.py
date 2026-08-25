"""Tests for the `ingest-md` markdown→labels bridge in spotcheck_closed_loop.py.

The blinded κ annotation is done in a markdown form; these tests pin the parser
that turns that filled form into the LABEL_FIELDS CSV the `kappa` step consumes.
"""
import argparse
import csv
import json
from pathlib import Path

import pytest

from scripts.spotcheck_closed_loop import (
    _parse_form, _is_blank_label, _validate_label, cmd_ingest_md,
    _ac1, _raw_agreement, _was_judged, cmd_audit_ungrounded, cmd_kappa, cmd_sample,
    AUDIT_FIELDS, _label_fields, _worklist_fields,
)

# The v1 rubric this file pins. v25 made the axis list data-derived so both vintages
# stay readable; these tests are the regression guard for the v1 side.
V1_AXES = ["faithfulness", "answer_relevance", "question_coverage", "calibration"]
LABEL_FIELDS = _label_fields(V1_AXES)
WORKLIST_FIELDS = _worklist_fields(V1_AXES)


def _sample_ns(**kw):
    """cmd_sample Namespace with the v25 sampling options defaulted off."""
    return argparse.Namespace(
        source=None, include_rows=None, pad_null=0, pad_scored=0,
        null_axis="entity_consistency", all_judged=False, **kw)

# One filled case + one fully-blank case. Uses the real form's punctuation:
# em dash (—) in the heading, en dash (–) in the "(0–10)" labels, backtick-
# wrapped brackets around the score. The leading instructions contain a stray
# `[ ]` that must NOT be parsed as a score.
MINI_FORM = """# Stage 3 — closed-loop κ labelling (blinded)

Fill the bracketed `[ ]` placeholders inline.

---

## Case 1 / 2 — `EKR_GEN_006`

**Expected route:** `generic`

### Question

> What does energy-efficiency improvement mean?

### Model's final answer

```
Some fluent answer.
```

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Your scores

- faithfulness (0–10): `[10]`
- answer_relevance (0–10): `[9]`
- question_coverage (0–10): `[8]`
- calibration (0–10): `[7]`
- overall_pass (0 or 1): `[1]`
- notes: broadly accurate; answers the question

---

## Case 2 / 2 — `DEMO_EXTRA_BRF_002`

### Your scores

- faithfulness (0–10): `[ ]`
- answer_relevance (0–10): `[ ]`
- question_coverage (0–10): `[ ]`
- calibration (0–10): `[ ]`
- overall_pass (0 or 1): `[ ]`
- notes:
"""


def test_parse_extracts_case_ids_in_order():
    """Form blocks parse in document order, so labels line up with their worklist rows."""
    blocks = _parse_form(MINI_FORM)
    assert [b["case_id"] for b in blocks] == ["EKR_GEN_006", "DEMO_EXTRA_BRF_002"]


def test_parse_extracts_all_five_scores_and_notes():
    """Every scored field and the free-text note survive parsing."""
    b0 = _parse_form(MINI_FORM)[0]
    assert b0["faithfulness"] == "10"
    assert b0["answer_relevance"] == "9"
    assert b0["question_coverage"] == "8"
    assert b0["calibration"] == "7"
    assert b0["overall_pass"] == "1"
    assert b0["notes"] == "broadly accurate; answers the question"


def test_blank_block_detected():
    """An unfilled block is recognised as blank, so it is skipped rather than scored 0."""
    blocks = _parse_form(MINI_FORM)
    assert _is_blank_label(blocks[0], V1_AXES) is False
    assert _is_blank_label(blocks[1], V1_AXES) is True


def test_validate_accepts_in_range():
    """A fully scored, in-range block validates clean."""
    assert _validate_label({"faithfulness": "10", "answer_relevance": "9",
                            "question_coverage": "8", "calibration": "7",
                            "overall_pass": "1"}, V1_AXES) is None


def test_validate_rejects_out_of_range_axis():
    """An out-of-range axis is rejected and the error names the offending axis."""
    err = _validate_label({"faithfulness": "12", "answer_relevance": "9",
                           "question_coverage": "8", "calibration": "7",
                           "overall_pass": "1"}, V1_AXES)
    assert err is not None and "faithfulness" in err


def test_validate_rejects_bad_overall_pass():
    """`overall_pass` must be 0 or 1."""
    err = _validate_label({"faithfulness": "10", "answer_relevance": "9",
                           "question_coverage": "8", "calibration": "7",
                           "overall_pass": "2"}, V1_AXES)
    assert err is not None and "overall_pass" in err


def test_ingest_md_writes_only_filled_cases(tmp_path):
    """Only filled blocks reach the labels CSV; blanks are not written as rows."""
    form = tmp_path / "form.md"
    form.write_text(MINI_FORM, encoding="utf-8")
    labels = tmp_path / "labels.csv"
    cmd_ingest_md(argparse.Namespace(form=form, labels=labels, annotator_id="nico"))

    with labels.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1                      # blank case skipped
    r = rows[0]
    assert r["case_id"] == "EKR_GEN_006"
    assert r["faithfulness"] == "10" and r["overall_pass"] == "1"
    assert r["annotator_id"] == "nico"


def test_ingest_md_is_idempotent(tmp_path):
    """Re-ingesting the same form does not duplicate already-written rows."""
    form = tmp_path / "form.md"
    form.write_text(MINI_FORM, encoding="utf-8")
    labels = tmp_path / "labels.csv"
    ns = argparse.Namespace(form=form, labels=labels, annotator_id="nico")
    cmd_ingest_md(ns)
    cmd_ingest_md(ns)                          # second pass: case already done
    with labels.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# v21 §2.4 — prevalence-robust agreement, and the fail-open filter that was
# silently discarding most of the judged pool.
# ---------------------------------------------------------------------------

def test_ac1_is_stable_exactly_where_kappa_collapses():
    """The prevalence paradox, in one assertion. 18 agreed passes, 2 disagreements:
    90% raw agreement, yet Cohen's κ goes NEGATIVE because both raters almost always
    say 'pass'. Gating on κ >= 0.6 here would reject a judge that agrees 9 times in 10."""
    from scripts.spotcheck_closed_loop import _kappa_binary
    pairs = [(1, 1)] * 18 + [(1, 0), (0, 1)]
    assert _raw_agreement(pairs) == pytest.approx(0.90)
    assert _kappa_binary(pairs) < 0.0
    assert _ac1(pairs) == pytest.approx(0.8895, abs=1e-4)


def test_ac1_degenerate_inputs_do_not_crash():
    """AC1 handles the zero-variance edges that make kappa undefined."""
    assert _ac1([]) == 0.0
    assert _ac1([(1, 1)] * 5) == 1.0        # perfect agreement, zero variance


def _worklist(tmp_path, rows):
    """Write a worklist CSV and return its path."""
    p = tmp_path / "worklist.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=WORKLIST_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in WORKLIST_FIELDS})
    return p


def _labels(tmp_path, rows):
    """Write a labels CSV and return its path."""
    p = tmp_path / "labels.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LABEL_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in LABEL_FIELDS})
    return p


_JUDGED_AXES = {"judge_answer_relevance": 9, "judge_question_coverage": 9,
                "judge_calibration": 9, "judge_semantic_judge_pass": "True",
                "judge_fail_open": False}
_HUMAN = {"answer_relevance": 9, "question_coverage": 9, "calibration": 9,
          "faithfulness": 9, "overall_pass": 1}


def _kappa(tmp_path, worklist_rows, label_rows):
    """Run the kappa sub-command over a worklist/labels pair and return its output."""
    out = tmp_path / "kappa.json"
    cmd_kappa(argparse.Namespace(worklist=_worklist(tmp_path, worklist_rows),
                                 labels=_labels(tmp_path, label_rows),
                                 out=out, n_resamples=50, seed=1))
    return json.loads(out.read_text(encoding="utf-8"))


def test_kappa_keeps_judged_rows_whose_faithfulness_is_null(tmp_path):
    """The §1.8 instrument bug. After Fix 4, faithfulness is legitimately blank on the 61
    evidence-absent cases; the old filter read that blank as 'fail-open' and dropped them,
    which would have thrown away most of the agreement pool."""
    wl = [dict(_JUDGED_AXES, case_id=f"C{i}", judge_faithfulness="") for i in range(4)]
    lb = [dict(_HUMAN, case_id=f"C{i}") for i in range(4)]
    out = _kappa(tmp_path, wl, lb)
    assert out["n_paired"] == 4 and out["n_dropped_fail_open"] == 0
    # ...and the null axis simply contributes no pairs, rather than pairing against nothing.
    assert out["per_axis_kappa_weighted"]["faithfulness"]["n"] == 0
    assert out["per_axis_kappa_weighted"]["calibration"]["n"] == 4


def test_kappa_drops_only_fail_open_rows(tmp_path):
    """Fail-open rows are excluded from agreement; genuinely judged rows are kept."""
    wl = [dict(_JUDGED_AXES, case_id="C0", judge_faithfulness=8),
          dict(_JUDGED_AXES, case_id="C1", judge_faithfulness="", judge_fail_open=True)]
    lb = [dict(_HUMAN, case_id="C0"), dict(_HUMAN, case_id="C1")]
    out = _kappa(tmp_path, wl, lb)
    assert out["n_paired"] == 1 and out["n_dropped_fail_open"] == 1


def test_kappa_reports_gates_and_flags_kappa_as_ungated(tmp_path):
    """Gates are reported on agreement and AC1; kappa itself is explicitly ungated.

    On a skewed label distribution kappa collapses even at near-total agreement, so
    gating on it would fail a panel that is in fact consistent.
    """
    wl = [dict(_JUDGED_AXES, case_id=f"C{i}", judge_faithfulness=9) for i in range(10)]
    lb = [dict(_HUMAN, case_id=f"C{i}") for i in range(10)]
    out = _kappa(tmp_path, wl, lb)
    assert out["gates"] == {"raw_agreement_ge_0.90": True, "ac1_ge_0.6": True}
    assert "prevalence" in out["kappa_caveat"]


# --- sampling -------------------------------------------------------------------------

def _run_dir(tmp_path, rows):
    """Lay out a run directory containing one arm's per-case trace."""
    d = tmp_path / "run" / "A_open" / "traces"
    d.mkdir(parents=True)
    (d / "per_case.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    ds = tmp_path / "dataset.jsonl"
    ds.write_text("\n".join(json.dumps({"case_id": r["case_id"], "question": f"q {r['case_id']}"})
                            for r in rows) + "\n", encoding="utf-8")
    return tmp_path / "run", ds


def _row(cid, axes, **kw):
    """A judged, passing trace row carrying the given axis scores."""
    return {"case_id": cid, "expected_route": "generic", "final_answer": f"answer {cid}",
            "answer_quality_verdict": {"verdict": "pass", "axes": axes, "composite": 8.0},
            "semantic_judge_pass": True, **kw}


def test_was_judged_excludes_short_circuits_and_fail_opens():
    """Only genuinely scored attempts count as judged — not short-circuits or crashes."""
    assert _was_judged(_row("a", {"calibration": 9}))
    assert not _was_judged(_row("b", {"_fail_open": True}))
    assert not _was_judged(_row("c", {"_short_circuit": True}))
    assert not _was_judged(_row("d", {}))


def test_all_judged_takes_the_whole_judged_pool(tmp_path):
    """Sampling "all judged" takes every judged row, not a subsample of it."""
    rows = [_row("A", {"calibration": 9, "faithfulness": None}),
            _row("B", {"calibration": 8}),
            _row("C", {"_short_circuit": True}),
            _row("D", {"_fail_open": True, "_error": "boom"})]
    run, ds = _run_dir(tmp_path, rows)
    wl = tmp_path / "wl.csv"
    ns = _sample_ns(run_dir=run, arm="A_open", dataset=ds, worklist=wl, seed=1)
    ns.all_judged = True
    cmd_sample(ns)
    with wl.open(encoding="utf-8") as f:
        got = list(csv.DictReader(f))
    assert sorted(r["case_id"] for r in got) == ["A", "B"]
    assert got[0]["judge_fail_open"] == "False"


# --- audit-ungrounded -----------------------------------------------------------------

def _audit_ns(run, ds, sheet, n=20):
    """CLI namespace for the audit sub-command."""
    return argparse.Namespace(run_dir=run, arm="A_open", dataset=ds, sheet=sheet,
                              n=n, seed=1)


def test_audit_samples_only_evidence_absent_answers(tmp_path):
    """The evidence-absent audit must not pull in rows that did have evidence."""
    rows = [_row("A", {"calibration": 9}, evidence_present=False),
            _row("B", {"calibration": 9}, evidence_present=True),
            _row("C", {"calibration": 9}, evidence_present=False)]
    run, ds = _run_dir(tmp_path, rows)
    sheet = tmp_path / "audit.csv"
    cmd_audit_ungrounded(_audit_ns(run, ds, sheet))
    with sheet.open(encoding="utf-8") as f:
        got = list(csv.DictReader(f))
    assert [r["case_id"] for r in got] == ["A", "C"]
    assert got[0]["question"] == "q A"                    # question shown
    assert got[0]["fabricated_fact_suspected"] == ""      # judge fields hidden, blank to fill


def test_audit_tallies_against_the_pre_registered_gate(tmp_path, capsys):
    """Re-running over a filled sheet reports the fabrication count and the gate verdict."""
    sheet = tmp_path / "audit.csv"
    with sheet.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=AUDIT_FIELDS)
        w.writeheader()
        for i in range(5):
            w.writerow({"case_id": f"C{i}", "question": "q", "final_answer": "a",
                        "contains_building_specific_claim": "1" if i < 2 else "0",
                        "fabricated_fact_suspected": "1" if i < 3 else "0", "notes": ""})
    cmd_audit_ungrounded(_audit_ns(Path("unused"), Path("unused"), sheet))
    got = json.loads(capsys.readouterr().out)
    assert got["n_labelled"] == 5 and got["complete"] is True
    assert got["contains_building_specific_claim"] == 2
    assert got["fabricated_fact_suspected"] == 3
    assert got["gate_pass"] is False        # 3 > 2
