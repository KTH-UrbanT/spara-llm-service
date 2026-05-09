"""Tests for ``scripts/write_run_record.py``.

Per plan-eil-v2.md §C.2: a single test that drives the helper against a
synthetic run dir + protocol file and asserts the output YAML has all the
keys the analysis pipeline expects, with non-empty hex SHA-256 values for
the file-pinning fields.

The test never reaches the network or the real filesystem outside
``tmp_path``. ``OPENAI_RESPONSE_*`` env vars are forced to known values via
``monkeypatch`` so the assertions are deterministic.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts import write_run_record as wrr


HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _seed_run_dir(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Build a minimal but valid run-family directory + supporting files.

    Layout:
        tmp_path/
            protocol.yaml
            evaluator_prompt.txt
            evaluator_prompt_v2.txt
            data/questions.json
            runs/<run-id>/
                A1/results.jsonl   (1 row)
                A1/traces/evaluation_traces.jsonl  (1 bypassed row)
                A2/results.jsonl   (2 rows)
                A2/traces/evaluation_traces.jsonl  (2 rows: 1 evaluated, 1 failed_open)
    """
    protocol_path = tmp_path / "protocol.yaml"
    dataset_path = tmp_path / "data" / "questions.json"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_v1_path = tmp_path / "evaluator_prompt.txt"
    prompt_v2_path = tmp_path / "evaluator_prompt_v2.txt"

    protocol_path.write_text(
        "plan_version: 'v2'\n"
        "dataset:\n"
        f"  path: '{dataset_path}'\n"
        "  expected_count: 20\n",
        encoding="utf-8",
    )
    dataset_path.write_text(json.dumps({"dataset_version": "2.0", "questions": []}))
    prompt_v1_path.write_text("v1 prompt body")
    prompt_v2_path.write_text("v2 prompt body")

    output_dir = tmp_path / "runs" / "2026-05-15_v2"
    _write_jsonl(output_dir / "A1" / "results.jsonl", [{"question_id": "Q001"}])
    _write_jsonl(
        output_dir / "A1" / "traces" / "evaluation_traces.jsonl",
        [{"question_id": "Q001", "evaluation_status": "bypassed"}],
    )
    _write_jsonl(
        output_dir / "A2" / "results.jsonl",
        [{"question_id": "Q001"}, {"question_id": "Q002"}],
    )
    _write_jsonl(
        output_dir / "A2" / "traces" / "evaluation_traces.jsonl",
        [
            {"question_id": "Q001", "evaluation_status": "evaluated"},
            {"question_id": "Q002", "evaluation_status": "failed_open"},
        ],
    )
    return protocol_path, output_dir, prompt_v1_path, prompt_v2_path


def test_write_run_record_yaml_has_required_fields(tmp_path, monkeypatch):
    """End-to-end smoke: synthetic run -> YAML with all expected keys + hex SHAs."""
    protocol_path, output_dir, prompt_v1_path, prompt_v2_path = _seed_run_dir(tmp_path)

    # Deterministic env vars so deployment fields are predictable.
    monkeypatch.setenv("OPENAI_RESPONSE_MODEL_API_VERSION", "2024-02-01")
    monkeypatch.setenv("OPENAI_RESPONSE_MODEL_DEPLOYMENT_NAME", "gpt-4o-test")
    monkeypatch.delenv("EVALUATOR_MODEL_DEPLOYMENT_NAME", raising=False)

    out_path = output_dir / "run_record.yaml"
    rc = wrr.main([
        "--run-id", "2026-05-15_v2",
        "--output-dir", str(output_dir),
        "--arms", "A1,A2",
        "--protocol", str(protocol_path),
        "--start-utc", "2026-05-15T09:00:00Z",
        "--end-utc", "2026-05-15T09:14:53Z",
        "--prompt-v1", str(prompt_v1_path),
        "--prompt-v2", str(prompt_v2_path),
        "--out", str(out_path),
    ])
    assert rc == 0
    assert out_path.exists()

    body = out_path.read_text(encoding="utf-8")

    # Required scalar keys.
    for key in (
        "run_family_id",
        "protocol_sha256",
        "dataset_sha256",
        "prompt_v1_sha256",
        "prompt_v2_sha256",
        "start_time_utc",
        "end_time_utc",
        "azure_api_version",
        "summarizer_deployment",
        "evaluator_deployment",
        "arms_executed",
        "total_questions_processed_per_arm",
        "fail_open_count_per_arm",
    ):
        assert key in body, f"missing key {key!r} in run_record.yaml:\n{body}"

    # SHA-256 fields must be non-empty hex strings.
    record = wrr.build_record(
        wrr.parse_args([
            "--run-id", "2026-05-15_v2",
            "--output-dir", str(output_dir),
            "--arms", "A1,A2",
            "--protocol", str(protocol_path),
            "--start-utc", "2026-05-15T09:00:00Z",
            "--end-utc", "2026-05-15T09:14:53Z",
            "--prompt-v1", str(prompt_v1_path),
            "--prompt-v2", str(prompt_v2_path),
        ])
    )
    for sha_field in ("protocol_sha256", "dataset_sha256",
                      "prompt_v1_sha256", "prompt_v2_sha256"):
        value = record[sha_field]
        assert value is not None, f"{sha_field} is None"
        assert HEX_RE.match(value), f"{sha_field}={value!r} is not 64-char hex"

    # Per-arm counts derived from the synthetic JSONL files.
    assert record["arms_executed"] == ["A1", "A2"]
    assert record["total_questions_processed_per_arm"] == {"A1": 1, "A2": 2}
    assert record["fail_open_count_per_arm"] == {"A1": 0, "A2": 1}
    assert record["azure_api_version"] == "2024-02-01"
    assert record["summarizer_deployment"] == "gpt-4o-test"
    # No EVALUATOR_MODEL_DEPLOYMENT_NAME → falls back to summariser deployment.
    assert record["evaluator_deployment"] == "gpt-4o-test"


def test_dataset_path_from_protocol_quoted_and_unquoted(tmp_path):
    """The hand-rolled YAML scan accepts both single-quoted and bare paths."""
    p_quoted = tmp_path / "protocol_q.yaml"
    p_quoted.write_text("dataset:\n  path: 'src/config/questions.json'\n")
    p_bare = tmp_path / "protocol_b.yaml"
    p_bare.write_text("dataset:\n  path: src/config/questions.json\n")

    assert wrr._dataset_path_from_protocol(p_quoted) == Path("src/config/questions.json")
    assert wrr._dataset_path_from_protocol(p_bare) == Path("src/config/questions.json")


def test_missing_protocol_does_not_crash(tmp_path, monkeypatch):
    """A missing protocol file must yield ``None`` SHAs but a valid YAML."""
    output_dir = tmp_path / "runs" / "missing_protocol"
    _write_jsonl(output_dir / "A1" / "results.jsonl", [{"question_id": "Q001"}])
    _write_jsonl(output_dir / "A1" / "traces" / "evaluation_traces.jsonl", [])

    monkeypatch.delenv("OPENAI_RESPONSE_MODEL_API_VERSION", raising=False)
    monkeypatch.delenv("OPENAI_RESPONSE_MODEL_DEPLOYMENT_NAME", raising=False)
    monkeypatch.delenv("EVALUATOR_MODEL_DEPLOYMENT_NAME", raising=False)

    rc = wrr.main([
        "--run-id", "ghost_run",
        "--output-dir", str(output_dir),
        "--arms", "A1",
        "--protocol", str(tmp_path / "does_not_exist.yaml"),
        "--start-utc", "2026-05-15T09:00:00Z",
        "--end-utc", "2026-05-15T09:01:00Z",
        "--prompt-v1", str(tmp_path / "no_v1.txt"),
        "--prompt-v2", str(tmp_path / "no_v2.txt"),
    ])
    assert rc == 0
    body = (output_dir / "run_record.yaml").read_text(encoding="utf-8")
    # All file-SHA fields recorded null.
    assert "protocol_sha256: null" in body
    assert "dataset_sha256: null" in body
    assert "prompt_v1_sha256: null" in body
    assert "prompt_v2_sha256: null" in body
    # Counts still computed.
    assert "A1: 1" in body
