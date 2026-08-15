"""The audit feeds a human label decision, so a misclassification silently corrupts gold.
Fixtures mirror the three hand-verified R2 cases (plan-eil-v22.md)."""
import json
from pathlib import Path

from scripts.audit_must_include import audit, bounded


def test_bounded_rejects_longer_number_and_word():
    assert bounded("155", "value: 155,")
    assert not bounded("155", "eginormkorrgd: 155406")
    assert not bounded("155", "x: 155.4")
    assert not bounded("e", "the answer")   # would be caught earlier as single-char anyway


def _write(tmp: Path, cases: list[dict], caches: dict[str, dict],
           traces: list[dict] = ()) -> tuple[Path, Path]:
    ds = tmp / "cases.jsonl"
    ds.write_text("\n".join(json.dumps(c) for c in cases), encoding="utf-8")
    run = tmp / "run"
    (run / "case_cache").mkdir(parents=True)
    for cid, agg in caches.items():
        (run / "case_cache" / f"{cid}.json").write_text(
            json.dumps({"aggregated_data": agg}), encoding="utf-8")
    if traces:
        (run / "traces").mkdir()
        (run / "traces" / "per_case.jsonl").write_text(
            "\n".join(json.dumps(t) for t in traces), encoding="utf-8")
    return ds, run


def _classes(findings):
    return {(f["case_id"], str(f["token"])): f["class"] for f in findings}


def test_audit_classifies_the_three_r2_defect_classes(tmp_path):
    epc = lambda perf: {"energy_performance": perf, "energy_class": "B"}
    cases = [
        # stale-gold: 189 in no retrieval of BLD-A, sibling included
        {"case_id": "STALE", "expected_building_id": "BLD-A", "must_include": ["F", "189"]},
        {"case_id": "STALE_SIB", "expected_building_id": "BLD-A", "must_include": []},
        # version-slice: 155 absent here (only inside 155406) but bounded in the sibling
        {"case_id": "SLICE", "expected_building_id": "BLD-B", "must_include": ["155"]},
        # record-choice: 155 present alongside a second EPC version
        {"case_id": "LOTTERY", "expected_building_id": "BLD-B", "must_include": ["155"]},
        # no cache file at all
        {"case_id": "NOEV", "must_include": ["district heating"]},
    ]
    caches = {
        "STALE": {"generic_sql": [epc(53)]}, "STALE_SIB": {"generic_sql": [epc(53)]},
        "SLICE": {"generic_sql": [dict(epc(190), epc_eginormkorrgd=155406)]},
        "LOTTERY": {"generic_sql": [epc(155), epc(109)]},
    }
    got = _classes(audit(*_write(tmp_path, cases, caches)))
    assert got[("STALE", "189")] == "stale-gold"
    assert got[("STALE", "F")] == "single-char"
    assert got[("SLICE", "155")] == "version-slice"
    assert got[("LOTTERY", "155")] == "record-choice"
    assert got[("NOEV", "district heating")] == "no-evidence"


def test_audit_downgrades_a_translated_token_to_indirect_not_stale_gold(tmp_path):
    """"ElDirekt" in evidence, gold token "electric": absent at string level, but the case
    passed must_include — the answer translates the Swedish value. Calling that stale-gold
    would invite a wrong label edit; a token that also FAILED stays stale-gold."""
    cases = [{"case_id": "TRANS", "expected_building_id": "BLD", "must_include": ["electric"]},
             {"case_id": "DEAD", "expected_building_id": "BLD2", "must_include": ["189"]}]
    caches = {"TRANS": {"generic_sql": [{"heating_system": "ElDirekt"}]},
              "DEAD": {"generic_sql": [{"energy_performance": 53}]}}
    traces = [{"case_id": "TRANS", "must_include_pass": True},
              {"case_id": "DEAD", "must_include_pass": False}]
    got = _classes(audit(*_write(tmp_path, cases, caches, traces)))
    assert got[("TRANS", "electric")] == "indirect"
    assert got[("DEAD", "189")] == "stale-gold"


def test_audit_marks_a_clean_token_ok(tmp_path):
    cases = [{"case_id": "GOOD", "expected_building_id": "BLD",
              "must_include": ["fjärrvärme"]}]
    caches = {"GOOD": {"generic_sql": [{"heating_system": "Fjärrvärme"}]}}
    got = _classes(audit(*_write(tmp_path, cases, caches)))
    assert got[("GOOD", "fjärrvärme")] == "ok"
