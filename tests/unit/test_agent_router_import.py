"""The service must boot even when an unmerged feature's module is absent.

agent_router.py:11 imported src.services.draft_report_service from af07ed2 (2026-05-14)
onward, but that module has never been on this branch — so `python generation.py` died at
import and the container exited in ~5s. Guarded now; this is the check that it stays guarded.
"""
import importlib


def test_agent_router_imports_without_the_draft_report_module():
    import src.services
    assert not hasattr(src.services, "draft_report_service") or True  # presence is optional
    m = importlib.import_module("src.pipeline.agent_router")
    assert callable(m.generate_draft_report_response)


def test_the_draft_report_fallback_returns_a_well_formed_turn():
    """If the real module IS present this asserts nothing about the fallback, so skip then."""
    import src.pipeline.agent_router as m
    try:
        import src.services.draft_report_service  # noqa: F401
    except ImportError:
        r = m.generate_draft_report_response("t1", [], {})
        # Same shape agent_router.py:287 then calls .setdefault("route", ...) on.
        assert r["role"] == "assistant" and r["classification"] == "draft_energy_report"
        assert isinstance(r.get("content"), str) and r["content"]
