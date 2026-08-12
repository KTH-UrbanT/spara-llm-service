"""Gold-grounded checks computed by the offline harness only — never by the judge.

FIELD_NAME_MAP translates the dataset's `expected_fields` names to the keys that
actually appear in the pipeline's data rows. The dataset only uses three field
names (energy_class / energy_performance / heating_system), and the pipeline's
`_enrich_building_fact_aliases` adds those exact keys to every generic_sql row
(derived via `extract_retrieved_facts`), so `sql_fields_used` already contains
them verbatim — the map is identity. Extend only if new `expected_fields` appear.
Vector chunks are deliberately excluded from field coverage (free-text passages
are not typed registry fields).
"""
from __future__ import annotations
from src.evaluation.closed_loop.route_label_adapter import route_match, agent_match

FIELD_NAME_MAP: dict[str, str] = {
    "energy_class": "energy_class",
    "energy_performance": "energy_performance",
    "heating_system": "heating_system",
}


def check_building_id_match(snapshot: dict, case: dict) -> bool:
    expected = case.get("expected_building_id")
    return True if expected is None else snapshot.get("resolved_building_id") == expected


def check_field_coverage(snapshot: dict, case: dict, tau_field: float = 1.0) -> tuple[bool, float]:
    expected_fields = case.get("expected_fields") or []
    if not expected_fields:
        return True, 1.0
    mapped = {FIELD_NAME_MAP.get(f, f) for f in expected_fields}
    used = set(snapshot.get("sql_fields_used") or [])
    coverage = len(mapped & used) / len(mapped)
    return coverage >= tau_field, coverage


def check_must_include(final_answer: str, case: dict) -> bool:
    lo = final_answer.lower()
    return all(str(p).lower() in lo for p in (case.get("must_include") or []))


def check_must_not_include(final_answer: str, case: dict) -> bool:
    lo = final_answer.lower()
    return not any(str(p).lower() in lo for p in (case.get("must_not_include") or []))


def check_semantic_judge_pass(answer_verdict: dict | None) -> bool | None:
    """Tri-state: True / False / None ("not measured").

    None is returned when nothing was actually judged — a verdict with no axes
    (a short-circuit that never called the judge) or one carrying `_fail_open`
    (the judge crashed and the safety path returned a bare "pass"). Counting
    either as a pass is what let every dodged question score a pass (§1.6a).

    `analyze_results_closed_loop.py` drops None per check, so the per-check rate
    is over cases that were genuinely measured, while `case_pass` treats None as
    not passing (None is falsy) — an unmeasured case is not a passing case.
    """
    v = answer_verdict or {}
    axes = v.get("axes") or {}
    if not axes or axes.get("_fail_open"):
        return None
    return v.get("verdict") == "pass"


def run_all_checks(snapshot: dict, final_answer: str, case: dict,
                   answer_verdict: dict | None, tau_field: float = 1.0) -> dict:
    fc_pass, fc_val = check_field_coverage(snapshot, case, tau_field)
    return {
        "route_match": route_match(snapshot, case.get("expected_route", "")),
        "agent_match": agent_match(snapshot, case.get("expected_agent")),
        "building_id_match": check_building_id_match(snapshot, case),
        "field_coverage": fc_val, "field_coverage_pass": fc_pass,
        "must_include_pass": check_must_include(final_answer, case),
        "must_not_include_pass": check_must_not_include(final_answer, case),
        "semantic_judge_pass": check_semantic_judge_pass(answer_verdict),
    }


def case_pass(checks: dict) -> bool:
    # semantic_judge_pass is tri-state; None ("not measured") is falsy and so
    # already fails the case. Do not "fix" this into `is not False`.
    return all([
        checks.get("route_match", False),
        checks.get("agent_match", True),
        checks.get("building_id_match", True),
        checks.get("field_coverage_pass", True),
        checks.get("must_include_pass", True),
        checks.get("must_not_include_pass", True),
        checks.get("semantic_judge_pass", False),
    ])
