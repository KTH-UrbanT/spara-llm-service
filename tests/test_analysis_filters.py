"""Unit tests for analysis_filters (Section 0.10 of plan-eil-v1.md).

These cover the filtering rules that Task 8's analysis script will use to
compute pass rates, per-axis stats, and fail-open rates from trace records.
The tests pin the *thesis-relevant invariants*: a fail-open verdict must NEVER
silently inflate the headline pass rate.
"""
from src.evaluation.analysis_filters import (
    BYPASSED,
    EVALUATED,
    FAIL_OPEN_RATE_WARNING_THRESHOLD,
    FAILED_OPEN,
    fail_open_rate,
    fail_open_rate_exceeds_threshold,
    filter_for_per_axis,
    filter_genuine_evaluations,
    is_bypassed,
    is_fail_open,
    is_genuine_evaluation,
    pass_rate_evaluated,
    pass_rate_unfiltered,
)


def _record(**fields):
    """Build a minimal trace record for testing."""
    base = {"verdict": "pass", "evaluation_status": EVALUATED}
    base.update(fields)
    return base


class TestStatusPredicates:
    def test_default_status_is_evaluated(self):
        # Records missing the status field default to 'evaluated' (back-compat).
        r = {"verdict": "pass"}
        assert is_genuine_evaluation(r)
        assert not is_fail_open(r)
        assert not is_bypassed(r)

    def test_evaluated_status(self):
        r = _record(evaluation_status=EVALUATED)
        assert is_genuine_evaluation(r)
        assert not is_fail_open(r)
        assert not is_bypassed(r)

    def test_failed_open_status(self):
        r = _record(evaluation_status=FAILED_OPEN)
        assert not is_genuine_evaluation(r)
        assert is_fail_open(r)
        assert not is_bypassed(r)

    def test_bypassed_status(self):
        r = _record(evaluation_status=BYPASSED)
        assert not is_genuine_evaluation(r)
        assert not is_fail_open(r)
        assert is_bypassed(r)


class TestFilterGenuineEvaluations:
    def test_drops_bypassed_and_fail_open(self):
        records = [
            _record(verdict="pass", evaluation_status=EVALUATED),
            _record(verdict="pass", evaluation_status=FAILED_OPEN),
            _record(verdict="pass", evaluation_status=BYPASSED),
            _record(verdict="fail", evaluation_status=EVALUATED),
        ]
        out = filter_genuine_evaluations(records)
        assert len(out) == 2
        assert all(r["evaluation_status"] == EVALUATED for r in out)


class TestFilterForPerAxis:
    def test_drops_rows_with_fallbacks(self):
        records = [
            _record(eval_scores={"score_fallbacks_applied": []}),  # clean → kept
            _record(eval_scores={"score_fallbacks_applied": ["numeric_fidelity_score"]}),  # synth → drop
            _record(eval_scores={}),  # treat empty/missing as clean → kept
        ]
        out = filter_for_per_axis(records)
        assert len(out) == 2

    def test_drops_fail_open_even_with_clean_scores(self):
        """Both filters must apply: genuine evaluation AND no fallbacks."""
        records = [
            _record(eval_scores={"score_fallbacks_applied": []}, evaluation_status=EVALUATED),  # kept
            _record(eval_scores={"score_fallbacks_applied": []}, evaluation_status=FAILED_OPEN),  # drop
        ]
        out = filter_for_per_axis(records)
        assert len(out) == 1
        assert out[0]["evaluation_status"] == EVALUATED

    def test_handles_missing_eval_scores_field(self):
        # Bypassed (A1) rows have no eval_scores. Should be dropped silently.
        records = [_record(evaluation_status=BYPASSED)]  # no eval_scores
        out = filter_for_per_axis(records)
        assert out == []


class TestFailOpenRate:
    def test_zero_when_no_records(self):
        assert fail_open_rate([]) == 0.0

    def test_zero_when_all_evaluated(self):
        records = [_record(evaluation_status=EVALUATED) for _ in range(10)]
        assert fail_open_rate(records) == 0.0

    def test_correct_fraction(self):
        records = [_record(evaluation_status=EVALUATED) for _ in range(8)]
        records.extend(_record(evaluation_status=FAILED_OPEN) for _ in range(2))
        # 2 fail-open / 10 evaluator-running rows = 0.2
        assert fail_open_rate(records) == 0.2

    def test_excludes_bypassed_from_denominator(self):
        """Bypassed rows (A1) intentionally skip the evaluator; they should NOT
        affect the fail-open rate of arms that DID run the evaluator."""
        records = [
            _record(evaluation_status=BYPASSED),  # excluded
            _record(evaluation_status=BYPASSED),  # excluded
            _record(evaluation_status=EVALUATED),
            _record(evaluation_status=FAILED_OPEN),
        ]
        # Denom should be 2 (evaluated + failed_open), one of which is fail-open → 0.5
        assert fail_open_rate(records) == 0.5

    def test_threshold_yellow_flag(self):
        records = [_record(evaluation_status=EVALUATED) for _ in range(99)]
        records.append(_record(evaluation_status=FAILED_OPEN))
        # 1/100 = 1% — under the 2% threshold.
        assert not fail_open_rate_exceeds_threshold(records)

        records.append(_record(evaluation_status=FAILED_OPEN))
        records.append(_record(evaluation_status=FAILED_OPEN))
        # 3/102 = 2.94% — exceeds 2% threshold; yellow flag fires.
        assert fail_open_rate_exceeds_threshold(records)

    def test_default_threshold_is_two_percent(self):
        """Pin the threshold so a refactor doesn't silently weaken the warning."""
        assert FAIL_OPEN_RATE_WARNING_THRESHOLD == 0.02


class TestPassRateEvaluated:
    """Section 0.10 headline behavior: fail-open passes must NOT inflate pass rate."""

    def test_excludes_fail_open_passes(self):
        records = [
            _record(verdict="pass", evaluation_status=EVALUATED),  # genuine pass
            _record(verdict="pass", evaluation_status=EVALUATED),  # genuine pass
            _record(verdict="fail", evaluation_status=EVALUATED),  # genuine fail
            _record(verdict="pass", evaluation_status=FAILED_OPEN),  # synthetic pass — must be excluded
            _record(verdict="pass", evaluation_status=FAILED_OPEN),  # synthetic pass — must be excluded
        ]
        # Filtered: 2 pass / 1 fail among the 3 genuine rows → 2/3 ≈ 0.667
        assert abs(pass_rate_evaluated(records) - (2 / 3)) < 1e-9

    def test_unfiltered_inflates_when_fail_open_present(self):
        """The exact pathology Section 0.10 prevents."""
        records = [
            _record(verdict="pass", evaluation_status=EVALUATED),
            _record(verdict="pass", evaluation_status=EVALUATED),
            _record(verdict="fail", evaluation_status=EVALUATED),
            _record(verdict="pass", evaluation_status=FAILED_OPEN),
            _record(verdict="pass", evaluation_status=FAILED_OPEN),
        ]
        # Naïve unfiltered: 4 pass / 5 → 0.8 (inflated)
        # Filtered: 2/3 ≈ 0.667 (truth)
        unfilt = pass_rate_unfiltered(records)
        filt = pass_rate_evaluated(records)
        assert unfilt > filt, (
            "Unfiltered must be inflated relative to filtered when fail-open is present. "
            "If this fails, the headline pass rate is silently wrong."
        )

    def test_zero_when_no_genuine_rows(self):
        records = [_record(evaluation_status=BYPASSED), _record(evaluation_status=FAILED_OPEN)]
        assert pass_rate_evaluated(records) == 0.0
