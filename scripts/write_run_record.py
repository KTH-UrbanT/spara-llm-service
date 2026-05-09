#!/usr/bin/env python3
"""Write the per-run-family record YAML at the end of a multi-arm run.

This helper is invoked by ``scripts/run_experiment.sh`` after every arm has
exited successfully. It does the bookkeeping that bash is bad at:

  - SHA-256 of the dataset file, both prompt files, and protocol.yaml.
  - ``git rev-parse HEAD`` for the commit the experiment ran from.
  - Per-arm row counts (one row per question per arm) read from
    ``<output-dir>/<arm>/results.jsonl``.
  - Per-arm fail-open counts read from
    ``<output-dir>/<arm>/traces/evaluation_traces.jsonl``.
  - The Azure deployment names actually used (from env vars).

The output is written to ``<output-dir>/run_record.yaml``.

Cross-reference:
  - plan-eil-v2.md §C.2 for the helper specification.
  - plan-eil-v2.md §C.5 for the run_record YAML schema.

Usage::

    python -m scripts.write_run_record \\
        --run-id 2026-05-15_v2 \\
        --output-dir artifacts/runs/2026-05-15_v2 \\
        --arms A1,A2 \\
        --protocol experiments/protocol.yaml \\
        --start-utc 2026-05-15T09:00:00Z \\
        --end-utc 2026-05-15T09:14:53Z

The script never raises; missing optional inputs are recorded as ``null``
in the YAML so a partial record is still readable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Write a run-record YAML for a completed multi-arm run.",
    )
    p.add_argument("--run-id", required=True,
                   help="Stable label for the run family, e.g. 2026-05-15_v2.")
    p.add_argument("--output-dir", required=True, type=Path,
                   help="Run-family output dir (parent of A1/, A2/, ...).")
    p.add_argument("--arms", required=True,
                   help="Comma-separated list of arms that were executed, e.g. A1,A2.")
    p.add_argument("--protocol", required=True, type=Path,
                   help="Path to experiments/protocol.yaml.")
    p.add_argument("--start-utc", required=True,
                   help="Run start time in ISO 8601 (UTC).")
    p.add_argument("--end-utc", required=True,
                   help="Run end time in ISO 8601 (UTC).")
    p.add_argument("--prompt-v1", type=Path,
                   default=Path("src/prompts/evaluator_prompt.txt"),
                   help="Path to the v1 evaluator prompt for SHA-256.")
    p.add_argument("--prompt-v2", type=Path,
                   default=Path("src/prompts/evaluator_prompt_v2.txt"),
                   help="Path to the v2 evaluator prompt for SHA-256.")
    p.add_argument("--out", type=Path, default=None,
                   help="Override the output path. Defaults to "
                        "<output-dir>/run_record.yaml.")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# SHA-256 / git helpers
# ---------------------------------------------------------------------------
def sha256_file(path: Path) -> Optional[str]:
    """Return hex SHA-256 of ``path``, or ``None`` if the file is missing.

    Reads in 64-KiB chunks so very large datasets don't load into memory.
    """
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit_at(cwd: Optional[Path] = None) -> Optional[str]:
    """Resolve ``git rev-parse HEAD`` for ``cwd``. Returns ``None`` on any
    failure (no git, detached non-repo, etc.) so the run record is still
    written; the absence of a commit is reported, not crashed on."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd) if cwd else None,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    return out.decode("utf-8").strip() or None


# ---------------------------------------------------------------------------
# Counting helpers (per-arm)
# ---------------------------------------------------------------------------
def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    """Yield decoded rows from a JSONL file. Skip blank/malformed lines."""
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def count_questions_processed(arm_dir: Path) -> int:
    """Count rows in ``<arm_dir>/results.jsonl`` (one per question per arm)."""
    return sum(1 for _ in _iter_jsonl(arm_dir / "results.jsonl"))


def count_fail_open(arm_dir: Path) -> int:
    """Count trace records with ``evaluation_status == 'failed_open'``."""
    traces_path = arm_dir / "traces" / "evaluation_traces.jsonl"
    return sum(
        1 for rec in _iter_jsonl(traces_path)
        if rec.get("evaluation_status") == "failed_open"
    )


# ---------------------------------------------------------------------------
# YAML serialiser (vendored — avoids requiring PyYAML for this helper)
# ---------------------------------------------------------------------------
def _yaml_scalar(value: Any) -> str:
    """Render a scalar in single-quoted YAML, choosing null/true/false/numbers
    when appropriate. Strings are always quoted to keep the output unambiguous
    even when values look numeric (e.g. SHA hashes)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    s = str(value)
    return "'" + s.replace("'", "''") + "'"


def _emit_yaml(data: Dict[str, Any]) -> str:
    """Tiny dict-of-(scalars|list-of-scalars|dict-of-scalars) YAML emitter.

    The run-record schema is shallow on purpose: top-level scalars, plus
    one list (``arms_executed``) and two single-level dicts
    (``total_questions_processed_per_arm``, ``fail_open_count_per_arm``).
    """
    lines: List[str] = []
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_yaml_scalar(item)}")
        elif isinstance(value, dict):
            lines.append(f"{key}:")
            for k, v in value.items():
                lines.append(f"  {k}: {_yaml_scalar(v)}")
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def build_record(args: argparse.Namespace,
                 dataset_path: Optional[Path] = None) -> Dict[str, Any]:
    """Assemble the run-record dict from the CLI args and the live filesystem.

    ``dataset_path`` is read from the protocol file when not explicitly passed.
    Surfacing it as a parameter is useful for tests.
    """
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    output_dir = Path(args.output_dir)

    # Resolve dataset path from protocol.yaml. If protocol is unreadable we
    # still want to produce a record — set null placeholders.
    if dataset_path is None:
        dataset_path = _dataset_path_from_protocol(args.protocol)

    record: Dict[str, Any] = {
        "run_family_id": args.run_id,
        "git_commit_at_run_start": git_commit_at(),
        "protocol_sha256": sha256_file(args.protocol),
        "dataset_sha256": sha256_file(dataset_path) if dataset_path else None,
        "prompt_v1_sha256": sha256_file(args.prompt_v1),
        "prompt_v2_sha256": sha256_file(args.prompt_v2),
        "start_time_utc": args.start_utc,
        "end_time_utc": args.end_utc,
        "azure_api_version": os.environ.get("OPENAI_RESPONSE_MODEL_API_VERSION"),
        "summarizer_deployment": os.environ.get(
            "OPENAI_RESPONSE_MODEL_DEPLOYMENT_NAME",
        ),
        "evaluator_deployment": os.environ.get(
            "EVALUATOR_MODEL_DEPLOYMENT_NAME",
            os.environ.get("OPENAI_RESPONSE_MODEL_DEPLOYMENT_NAME"),
        ),
        "arms_executed": arms,
        "total_questions_processed_per_arm": {
            arm: count_questions_processed(output_dir / arm) for arm in arms
        },
        "fail_open_count_per_arm": {
            arm: count_fail_open(output_dir / arm) for arm in arms
        },
    }
    return record


def _dataset_path_from_protocol(protocol: Path) -> Optional[Path]:
    """Pull ``dataset.path`` out of the protocol YAML.

    We use a deliberately small parser (line scan) so the helper has no hard
    dependency on PyYAML — pip-installing it for a single string lookup
    inflates the dependency graph for no benefit.
    """
    if not protocol.exists():
        return None
    in_dataset_block = False
    for raw in protocol.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "dataset:":
            in_dataset_block = True
            continue
        if in_dataset_block:
            if not raw.startswith(" ") and not raw.startswith("\t"):
                # Block ended.
                break
            if stripped.startswith("path:"):
                value = stripped.split(":", 1)[1].strip()
                # Strip surrounding quotes if any.
                if value and value[0] in ("'", '"') and value[-1] == value[0]:
                    value = value[1:-1]
                return Path(value) if value else None
    return None


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    record = build_record(args)
    out_path = args.out or (Path(args.output_dir) / "run_record.yaml")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_emit_yaml(record), encoding="utf-8")
    print(f"Wrote run record: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
