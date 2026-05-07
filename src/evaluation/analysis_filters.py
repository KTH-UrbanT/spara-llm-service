"""Analysis-side filters for evaluation traces.

When the evaluator crashes (a "fail-open" event), it returns verdict="pass" so
the user-facing path is never blocked. But a naïve `pass_rate = (verdict ==
"pass").mean()` over traces would silently count fail-open verdicts as real
passes, inflating pass rates by the crash rate.

This module defines the canonical filtering rules that `analyze_results.py`
must apply. Codifying them here (with tests) means a future analyst gets the
right behavior by importing instead of re-deriving the logic.

Trace `evaluation_status` value semantics:
  - "evaluated"   → evaluator successfully judged the answer; verdict is meaningful.
  - "failed_open" → evaluator crashed; verdict was synthetically "pass" to avoid
                    blocking the user. EXCLUDE from pass-rate aggregations.
  - "bypassed"    → the evaluator never ran (mode=off). Synthetic trace records
                    for bypassed arms carry this status. They have no eval_scores,
                    so they are excluded from per-axis aggregations but are still
                    part of overall arm comparisons via human labels.

Rule of thumb for the analysis script:
  - Per-arm `pass_rate_evaluated`: only rows with status == "evaluated".
  - Per-arm `pass_rate_unfiltered`: all rows (reported in footnote).
  - Per-axis stats: only rows with status == "evaluated" AND no fallback
    contamination (see `score_fallbacks_applied` in eval_scores).
  - `fail_open_rate`: fraction of evaluator-running rows with status == "failed_open".
    A value above 2 % is a yellow flag that warrants investigation before publishing.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

# Public API
EVALUATED = "evaluated"
FAILED_OPEN = "failed_open"
BYPASSED = "bypassed"

# Configurable warning threshold for fail-open rate.
# A run with fail_open_rate above this threshold should not be published without
# investigation: inspect evaluator_failure_reason patterns to determine whether
# the root cause is a JSON parse failure, timeout, or rate-limit.
FAIL_OPEN_RATE_WARNING_THRESHOLD = 0.02


def evaluation_status(record: Dict[str, Any]) -> str:
    """Read the evaluation_status field, defaulting to 'evaluated' when absent.

    The default exists for backward compatibility with traces written before
    the evaluation_status field was introduced (schema version 1). Older records
    were only ever written by a successful evaluator run, so treating them as
    'evaluated' is correct.
    """
    return str(record.get("evaluation_status") or EVALUATED)


def is_genuine_evaluation(record: Dict[str, Any]) -> bool:
    """True iff the evaluator actually ran and produced a verdict.

    Rows that pass this filter are the ones whose verdict + scores can enter
    pass-rate and per-axis aggregations.
    """
    return evaluation_status(record) == EVALUATED


def is_fail_open(record: Dict[str, Any]) -> bool:
    """True iff this trace records a fail-open event (evaluator crashed → synthetic pass)."""
    return evaluation_status(record) == FAILED_OPEN


def is_bypassed(record: Dict[str, Any]) -> bool:
    """True iff the evaluator was never run for this row (arm A1 / mode=off)."""
    return evaluation_status(record) == BYPASSED


def filter_genuine_evaluations(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return only the records where the evaluator successfully ran.

    Use this for `pass_rate_evaluated` and any per-axis aggregation. Bypassed
    rows (A1) and fail-open rows are dropped.
    """
    return [r for r in records if is_genuine_evaluation(r)]


def filter_for_per_axis(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return rows safe for per-axis statistical comparisons.

    Rows where the LLM omitted a scoring dimension (causing the node to synthesize
    it from another axis) must be excluded from per-axis breakdowns — the score
    is not independent and would contaminate per-axis statistical comparisons.
    This filter combines that fallback check with the genuine-evaluation rule.
    """
    out: List[Dict[str, Any]] = []
    for r in records:
        if not is_genuine_evaluation(r):
            continue
        scores = r.get("eval_scores") or {}
        if not isinstance(scores, dict):
            continue
        fallbacks = scores.get("score_fallbacks_applied") or []
        if isinstance(fallbacks, list) and len(fallbacks) > 0:
            continue
        out.append(r)
    return out


def fail_open_rate(records: Iterable[Dict[str, Any]]) -> float:
    """Fraction of evaluator-running rows that fail-open'd.

    Bypassed rows (A1) are excluded from the denominator because they intentionally
    skip the evaluator and a "fail-open" concept does not apply. If no
    evaluator-running rows exist, returns 0.0.
    """
    rows = [r for r in records if not is_bypassed(r)]
    if not rows:
        return 0.0
    failed = sum(1 for r in rows if is_fail_open(r))
    return failed / len(rows)


def fail_open_rate_exceeds_threshold(records: Iterable[Dict[str, Any]]) -> bool:
    """Return True if the fail-open rate is above the warning threshold.

    A fail-open rate above 2 % means the evaluator crashed more often than
    expected, and any pass-rate numbers derived from that run are suspect —
    each crash silently added a synthetic "pass" verdict. Investigate the
    `evaluator_failure_reason` field in the traces before publishing results.
    """
    return fail_open_rate(records) > FAIL_OPEN_RATE_WARNING_THRESHOLD


def pass_rate_evaluated(records: Iterable[Dict[str, Any]]) -> float:
    """Pass rate restricted to rows where the evaluator actually ran successfully.

    A pass is `verdict == "pass"`. Fail-open verdicts are excluded by construction
    because their status is `"failed_open"`, not `"evaluated"`.
    """
    rows = filter_genuine_evaluations(records)
    if not rows:
        return 0.0
    passes = sum(1 for r in rows if str(r.get("verdict") or "").lower() == "pass")
    return passes / len(rows)


def pass_rate_unfiltered(records: Iterable[Dict[str, Any]]) -> float:
    """Pass rate over ALL rows (footnote-only — fail-open verdicts inflate this)."""
    rows = list(records)
    if not rows:
        return 0.0
    passes = sum(1 for r in rows if str(r.get("verdict") or "").lower() == "pass")
    return passes / len(rows)
