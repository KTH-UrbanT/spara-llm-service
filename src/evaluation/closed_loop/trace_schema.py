"""Trace writers: one per-case row, one per-attempt row, one per-arm summary."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

# v4: final_answer and answer_drafted_this_attempt are stored untruncated (v3 capped them at
# 1000 and 500 chars). A consumer that infers "the stored text may be cut off" must gate on
# trace_schema_version < 4 — see scripts/rescore_deterministic.py.
TRACE_SCHEMA_VERSION = 4


def _now() -> str:
    """UTC timestamp stamped onto every record."""
    return datetime.now(timezone.utc).isoformat()


def _append(path: Path, record: dict) -> None:
    """Append one JSONL record, creating the run directory on first write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        # ensure_ascii=True escapes U+2028/U+2029 (which the LLM can emit) so a value
        # can never contain a line-break that splitlines() would split a record on.
        f.write(json.dumps({"timestamp_utc": _now(),
                            "trace_schema_version": TRACE_SCHEMA_VERSION, **record},
                           ensure_ascii=True, default=str) + "\n")


def append_per_case_trace(record: dict, run_dir: Path, arm: str) -> None:
    """One row per case: the final outcome the analysis scripts read."""
    _append(run_dir / arm / "traces" / "per_case.jsonl", record)


def append_per_attempt_trace(record: dict, run_dir: Path, arm: str) -> None:
    """One row per checkpoint firing: several per case when the loop rewinds."""
    _append(run_dir / arm / "traces" / "per_attempt.jsonl", record)


def write_arm_summary(record: dict, run_dir: Path, arm: str) -> None:
    """Overwrite the arm's roll-up totals once the run finishes.

    ensure_ascii=False and indent here, unlike `_append`: this file is read by humans
    and is a single JSON object, so the line-splitting hazard the JSONL writers guard
    against does not apply.
    """
    path = run_dir / arm / "traces" / "arm_summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"timestamp_utc": _now(),
                                "trace_schema_version": TRACE_SCHEMA_VERSION, **record},
                               ensure_ascii=False, indent=2, default=str))


def load_per_case_traces(run_dir: Path, arm: str) -> list[dict]:
    """Read back an arm's per-case rows; empty list if the arm never ran."""
    path = run_dir / arm / "traces" / "per_case.jsonl"
    if not path.exists():
        return []
    # split('\n') (not splitlines) so embedded U+2028/U+2029 can't split a record.
    return [json.loads(l) for l in path.read_text(encoding="utf-8").split("\n") if l.strip()]
