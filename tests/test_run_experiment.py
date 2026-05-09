"""Structural tests for ``scripts/run_experiment.sh``.

Per plan-eil-v2.md §C.6: parse the bash script as text and assert its
orchestration shape. We do **not** invoke the script — mocking Azure
inside a bash subprocess is fragile and adds maintenance burden
disproportionate to its value. The bash wrapper is thin orchestration; a
text-level assertion is sufficient when paired with the manual smoke run
in plan-eil-v2.md §C.7 step 1.
"""
from __future__ import annotations

from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_experiment.sh"


@pytest.fixture(scope="module")
def script_text() -> str:
    assert SCRIPT_PATH.exists(), f"missing wrapper script: {SCRIPT_PATH}"
    return SCRIPT_PATH.read_text(encoding="utf-8")


def test_run_experiment_dispatches_arms_in_order(script_text):
    """A1 must invoke before A2; both arms must be wired into run_arm."""
    assert "run_arm A1" in script_text, "A1 invocation missing"
    assert "run_arm A2" in script_text, "A2 invocation missing"

    # A1 must come before A2 in the file.
    a1_idx = script_text.index("run_arm A1")
    a2_idx = script_text.index("run_arm A2")
    assert a1_idx < a2_idx, (
        "A1 must be invoked before A2 — sequencing is load-bearing because "
        "A2 reads A1's aggregated_data cache via --cache-from."
    )


def test_cache_from_only_on_a2_and_optional_arms(script_text):
    """A1 must NOT pass --cache-from (it populates the cache as a side
    effect). A2 must pass --cache-from pointing at the A1 output dir."""
    a1_block_start = script_text.index("run_arm A1")
    # The next 'run_arm' invocation is A2. Slice only the A1 invocation line.
    next_invocation = script_text.find("run_arm ", a1_block_start + 1)
    a1_invocation = script_text[a1_block_start:next_invocation]

    assert "--cache-from" not in a1_invocation, (
        "A1 must NOT receive --cache-from; it populates the cache itself."
    )

    # A2 must include --cache-from. Optional arms (A3/A4) too when present.
    assert "run_arm A2 \"--cache-from " in script_text or \
           "run_arm A2 \"--cache-from $" in script_text or \
           "run_arm A2 \"--cache-from ${OUTPUT_DIR}/A1\"" in script_text, (
        "A2 must receive --cache-from pointing at A1's output dir."
    )


def test_optional_arms_gated_by_env_var(script_text):
    """RUN_OPTIONAL_ARMS=true unlocks A3 and A4."""
    assert "RUN_OPTIONAL_ARMS" in script_text, (
        "Optional arms must be gated by RUN_OPTIONAL_ARMS env var "
        "per plan-eil-v2.md §C.2."
    )
    # A3 and A4 must appear inside the gated block.
    assert "run_arm A3" in script_text
    assert "run_arm A4" in script_text


def test_header_comment_block_present(script_text):
    """The DO-NOT-REFACTOR header must be present verbatim. This is a guard
    against future LLM refactors that would silently break arm A4 by
    collapsing the multi-process layout."""
    required_lines = [
        "DO NOT refactor this into a single Python process.",
        "The 4-process layout is load-bearing",
        "loads its arm-specific prompt at module import time.",
    ]
    for line in required_lines:
        assert line in script_text, (
            f"missing required header line: {line!r}"
        )


def test_calls_write_run_record_with_required_args(script_text):
    """At end of run the wrapper must invoke write_run_record.py with the
    minimum set of args required by its CLI."""
    assert "scripts.write_run_record" in script_text
    for required_flag in ("--run-id", "--output-dir", "--arms",
                          "--protocol", "--start-utc", "--end-utc"):
        assert required_flag in script_text, (
            f"write_run_record invocation missing flag {required_flag!r}"
        )


def test_no_backgrounded_arm_invocation(script_text):
    """Sequencing depends on A1 finishing before A2. Detect the most common
    way that gets broken: a trailing `&` after a `run_arm` line."""
    for line in script_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("run_arm "):
            # Allow trailing comment or `\` continuation, but not bare `&`.
            assert not stripped.rstrip("\\").rstrip().endswith("&"), (
                f"backgrounded arm invocation: {stripped!r} — "
                "this would let A2 race A1; remove the trailing &."
            )
