"""Tests for the `ingest-md` markdown→labels bridge in spotcheck_closed_loop.py.

The blinded κ annotation is done in a markdown form; these tests pin the parser
that turns that filled form into the LABEL_FIELDS CSV the `kappa` step consumes.
"""
import argparse
import csv

from scripts.spotcheck_closed_loop import (
    _parse_form, _is_blank_label, _validate_label, cmd_ingest_md,
)

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
    blocks = _parse_form(MINI_FORM)
    assert [b["case_id"] for b in blocks] == ["EKR_GEN_006", "DEMO_EXTRA_BRF_002"]


def test_parse_extracts_all_five_scores_and_notes():
    b0 = _parse_form(MINI_FORM)[0]
    assert b0["faithfulness"] == "10"
    assert b0["answer_relevance"] == "9"
    assert b0["question_coverage"] == "8"
    assert b0["calibration"] == "7"
    assert b0["overall_pass"] == "1"
    assert b0["notes"] == "broadly accurate; answers the question"


def test_blank_block_detected():
    blocks = _parse_form(MINI_FORM)
    assert _is_blank_label(blocks[0]) is False
    assert _is_blank_label(blocks[1]) is True


def test_validate_accepts_in_range():
    assert _validate_label({"faithfulness": "10", "answer_relevance": "9",
                            "question_coverage": "8", "calibration": "7",
                            "overall_pass": "1"}) is None


def test_validate_rejects_out_of_range_axis():
    err = _validate_label({"faithfulness": "12", "answer_relevance": "9",
                           "question_coverage": "8", "calibration": "7",
                           "overall_pass": "1"})
    assert err is not None and "faithfulness" in err


def test_validate_rejects_bad_overall_pass():
    err = _validate_label({"faithfulness": "10", "answer_relevance": "9",
                           "question_coverage": "8", "calibration": "7",
                           "overall_pass": "2"})
    assert err is not None and "overall_pass" in err


def test_ingest_md_writes_only_filled_cases(tmp_path):
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
