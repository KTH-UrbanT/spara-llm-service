"""Closed-loop evaluator package."""
from src.evaluation.closed_loop.trace_schema import (
    append_per_case_trace, append_per_attempt_trace, write_arm_summary, load_per_case_traces,
)
from src.evaluation.closed_loop.deterministic_checks import run_all_checks, case_pass
from src.evaluation.closed_loop.route_label_adapter import map_route, route_match, agent_match

__all__ = [
    "append_per_case_trace", "append_per_attempt_trace", "write_arm_summary",
    "load_per_case_traces", "run_all_checks", "case_pass",
    "map_route", "route_match", "agent_match",
]
