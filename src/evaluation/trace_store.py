from __future__ import annotations

import csv
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable


_TRACE_LOCK = threading.Lock()
_DEFAULT_TRACE_DIR = Path(__file__).resolve().parents[2] / "artifacts" / "evaluation_traces"


def get_trace_dir() -> Path:
    """Return the directory used for evaluation trace artifacts."""
    configured = os.getenv("EVALUATION_TRACE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return _DEFAULT_TRACE_DIR


def get_trace_paths() -> Dict[str, Path]:
    """Return the canonical JSONL and CSV output paths."""
    trace_dir = get_trace_dir()
    return {
        "directory": trace_dir,
        "jsonl": trace_dir / "evaluation_traces.jsonl",
        "csv": trace_dir / "evaluation_traces.csv",
    }


TRACE_SCHEMA_VERSION = 2
"""Schema version for evaluation trace records.

Version 2 adds experiment identifier fields — question_id, run_id, arm,
attempt_index, dataset_version — that are required to join records across
experimental arms in statistical analysis.
"""


_DEFAULT_TRUNCATION_LIMIT = 4000
_TRUNCATION_MARKER = "…[truncated]"


def truncate_for_trace(value: Any, max_length: int = _DEFAULT_TRUNCATION_LIMIT) -> Any:
    """Recursively truncate string leaves so the trace JSONL stays manageable.

    `aggregated_data` can contain large vector-store snippets (10–50 KB each).
    Without truncation a single trace line can exceed 100 KB and choke downstream
    tools that read the JSONL.

    Behavior:
      - dict / list: recurse, preserving structure.
      - str: truncate to `max_length` chars and append _TRUNCATION_MARKER.
      - int / float / bool / None: returned unchanged.
      - other types: coerced to str via repr() then truncated.
    The original input is *not* mutated.
    """
    if isinstance(value, str):
        if len(value) > max_length:
            return value[:max_length] + _TRUNCATION_MARKER
        return value
    if isinstance(value, dict):
        return {k: truncate_for_trace(v, max_length) for k, v in value.items()}
    if isinstance(value, list):
        return [truncate_for_trace(v, max_length) for v in value]
    if isinstance(value, tuple):
        return [truncate_for_trace(v, max_length) for v in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    # Fallback for non-JSON-serializable types (MagicMock in tests, etc.).
    return truncate_for_trace(repr(value), max_length)


def build_evaluation_trace(**fields: Any) -> Dict[str, Any]:
    """Build a timestamped trace record for a single evaluator run."""
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "trace_schema_version": TRACE_SCHEMA_VERSION,
    }
    record.update(fields)
    return record


def _stringify_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _flatten_record(record: Dict[str, Any], parent_key: str = "", separator: str = ".") -> Dict[str, Any]:
    flattened: Dict[str, Any] = {}
    for key, value in record.items():
        new_key = f"{parent_key}{separator}{key}" if parent_key else str(key)
        if isinstance(value, dict):
            flattened.update(_flatten_record(value, new_key, separator=separator))
        elif isinstance(value, list):
            flattened[new_key] = json.dumps(value, ensure_ascii=False)
        else:
            flattened[new_key] = _stringify_value(value)
    return flattened


def _load_jsonl_records(jsonl_path: Path) -> list[Dict[str, Any]]:
    records: list[Dict[str, Any]] = []
    if not jsonl_path.exists():
        return records

    with jsonl_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
    return records


def _write_csv_summary(csv_path: Path, records: Iterable[Dict[str, Any]]) -> None:
    rows = [_flatten_record(record) for record in records]
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def append_evaluation_trace(record: Dict[str, Any]) -> Dict[str, Path]:
    """Append one trace record and refresh the CSV summary.

    The writer is intentionally best-effort so that trace logging never blocks the
    evaluator path.
    """
    paths = get_trace_paths()
    paths["directory"].mkdir(parents=True, exist_ok=True)

    with _TRACE_LOCK:
        with paths["jsonl"].open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

        records = _load_jsonl_records(paths["jsonl"])
        _write_csv_summary(paths["csv"], records)

    return paths
