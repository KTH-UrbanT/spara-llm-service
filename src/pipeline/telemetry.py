from __future__ import annotations

import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterable, Iterator, Optional


_CURRENT_TELEMETRY: ContextVar[Optional["RequestTelemetry"]] = ContextVar(
    "spara_request_telemetry",
    default=None,
)


def _now() -> float:
    return time.perf_counter()


def _is_present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _estimate_tokens_from_text(value: Any) -> int:
    text = str(value or "")
    if not text:
        return 0
    return max(1, round(len(text) / 4))


def _estimate_tokens_from_messages(messages: Iterable[Dict[str, Any]] | None) -> int:
    return sum(_estimate_tokens_from_text(message.get("content")) for message in messages or [])


def _usage_from_response(response: Any) -> Dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return {}

    def get_usage_value(name: str) -> int:
        if isinstance(usage, dict):
            value = usage.get(name)
        else:
            value = getattr(usage, name, None)
        return int(value or 0)

    values = {
        "prompt_tokens": get_usage_value("prompt_tokens"),
        "completion_tokens": get_usage_value("completion_tokens"),
        "total_tokens": get_usage_value("total_tokens"),
    }
    return {key: value for key, value in values.items() if value}


def _cost_rates() -> tuple[float, float]:
    def env_float(name: str) -> float:
        try:
            return float(os.getenv(name, "0") or "0")
        except (TypeError, ValueError):
            return 0.0

    input_rate = env_float("SPARA_ESTIMATED_INPUT_COST_PER_1K_TOKENS")
    output_rate = env_float("SPARA_ESTIMATED_OUTPUT_COST_PER_1K_TOKENS")
    return input_rate, output_rate


class RequestTelemetry:
    def __init__(self, operation: str = "request"):
        self.operation = operation
        self.started_at = _now()
        self.model_calls = 0
        self.retrieval_calls = 0
        self.sql_calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.token_usage_estimated = False
        self.estimated_cost_usd = 0.0
        self.cost_configured = False
        self.component_latency_seconds: Dict[str, float] = {}
        self.failures = []

    def add_component_latency(self, component: str, latency_seconds: float | None) -> None:
        if not component or latency_seconds is None:
            return
        self.component_latency_seconds[component] = round(
            self.component_latency_seconds.get(component, 0.0) + max(float(latency_seconds), 0.0),
            4,
        )

    def add_failure(self, *, component: str, error: Any) -> None:
        self.failures.append(
            {
                "component": component or "unknown",
                "error": str(error)[:500],
            }
        )

    def add_model_call(
        self,
        *,
        component: str,
        model: str | None = None,
        input_messages: Iterable[Dict[str, Any]] | None = None,
        output_text: Any = None,
        response: Any = None,
        latency_seconds: float | None = None,
        success: bool = True,
        error: Any = None,
    ) -> None:
        self.model_calls += 1
        self.add_component_latency(component or "model", latency_seconds)

        usage = _usage_from_response(response)
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
        estimated = False

        if prompt_tokens is None:
            prompt_tokens = _estimate_tokens_from_messages(input_messages)
            estimated = True
        if completion_tokens is None:
            completion_tokens = _estimate_tokens_from_text(output_text)
            estimated = True
        if total_tokens is None or total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens
            estimated = True

        self.prompt_tokens += int(prompt_tokens or 0)
        self.completion_tokens += int(completion_tokens or 0)
        self.total_tokens += int(total_tokens or 0)
        self.token_usage_estimated = self.token_usage_estimated or estimated

        input_rate, output_rate = _cost_rates()
        if input_rate or output_rate:
            self.cost_configured = True
            self.estimated_cost_usd += (
                (int(prompt_tokens or 0) / 1000.0) * input_rate
                + (int(completion_tokens or 0) / 1000.0) * output_rate
            )

        if not success or error:
            self.add_failure(component=component or model or "model", error=error or "model call failed")

    def add_retrieval_call(
        self,
        *,
        component: str,
        latency_seconds: float | None = None,
        result_count: int | None = None,
        success: bool = True,
        error: Any = None,
    ) -> None:
        self.retrieval_calls += 1
        self.add_component_latency(component or "retrieval", latency_seconds)
        if not success or error:
            self.add_failure(component=component or "retrieval", error=error or "retrieval failed")

    def add_sql_call(
        self,
        *,
        component: str,
        latency_seconds: float | None = None,
        row_count: int | None = None,
        success: bool = True,
        error: Any = None,
    ) -> None:
        self.sql_calls += 1
        self.add_component_latency(component or "sql", latency_seconds)
        if not success or error:
            self.add_failure(component=component or "sql", error=error or "sql call failed")

    def snapshot(self) -> Dict[str, Any]:
        payload = {
            "operation": self.operation,
            "total_latency_seconds": round(_now() - self.started_at, 4),
            "model_call_count": self.model_calls,
            "retrieval_call_count": self.retrieval_calls,
            "sql_call_count": self.sql_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "token_usage_estimated": self.token_usage_estimated,
            "estimated_cost_usd": round(self.estimated_cost_usd, 8) if self.cost_configured else None,
            "cost_rates_configured": self.cost_configured,
            "component_latency_seconds": self.component_latency_seconds,
            "failures": self.failures,
        }
        return {
            key: value
            for key, value in payload.items()
            if isinstance(value, bool) or _is_present(value)
        }


@contextmanager
def telemetry_context(operation: str = "request") -> Iterator[RequestTelemetry]:
    existing = _CURRENT_TELEMETRY.get()
    if existing is not None:
        yield existing
        return

    telemetry = RequestTelemetry(operation=operation)
    token = _CURRENT_TELEMETRY.set(telemetry)
    try:
        yield telemetry
    finally:
        _CURRENT_TELEMETRY.reset(token)


def current_telemetry() -> Optional[RequestTelemetry]:
    return _CURRENT_TELEMETRY.get()


def telemetry_snapshot() -> Dict[str, Any]:
    telemetry = current_telemetry()
    return telemetry.snapshot() if telemetry else {}


def record_component_latency(component: str, latency_seconds: float | None) -> None:
    telemetry = current_telemetry()
    if telemetry:
        telemetry.add_component_latency(component, latency_seconds)


def record_model_call(**kwargs) -> None:
    telemetry = current_telemetry()
    if telemetry:
        telemetry.add_model_call(**kwargs)


def record_retrieval_call(**kwargs) -> None:
    telemetry = current_telemetry()
    if telemetry:
        telemetry.add_retrieval_call(**kwargs)


def record_sql_call(**kwargs) -> None:
    telemetry = current_telemetry()
    if telemetry:
        telemetry.add_sql_call(**kwargs)
