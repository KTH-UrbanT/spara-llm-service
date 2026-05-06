"""Evaluation tracing utilities for thesis-grade experiments."""

from .analysis_filters import (
    EVALUATED,
    FAIL_OPEN_RATE_WARNING_THRESHOLD,
    FAILED_OPEN,
    BYPASSED,
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
from .trace_store import (
    append_evaluation_trace,
    build_evaluation_trace,
    get_trace_paths,
    truncate_for_trace,
)
