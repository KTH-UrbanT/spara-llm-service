import pytest
from src.evaluation.closed_loop.deterministic_checks import (
    check_building_id_match, check_field_coverage, check_must_include,
    check_must_not_include, check_semantic_judge_pass, case_pass, FIELD_NAME_MAP,
)

def _s(**kw) -> dict:
    d = {"top_route": "building", "clarification_fired": False,
         "request_address_fired": False, "specialists_invoked": ["generic_sql_agent"],
         "resolved_building_id": "01-80-TEST-1",
         "sql_fields_used": ["byggnadsid", "energy_performance", "energy_class"],
         "vector_chunks_retrieved": []}
    d.update(kw); return d

def test_bid_absent(): assert check_building_id_match(_s(), {}) is True
def test_bid_pass(): assert check_building_id_match(_s(), {"expected_building_id": "01-80-TEST-1"}) is True
def test_bid_fail(): assert check_building_id_match(_s(), {"expected_building_id": "OTHER"}) is False

def test_fc_empty(): p, v = check_field_coverage(_s(), {}); assert p is True and v == 1.0
def test_fc_full():
    f = FIELD_NAME_MAP.get("energy_performance", "energy_performance")
    p, v = check_field_coverage(_s(sql_fields_used=[f, "byggnadsid"]),
                                 {"expected_fields": ["energy_performance"]})
    assert p is True and v == pytest.approx(1.0)
def test_fc_partial():
    ef = FIELD_NAME_MAP.get("energy_performance", "energy_performance")
    p, v = check_field_coverage(_s(sql_fields_used=[ef]),
                                 {"expected_fields": ["energy_performance", "heating_system"]},
                                 tau_field=1.0)
    assert p is False and v == pytest.approx(0.5)
def test_fc_vector_excluded():
    p, v = check_field_coverage(
        _s(sql_fields_used=[], vector_chunks_retrieved=[{"source": "g.pdf"}]),
        {"expected_fields": ["energy_performance"]})
    assert p is False

def test_must_include_pass(): assert check_must_include("Atemp is 114.", {"must_include": ["Atemp","114"]}) is True
def test_must_include_ci(): assert check_must_include("the atemp is 114.", {"must_include": ["Atemp"]}) is True
def test_must_include_fail(): assert check_must_include("Unknown.", {"must_include": ["114"]}) is False
def test_must_not_include_pass(): assert check_must_not_include("114 m2.", {"must_not_include": ["guaranteed"]}) is True
def test_must_not_include_fail(): assert check_must_not_include("Guaranteed!", {"must_not_include": ["guaranteed"]}) is False

def test_case_pass_all_true():
    assert case_pass({k: True for k in ["route_match","agent_match","building_id_match",
        "field_coverage_pass","must_include_pass","must_not_include_pass","semantic_judge_pass"]}) is True
def test_case_pass_one_false():
    checks = {k: True for k in ["route_match","agent_match","building_id_match",
        "field_coverage_pass","must_include_pass","must_not_include_pass","semantic_judge_pass"]}
    checks["building_id_match"] = False
    assert case_pass(checks) is False
