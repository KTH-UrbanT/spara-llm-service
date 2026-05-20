from src.pipeline.telemetry import (
    record_model_call,
    record_retrieval_call,
    record_sql_call,
    telemetry_context,
    telemetry_snapshot,
)


class Usage:
    prompt_tokens = 10
    completion_tokens = 5
    total_tokens = 15


class Response:
    usage = Usage()


def test_telemetry_context_collects_request_metrics(monkeypatch):
    monkeypatch.setenv("SPARA_ESTIMATED_INPUT_COST_PER_1K_TOKENS", "0.10")
    monkeypatch.setenv("SPARA_ESTIMATED_OUTPUT_COST_PER_1K_TOKENS", "0.20")

    with telemetry_context("unit_test"):
        record_model_call(
            component="generic_agent",
            model="test-model",
            input_messages=[{"role": "user", "content": "question"}],
            output_text="answer",
            response=Response(),
            latency_seconds=0.25,
            success=True,
        )
        record_retrieval_call(
            component="vector_retrieval",
            latency_seconds=0.1,
            result_count=2,
            success=True,
        )
        record_sql_call(
            component="generic_sql",
            latency_seconds=0.2,
            row_count=0,
            success=False,
            error="missing table",
        )
        snapshot = telemetry_snapshot()

    assert snapshot["operation"] == "unit_test"
    assert snapshot["model_call_count"] == 1
    assert snapshot["retrieval_call_count"] == 1
    assert snapshot["sql_call_count"] == 1
    assert snapshot["prompt_tokens"] == 10
    assert snapshot["completion_tokens"] == 5
    assert snapshot["total_tokens"] == 15
    assert snapshot["token_usage_estimated"] is False
    assert snapshot["estimated_cost_usd"] == 0.002
    assert snapshot["component_latency_seconds"]["generic_agent"] == 0.25
    assert snapshot["component_latency_seconds"]["vector_retrieval"] == 0.1
    assert snapshot["component_latency_seconds"]["generic_sql"] == 0.2
    assert snapshot["failures"] == [
        {"component": "generic_sql", "error": "missing table"}
    ]


def test_telemetry_calls_are_noops_without_context():
    record_model_call(component="generic_agent", input_messages=[], output_text="")

    assert telemetry_snapshot() == {}
