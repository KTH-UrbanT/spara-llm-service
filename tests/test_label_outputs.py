"""Tests for ``scripts/label_outputs.py``.

Per plan-eil-v2.md §C.3: exercise the labeller with stdin-driven
fixtures only — no Azure, no real DB. ``monkeypatch`` replaces
``label_outputs._ask`` with a generator drawing from a queue of
pre-canned responses.

Each test is self-contained: it builds a tiny but valid run-dir with
``A1`` and ``A2`` results.jsonl + matching ``aggregated_cache`` +
``questions.json`` and runs the labeller against it.
"""
from __future__ import annotations

import csv
import io
import json
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from scripts import label_outputs as lo


# ---------------------------------------------------------------------------
# Fixture helpers.
# ---------------------------------------------------------------------------
def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _build_run_dir(
    tmp_path: Path,
    qids: list[str] = None,
    arms: list[str] = None,
    inject_error_qid: str | None = None,
    categories: dict[str, str] | None = None,
) -> tuple[Path, Path]:
    """Create a run-dir + questions.json. Returns (run_dir, questions_path).

    By default 2 questions × 2 arms (A1, A2) = 4 outputs.
    ``inject_error_qid`` makes that question_id's row in EVERY arm carry
    an ``error`` field instead of an answer.
    """
    qids = qids or ["Q001", "Q002"]
    arms = arms or ["A1", "A2"]
    categories = categories or {q: "simple_address" for q in qids}

    run_dir = tmp_path / "run"
    questions_path = tmp_path / "questions.json"

    for arm in arms:
        rows = []
        for qid in qids:
            base = {"question_id": qid, "arm": arm, "run_id": "test"}
            if qid == inject_error_qid:
                base["error"] = "synthetic crash"
            else:
                base["final_response"] = f"answer for {qid} in {arm}"
            rows.append(base)
        _write_jsonl(run_dir / arm / "results.jsonl", rows)
        # Aggregated cache.
        cache_dir = run_dir / arm / "aggregated_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        for qid in qids:
            (cache_dir / f"{qid}.json").write_text(
                json.dumps({"sql_rows": [{"qid": qid, "arm": arm}]}),
                encoding="utf-8",
            )

    questions_payload = {
        "dataset_version": "2.0",
        "questions": [
            {"question_id": qid,
             "category": categories.get(qid, "simple_address"),
             "difficulty": "medium",
             "question": f"Question text for {qid}?",
             "expected_facts": [],
             "expected_constraints": [],
             "must_avoid": [],
             "notes": "",
             "created_at": "2026-05-15T00:00:00Z"}
            for qid in qids
        ],
    }
    questions_path.write_text(json.dumps(questions_payload), encoding="utf-8")
    return run_dir, questions_path


def _stub_input(monkeypatch, responses: list[str]) -> list[str]:
    """Replace ``label_outputs._ask`` with a queue-driven stub.

    Returns the list of prompts the labeller asked, so tests can assert on
    what was shown to the annotator.
    """
    iterator = iter(responses)
    asked: list[str] = []

    def fake_ask(prompt: str) -> str:
        asked.append(prompt)
        try:
            return next(iterator)
        except StopIteration as exc:  # pragma: no cover — surfaces test bug
            raise AssertionError(
                f"Labeller asked more questions than scripted; "
                f"last prompt was: {prompt!r}"
            ) from exc

    monkeypatch.setattr(lo, "_ask", fake_ask)
    return asked


def _full_rubric_responses(g=4, c=4, nf=4, cs=4, op=1,
                           tags="", notes="ok") -> list[str]:
    """Standard valid rubric: 4 axes + binary + tags + notes (7 inputs)."""
    return [str(g), str(c), str(nf), str(cs), str(op), tags, notes]


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------
def test_label_outputs_blinds_arm(tmp_path, capsys, monkeypatch):
    run_dir, questions_path = _build_run_dir(tmp_path)
    # 4 outputs (2 q × 2 arm) → 4 × 7 input prompts.
    _stub_input(monkeypatch, _full_rubric_responses() * 4)

    rc = lo.main([
        "--run-dir", str(run_dir),
        "--annotator-id", "tester",
        "--questions", str(questions_path),
    ])
    assert rc == 0

    captured = capsys.readouterr().out
    # The output blocks must NEVER expose the arm name.
    for arm in ("A1", "A2", "A3", "A4"):
        # `arm=` and bare `A1`/`A2` mentions (other than incidental like 'arms')
        # would leak the blind. We check the verbatim per-output block keys.
        assert f"arm={arm}" not in captured, f"arm leaked into prompt: {arm}"
        # The bracketed banner must use output_id, not arm.
        assert f"[{arm}]" not in captured, f"arm rendered in banner: [{arm}]"


def test_label_outputs_resume_skips_completed(tmp_path, monkeypatch):
    run_dir, questions_path = _build_run_dir(tmp_path)
    csv_path = run_dir / lo.LABELS_FILENAME

    # Pre-populate one row.
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    pre_oid = lo._output_id("Q001", "A1")
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(lo.LABEL_CSV_FIELDS))
        writer.writeheader()
        writer.writerow({
            "output_id": pre_oid, "annotator_id": "previous",
            "timestamp_utc": "2026-05-15T00:00:00Z",
            "question_id": "Q001", "arm": "A1",
            "groundedness": 4, "completeness": 4,
            "numeric_fidelity": 4, "constraint_satisfaction": 4,
            "overall_pass": 1, "failure_tags": "", "notes": "pre",
        })

    # Only 3 outputs left → 3 × 7 inputs.
    _stub_input(monkeypatch, _full_rubric_responses() * 3)

    rc = lo.main([
        "--run-dir", str(run_dir),
        "--annotator-id", "tester",
        "--questions", str(questions_path),
    ])
    assert rc == 0

    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    assert len(rows) == 4
    # Pre-existing row preserved verbatim.
    pre_row = next(r for r in rows if r["output_id"] == pre_oid)
    assert pre_row["annotator_id"] == "previous"
    assert pre_row["notes"] == "pre"


def test_label_outputs_validates_score_range(tmp_path, monkeypatch):
    run_dir, questions_path = _build_run_dir(tmp_path, qids=["Q001"], arms=["A1"])
    # First "5" is rejected (out of [0,4]); accept "3".
    responses = ["5", "3", "4", "4", "4", "1", "", "all good"]
    _stub_input(monkeypatch, responses)

    rc = lo.main([
        "--run-dir", str(run_dir),
        "--annotator-id", "t",
        "--questions", str(questions_path),
    ])
    assert rc == 0

    csv_path = run_dir / lo.LABELS_FILENAME
    row = next(csv.DictReader(csv_path.open(encoding="utf-8")))
    assert row["groundedness"] == "3"


def test_label_outputs_validates_failure_tags(tmp_path, monkeypatch):
    run_dir, questions_path = _build_run_dir(tmp_path, qids=["Q001"], arms=["A1"])
    # 4 axes + binary + invalid tags then valid + notes.
    responses = ["4", "4", "4", "4", "1",
                 "nonsense_tag,foo",     # rejected
                 "numeric_mismatch",     # accepted
                 "fine"]
    _stub_input(monkeypatch, responses)

    rc = lo.main([
        "--run-dir", str(run_dir),
        "--annotator-id", "t",
        "--questions", str(questions_path),
    ])
    assert rc == 0

    csv_path = run_dir / lo.LABELS_FILENAME
    row = next(csv.DictReader(csv_path.open(encoding="utf-8")))
    assert row["failure_tags"] == "numeric_mismatch"


def test_label_outputs_writes_all_rubric_fields(tmp_path, monkeypatch):
    run_dir, questions_path = _build_run_dir(tmp_path, qids=["Q001"], arms=["A1"])
    _stub_input(monkeypatch, _full_rubric_responses(
        g=2, c=3, nf=4, cs=1, op=0,
        tags="partial_answer,unsupported_claim",
        notes="answer drops constraint",
    ))

    rc = lo.main([
        "--run-dir", str(run_dir),
        "--annotator-id", "t",
        "--questions", str(questions_path),
    ])
    assert rc == 0

    csv_path = run_dir / lo.LABELS_FILENAME
    row = next(csv.DictReader(csv_path.open(encoding="utf-8")))

    # All 12 columns populated.
    for col in lo.LABEL_CSV_FIELDS:
        assert col in row
    assert row["groundedness"] == "2"
    assert row["completeness"] == "3"
    assert row["numeric_fidelity"] == "4"
    assert row["constraint_satisfaction"] == "1"
    assert row["overall_pass"] == "0"
    assert row["failure_tags"] == "partial_answer,unsupported_claim"
    assert row["notes"] == "answer drops constraint"
    assert row["question_id"] == "Q001"
    assert row["arm"] == "A1"
    assert row["annotator_id"] == "t"
    assert row["output_id"] == lo._output_id("Q001", "A1")


def test_label_outputs_skips_error_rows(tmp_path, monkeypatch):
    run_dir, questions_path = _build_run_dir(
        tmp_path, qids=["Q001", "Q002"], arms=["A1"], inject_error_qid="Q002",
    )
    # Only Q001 is labellable → 1 × 7 inputs.
    _stub_input(monkeypatch, _full_rubric_responses())

    rc = lo.main([
        "--run-dir", str(run_dir),
        "--annotator-id", "t",
        "--questions", str(questions_path),
    ])
    assert rc == 0

    csv_path = run_dir / lo.LABELS_FILENAME
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["question_id"] == "Q001"


def test_label_outputs_limit_is_per_session(tmp_path, monkeypatch):
    """--limit N caps THIS session at N new rows, not the global total."""
    run_dir, questions_path = _build_run_dir(
        tmp_path,
        qids=["Q001", "Q002", "Q003"],
        arms=["A1"],   # 3 outputs total.
    )
    # Pre-populate 1 row → 2 pending → --limit 2 should label exactly 2 more.
    csv_path = run_dir / lo.LABELS_FILENAME
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(lo.LABEL_CSV_FIELDS))
        writer.writeheader()
        writer.writerow({
            "output_id": lo._output_id("Q001", "A1"),
            "annotator_id": "previous",
            "timestamp_utc": "2026-05-15T00:00:00Z",
            "question_id": "Q001", "arm": "A1",
            "groundedness": 4, "completeness": 4,
            "numeric_fidelity": 4, "constraint_satisfaction": 4,
            "overall_pass": 1, "failure_tags": "", "notes": "",
        })

    _stub_input(monkeypatch, _full_rubric_responses() * 2)

    rc = lo.main([
        "--run-dir", str(run_dir),
        "--annotator-id", "tester",
        "--questions", str(questions_path),
        "--limit", "2",
    ])
    assert rc == 0
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    assert len(rows) == 3, f"expected 3 (1 pre + 2 new), got {len(rows)}"


def test_label_outputs_relabel_mode_stratified(tmp_path, monkeypatch):
    """Pre-label 8 outputs across 4 categories; relabel-subset 4 must spread
    selection across at least 4 distinct categories (round-robin)."""
    qids = [f"Q00{i}" for i in range(1, 9)]   # Q001..Q008
    cats = ["simple_address", "numeric_aggregation",
            "constraint_filtering", "vector_sql_hybrid"]
    categories = {qid: cats[i % len(cats)] for i, qid in enumerate(qids)}
    run_dir, questions_path = _build_run_dir(
        tmp_path, qids=qids, arms=["A1"], categories=categories,
    )

    # Pre-populate primary labels (one per output_id).
    csv_path = run_dir / lo.LABELS_FILENAME
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(lo.LABEL_CSV_FIELDS))
        writer.writeheader()
        for qid in qids:
            writer.writerow({
                "output_id": lo._output_id(qid, "A1"),
                "annotator_id": "primary",
                "timestamp_utc": "2026-05-15T00:00:00Z",
                "question_id": qid, "arm": "A1",
                "groundedness": 3, "completeness": 3,
                "numeric_fidelity": 3, "constraint_satisfaction": 3,
                "overall_pass": 1, "failure_tags": "", "notes": "",
            })

    # 4 relabels × 7 inputs each.
    _stub_input(monkeypatch, _full_rubric_responses() * 4)

    rc = lo.main([
        "--run-dir", str(run_dir),
        "--annotator-id", "primary",
        "--questions", str(questions_path),
        "--mode", "relabel",
        "--relabel-subset", "4",
    ])
    assert rc == 0

    relabels_path = run_dir / lo.RELABELS_FILENAME
    relabels = list(csv.DictReader(relabels_path.open(encoding="utf-8")))
    assert len(relabels) == 4
    # Stratification: 4 picks should land on 4 distinct categories
    # (round-robin starts at index 0 of each category).
    distinct_cats = set()
    qid_to_cat = categories
    for r in relabels:
        distinct_cats.add(qid_to_cat[r["question_id"]])
    assert len(distinct_cats) == 4, (
        f"expected 4 distinct categories, got {distinct_cats}"
    )
    # All rows tagged with relabel_round=2.
    assert all(r["relabel_round"] == "2" for r in relabels)


def test_label_outputs_relabel_mode_does_not_show_original(tmp_path, monkeypatch, capsys):
    run_dir, questions_path = _build_run_dir(
        tmp_path, qids=["Q001"], arms=["A1"],
    )
    csv_path = run_dir / lo.LABELS_FILENAME
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(lo.LABEL_CSV_FIELDS))
        writer.writeheader()
        writer.writerow({
            "output_id": lo._output_id("Q001", "A1"),
            "annotator_id": "primary",
            "timestamp_utc": "2026-05-15T00:00:00Z",
            "question_id": "Q001", "arm": "A1",
            "groundedness": 2, "completeness": 2,
            "numeric_fidelity": 2, "constraint_satisfaction": 2,
            "overall_pass": 0,
            "failure_tags": "partial_answer",
            "notes": "DISTINCTIVE_NOTE_FROM_ORIGINAL",
        })

    _stub_input(monkeypatch, _full_rubric_responses(g=4, c=4, nf=4, cs=4))

    rc = lo.main([
        "--run-dir", str(run_dir),
        "--annotator-id", "primary",
        "--questions", str(questions_path),
        "--mode", "relabel",
        "--relabel-subset", "1",
    ])
    assert rc == 0

    out = capsys.readouterr().out
    # The original distinctive note must NOT appear in the prompt — that
    # would defeat the cooling-off check.
    assert "DISTINCTIVE_NOTE_FROM_ORIGINAL" not in out
    # The original "groundedness=2" string must not be visible either.
    assert "groundedness=2" not in out
