"""Scope response evidence before deriving facts or rendering deterministic answers.

Retrieval completion and a selected ID do not establish that every returned row
belongs to that building. Keep document guidance separate from building records.
"""

import copy
import re
from typing import Any, Dict

from src.pipeline.safety_analysis import compute_data_freshness, extract_retrieved_facts


def _identifier(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _matches(value: Any, selected: str) -> bool:
    return bool(selected) and _identifier(value).casefold() == selected.casefold()


def _source(state: dict, name: str):
    direct_key = {"generic_sql": "agent_data_generic", "specialized_sql": "agent_data_specialized", "vector": "agent_data_vector"}[name]
    if direct_key in state:
        return True, state[direct_key]
    for container_key in ("aggregated_data", "agent_data"):
        container = state.get(container_key)
        if isinstance(container, dict) and name in container:
            return True, container[name]
    return False, None


def _rows(payload):
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("rows", "data", "records"):
            if isinstance(payload.get(key), list):
                return [row for row in payload[key] if isinstance(row, dict)]
        return [payload]
    return []


def _canonical_field_id(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or value == "building_id_not_available" or re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value):
        return ""
    return value


def select_response_building_id(state: Dict[str, Any], metadata: Dict[str, Any]):
    """Select an ODEN building identifier without promoting local DB IDs.

    The explicit byggnadsid namespace is authoritative. A unique ODEN
    byggnadsid may resolve legacy UUID metadata; several candidates require an
    exact row-level match, never first-row selection. Cached facts and document
    snippets cannot select the current building.
    """
    state, metadata = state or {}, metadata or {}
    selected = _canonical_field_id(metadata.get("byggnadsid"))
    if selected:
        return selected
    present, payload = _source(state, "generic_sql")
    rows = _rows(payload) if present else []
    candidates = {}
    for row in rows:
        lower = {str(key).lower(): value for key, value in row.items()}
        canonical = _canonical_field_id(lower.get("byggnadsid"))
        if canonical:
            candidates.setdefault(canonical.casefold(), canonical)
    if len(candidates) == 1:
        return next(iter(candidates.values()))
    legacy = _identifier(metadata.get("building_id"))
    if len(candidates) > 1:
        matching = {}
        for row in rows:
            lower = {str(key).lower(): value for key, value in row.items()}
            canonical = _canonical_field_id(lower.get("byggnadsid"))
            if canonical and any(_matches(lower.get(key), legacy) for key in ("byggnadsid", "building_id", "50a_uuid", "uuid", "oden_uuid")):
                matching.setdefault(canonical.casefold(), canonical)
        return next(iter(matching.values())) if len(matching) == 1 else None
    if re.fullmatch(r"\d{2}-\d{2}-[A-Za-z\u00c5\u00c4\u00d6\u00e5\u00e4\u00f60-9:_-]+-\d+", legacy):
        return legacy
    return None


def _row_matches(row: dict, selected: str, *, projection_bound: bool = False) -> bool:
    fields = {str(key).lower(): value for key, value in row.items()}
    canonical = fields.get("byggnadsid")
    if _identifier(canonical):
        return _matches(canonical, selected)
    local_id = fields.get("building_id")
    if _identifier(local_id):
        # Numeric IDs in the specialized database are a different namespace.
        # Only an explicit canonical ID on the row establishes correspondence.
        return _matches(local_id, selected)
    return bool(selected and projection_bound)


def _canonical_row(row: dict) -> dict:
    flat = {str(key): value for key, value in row.items() if not isinstance(value, (dict, list))}
    lower = {key.lower(): key for key in flat}
    declared_key = lower.get("epc_egienergiklass")
    if declared_key and flat[declared_key] not in (None, ""):
        flat["energy_class"] = flat[declared_key]
    return extract_retrieved_facts(flat)


def _consistent_facts(rows: list, selected: str):
    facts, conflicts = {}, {}
    for row in rows:
        for key, value in _canonical_row(row).items():
            if key in {"byggnadsid", "building_id"}:
                continue
            if key in conflicts:
                if value not in conflicts[key]:
                    conflicts[key].append(value)
            elif key not in facts:
                facts[key] = value
            elif facts[key] != value:
                conflicts[key] = [facts.pop(key), value]
    if rows and selected:
        facts["byggnadsid"] = selected
        facts["building_id"] = selected
    return facts, conflicts


def prepare_response_evidence(state: Dict[str, Any], metadata: Dict[str, Any], building_id: str) -> Dict[str, Any]:
    """Return scoped records, canonical facts, freshness and explicit limitations.

    This function is pure: it does not mutate graph state or cached metadata.
    Unmatched rows are omitted, including from the model's raw results. Cached
    facts require their own matching ID and are used only without a SQL refresh.
    """
    state, metadata = state or {}, metadata or {}
    selected = _identifier(building_id)
    if selected == "building_id_not_available":
        selected = ""
    identity = metadata.get("building_identity_check") or {}
    projection_bound = (identity.get("status") == "passed" and _matches(identity.get("matched_building_id"), selected))
    results, scoped_rows, oden_rows, sources = {}, [], [], {}
    refreshed = False
    for name, done_key in (("generic_sql", "done_generic_sql"), ("specialized_sql", "done_specialized_sql")):
        present, payload = _source(state, name)
        trace = (metadata.get("query_traces") or {}).get(name)
        # Metadata can retain a trace from an earlier turn; only graph output
        # or a completion flag indicates a retrieval attempt in this state.
        attempted = present or bool(state.get(done_key))
        refreshed = refreshed or attempted
        trace_status = str(trace.get("execution_status") or "").lower() if isinstance(trace, dict) else ""
        failed_refresh = bool(state.get(done_key) and trace_status in {
            "error", "not_found", "model_error", "import_error", "db_error", "empty_result", "not_run", "noop",
        })
        # The graph deep-merges aggregate sections across turns. A failed new
        # query must not make a retained old section look like current evidence.
        candidates = _rows(payload) if present and not failed_refresh else []
        accepted = [row for row in candidates if _row_matches(row, selected, projection_bound=(name == "generic_sql" and projection_bound))]
        excluded = len(candidates) - len(accepted)
        status = "available" if accepted else "unmatched" if excluded else "empty_or_failed" if attempted else "not_requested"
        sources[name] = {"status": status, "accepted_records": len(accepted), "excluded_records": excluded}
        if excluded:
            sources[name]["reason"] = "Returned records lack a verified match to the selected building ID. Local database IDs are not national building IDs."
        if present:
            results[name] = copy.deepcopy(accepted)
        scoped_rows.extend(accepted)
        if name == "generic_sql":
            oden_rows = accepted

    present, vector = _source(state, "vector")
    if present:
        results["vector"] = copy.deepcopy(vector)
        sources["vector"] = {"status": "guidance_only", "reason": "Document excerpts do not establish this building's own facts or identity."}

    cache_used = False
    cache = metadata.get("retrieved_facts")
    if not refreshed and isinstance(cache, dict) and _row_matches(cache, selected):
        scoped_rows = [cache]
        results["cached_building_facts"] = copy.deepcopy(cache)
        sources["cached_building_facts"] = {"status": "cached", "reason": "Previously retrieved facts for the same building; not refreshed during this turn."}
        cache_used = True

    facts, conflicts = _consistent_facts(scoped_rows, selected)
    freshness_rows = oden_rows or ([cache] if cache_used else [])
    year_values = {compute_data_freshness(row)["energy_declaration_year"] for row in freshness_rows}
    if len(year_values) > 1:
        freshness = compute_data_freshness({})
        freshness["freshness_status"] = "mixed"
    else:
        freshness = compute_data_freshness(freshness_rows)
    return {
        "facts": facts,
        "data_freshness": freshness,
        "results": results,
        "evidence_scope": {"building_id": selected or None, "sources": sources, "conflicting_fields": conflicts, "cached_facts_used": cache_used},
    }
