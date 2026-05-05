"""Unit tests for trace_store utilities.

Section 0.5 of plan-eil-v1.md introduced `truncate_for_trace` to bound the size
of trace records. These tests cover its leaf-level truncation behavior across
every JSON-relevant type, ensuring evaluator traces stay manageable even when
vector store snippets exceed 50 KB.
"""
from src.evaluation.trace_store import (
    TRACE_SCHEMA_VERSION,
    build_evaluation_trace,
    truncate_for_trace,
)


class TestTruncateForTrace:
    def test_short_string_unchanged(self):
        assert truncate_for_trace("hello") == "hello"

    def test_long_string_truncated(self):
        big = "x" * 10000
        out = truncate_for_trace(big, max_length=4000)
        assert len(out) <= 4100
        assert out.endswith("…[truncated]")
        assert out.startswith("x" * 100)  # Sanity: original prefix preserved.

    def test_at_threshold_not_truncated(self):
        """Strings exactly at max_length must NOT have the marker appended."""
        s = "x" * 4000
        assert truncate_for_trace(s, max_length=4000) == s

    def test_dict_recursion(self):
        out = truncate_for_trace({"k": "x" * 5000}, max_length=4000)
        assert isinstance(out, dict)
        assert out["k"].endswith("…[truncated]")

    def test_nested_list_in_dict(self):
        out = truncate_for_trace(
            {"vector": {"snippets": ["x" * 5000, "small"]}}, max_length=4000
        )
        snippets = out["vector"]["snippets"]
        assert snippets[0].endswith("…[truncated]")
        assert snippets[1] == "small"

    def test_primitives_unchanged(self):
        assert truncate_for_trace(42) == 42
        assert truncate_for_trace(3.14) == 3.14
        assert truncate_for_trace(True) is True
        assert truncate_for_trace(None) is None

    def test_tuple_becomes_list(self):
        """JSON has no tuple type; tuples must serialize as lists."""
        out = truncate_for_trace(("a", "b"))
        assert isinstance(out, list)
        assert out == ["a", "b"]

    def test_non_json_type_coerced_via_repr(self):
        """Defensive fallback: anything weird gets repr()'d, not raised."""
        class Weird:
            def __repr__(self):
                return "<Weird thing>"

        out = truncate_for_trace(Weird())
        assert out == "<Weird thing>"

    def test_does_not_mutate_input(self):
        """Truncation returns a new structure; the original is untouched."""
        original = {"k": "x" * 5000}
        original_value = original["k"]
        _ = truncate_for_trace(original, max_length=4000)
        assert original["k"] is original_value
        assert len(original["k"]) == 5000


class TestBuildEvaluationTrace:
    def test_schema_version_is_2(self):
        record = build_evaluation_trace()
        assert record["trace_schema_version"] == 2
        assert TRACE_SCHEMA_VERSION == 2

    def test_includes_timestamp(self):
        record = build_evaluation_trace()
        assert "timestamp_utc" in record
        # ISO 8601 with timezone offset.
        assert "T" in record["timestamp_utc"]

    def test_passes_through_arbitrary_fields(self):
        record = build_evaluation_trace(question_id="Q1", arm="A2", verdict="pass")
        assert record["question_id"] == "Q1"
        assert record["arm"] == "A2"
        assert record["verdict"] == "pass"
