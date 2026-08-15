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
    return datetime.now(timezone.utc).isoformat()


def _append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        # ensure_ascii=True escapes U+2028/U+2029 (which the LLM can emit) so a value
        # can never contain a line-break that splitlines() would split a record on.
        f.write(json.dumps({"timestamp_utc": _now(),
                            "trace_schema_version": TRACE_SCHEMA_VERSION, **record},
                           ensure_ascii=True, default=str) + "\n")


def append_per_case_trace(record: dict, run_dir: Path, arm: str) -> None:
    _append(run_dir / arm / "traces" / "per_case.jsonl", record)


def append_per_attempt_trace(record: dict, run_dir: Path, arm: str) -> None:
    _append(run_dir / arm / "traces" / "per_attempt.jsonl", record)


def write_arm_summary(record: dict, run_dir: Path, arm: str) -> None:
    path = run_dir / arm / "traces" / "arm_summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"timestamp_utc": _now(),
                                "trace_schema_version": TRACE_SCHEMA_VERSION, **record},
                               ensure_ascii=False, indent=2, default=str))


def load_per_case_traces(run_dir: Path, arm: str) -> list[dict]:
    path = run_dir / arm / "traces" / "per_case.jsonl"
    if not path.exists():
        return []
    # split('\n') (not splitlines) so embedded U+2028/U+2029 can't split a record.
    return [json.loads(l) for l in path.read_text(encoding="utf-8").split("\n") if l.strip()]
