from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


GROUNDED_ROUTES = {"generic", "building_specific", "combined"}

TEXT_KEYS = {
    "page_content",
    "content",
    "text",
    "snippet",
    "passage",
}

SOURCE_KEYS = {
    "source",
    "sources",
    "filename",
    "name",
    "title",
    "link",
    "url",
}

STOPWORDS = {
    "about",
    "above",
    "after",
    "again",
    "also",
    "because",
    "been",
    "being",
    "between",
    "building",
    "could",
    "does",
    "each",
    "from",
    "have",
    "into",
    "more",
    "most",
    "need",
    "only",
    "should",
    "some",
    "such",
    "than",
    "that",
    "their",
    "there",
    "these",
    "this",
    "those",
    "through",
    "what",
    "when",
    "where",
    "which",
    "with",
    "would",
    "your",
}

CITATION_RE = re.compile(
    r"(\((?:source|sources|kalla|källa):[^)]{2,}\)|\[(?:source|sources|kalla|källa):[^\]]{2,}\])",
    flags=re.IGNORECASE,
)
WORD_RE = re.compile(r"[a-z0-9]+", flags=re.IGNORECASE)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|[\r\n]+")


def _is_present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def _normalize_text(value: Any) -> str:
    text = str(value or "").lower()
    text = text.replace("\\", "/")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _content_tokens(value: Any) -> List[str]:
    return [
        token
        for token in WORD_RE.findall(_normalize_text(value))
        if len(token) > 2 and token not in STOPWORDS
    ]


def _safe_ratio(numerator: int, denominator: int) -> Optional[float]:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _shorten(text: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _split_claims(answer_text: str) -> List[str]:
    claims: List[str] = []
    seen = set()

    for chunk in SENTENCE_SPLIT_RE.split(str(answer_text or "")):
        chunk = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", chunk).strip()
        chunk = chunk.strip(" -")
        if claims and CITATION_RE.fullmatch(chunk.strip(".")):
            claims[-1] = f"{claims[-1]} {chunk}".strip()
            continue
        if not chunk or chunk.endswith("?"):
            continue
        tokens = _content_tokens(chunk)
        has_data_signal = bool(re.search(r"\d", chunk)) or bool(CITATION_RE.search(chunk))
        if len(tokens) < 4 and not has_data_signal:
            continue
        normalized = _normalize_text(chunk)
        if normalized in seen:
            continue
        seen.add(normalized)
        claims.append(chunk)

    return claims


def _flatten_facts(facts: Any) -> Dict[str, Any]:
    flattened: Dict[str, Any] = {}
    if not isinstance(facts, dict):
        return flattened

    def visit(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                next_prefix = f"{prefix}.{key}" if prefix else str(key)
                visit(next_prefix, nested)
            return
        if isinstance(value, list):
            for index, nested in enumerate(value):
                next_prefix = f"{prefix}.{index}" if prefix else str(index)
                visit(next_prefix, nested)
            return
        if _is_present(value):
            flattened[prefix] = value

    visit("", facts)
    return flattened


def _extract_texts_from_context(*contexts: Any) -> List[str]:
    texts: List[str] = []
    seen = set()
    for context in contexts:
        for node in _walk(context):
            if isinstance(node, dict):
                for key, value in node.items():
                    key_text = str(key).lower()
                    if key_text in TEXT_KEYS and isinstance(value, str) and value.strip():
                        normalized = _normalize_text(value)
                        if normalized not in seen:
                            seen.add(normalized)
                            texts.append(value.strip())
                    elif key_text == "snippets" and isinstance(value, list):
                        for item in value:
                            if isinstance(item, str) and item.strip():
                                normalized = _normalize_text(item)
                                if normalized not in seen:
                                    seen.add(normalized)
                                    texts.append(item.strip())
    return texts


def _source_name_parts(source: Any) -> List[str]:
    values: List[str] = []
    if isinstance(source, dict):
        for key in ("name", "filename", "title", "source", "link", "url"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
    elif isinstance(source, str) and source.strip():
        values.append(source.strip())

    parts: List[str] = []
    for value in values:
        clean = value.replace("\\", "/").rstrip("/").split("/")[-1]
        if clean:
            parts.append(clean)
            if "." in clean:
                parts.append(clean.rsplit(".", 1)[0])
        parts.append(value)
    return parts


def _extract_source_names(*sources: Any) -> List[str]:
    names: List[str] = []
    seen = set()
    for source in sources:
        if isinstance(source, list):
            candidates = source
        else:
            candidates = [source]

        for candidate in candidates:
            for part in _source_name_parts(candidate):
                normalized = _normalize_text(part)
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    names.append(part)
    return names


def _extract_nested_source_names(*contexts: Any) -> List[str]:
    names: List[str] = []
    seen = set()
    for context in contexts:
        for node in _walk(context):
            if not isinstance(node, dict):
                continue
            for key, value in node.items():
                if str(key).lower() not in SOURCE_KEYS:
                    continue
                for part in _extract_source_names(value):
                    normalized = _normalize_text(part)
                    if normalized and normalized not in seen:
                        seen.add(normalized)
                        names.append(part)
    return names


def _claim_has_citation(claim: str, source_names: Sequence[str]) -> bool:
    if CITATION_RE.search(claim):
        return True

    claim_norm = _normalize_text(claim)
    for source_name in source_names:
        source_norm = _normalize_text(source_name)
        if len(source_norm) >= 4 and source_norm in claim_norm:
            return True
    return False


def _fact_support_reason(claim: str, facts: Dict[str, Any]) -> Optional[str]:
    claim_norm = _normalize_text(claim)
    claim_tokens = set(_content_tokens(claim))
    for key, value in _flatten_facts(facts).items():
        value_norm = _normalize_text(value)
        if not value_norm:
            continue

        key_tokens = set(_content_tokens(str(key).replace("_", " ")))
        value_tokens = set(_content_tokens(value))
        has_key_context = bool(key_tokens.intersection(claim_tokens))

        if len(value_norm) <= 2:
            if value_norm in claim_norm.split() and has_key_context:
                return f"retrieved_fact:{key}"
            continue

        if value_norm in claim_norm and (has_key_context or value_tokens.intersection(claim_tokens)):
            return f"retrieved_fact:{key}"

        numeric_values = re.findall(r"\d+(?:[.,]\d+)?", str(value))
        if numeric_values and has_key_context:
            for numeric_value in numeric_values:
                if numeric_value.replace(",", ".") in claim.replace(",", "."):
                    return f"retrieved_fact:{key}"
    return None


def _evidence_overlap_reason(claim: str, evidence_texts: Sequence[str]) -> Optional[str]:
    claim_tokens = set(_content_tokens(claim))
    if len(claim_tokens) < 4:
        return None

    required_overlap = max(3, min(6, math.ceil(len(claim_tokens) * 0.45)))
    for index, evidence_text in enumerate(evidence_texts):
        evidence_tokens = set(_content_tokens(evidence_text))
        if len(evidence_tokens) < 4:
            continue
        overlap = claim_tokens.intersection(evidence_tokens)
        if len(overlap) >= required_overlap:
            return f"retrieved_text:{index}"
    return None


def _support_reason(
    claim: str,
    *,
    facts: Dict[str, Any],
    evidence_texts: Sequence[str],
    source_names: Sequence[str],
) -> Optional[str]:
    if _claim_has_citation(claim, source_names) and source_names:
        return "citation"

    fact_reason = _fact_support_reason(claim, facts)
    if fact_reason:
        return fact_reason

    overlap_reason = _evidence_overlap_reason(claim, evidence_texts)
    if overlap_reason:
        return overlap_reason

    return None


def assess_grounding(
    *,
    answer_text: str,
    route: Optional[str],
    retrieved_facts: Optional[Dict[str, Any]] = None,
    vector_sources: Optional[Sequence[Any]] = None,
    evidence_texts: Optional[Sequence[str]] = None,
    source_names: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    route_label = str(route or "").strip().lower()
    if route_label not in GROUNDED_ROUTES:
        return {
            "status": "not_applicable",
            "claim_count": 0,
            "supported_claim_count": 0,
            "unsupported_claim_count": 0,
            "support_ratio": None,
            "unsupported_claim_rate": None,
            "citation_coverage": None,
            "requires_review": False,
        }

    facts = retrieved_facts or {}
    facts_as_text = [f"{key}: {value}" for key, value in _flatten_facts(facts).items()]
    texts = list(evidence_texts or []) + facts_as_text
    sources = list(source_names or []) + _extract_source_names(vector_sources or [])

    claims = _split_claims(answer_text)
    cited_claims = [claim for claim in claims if _claim_has_citation(claim, sources)]
    unsupported: List[str] = []
    support_reasons: Dict[str, int] = {}

    for claim in claims:
        reason = _support_reason(
            claim,
            facts=facts,
            evidence_texts=texts,
            source_names=sources,
        )
        if reason:
            support_reasons[reason.split(":", 1)[0]] = support_reasons.get(reason.split(":", 1)[0], 0) + 1
        else:
            unsupported.append(_shorten(claim))

    claim_count = len(claims)
    supported_count = claim_count - len(unsupported)
    source_count = len({_normalize_text(source) for source in sources if _normalize_text(source)})

    if claim_count == 0:
        status = "no_claims"
    elif unsupported:
        status = "needs_review"
    else:
        status = "passed"

    return {
        "status": status,
        "claim_count": claim_count,
        "supported_claim_count": supported_count,
        "unsupported_claim_count": len(unsupported),
        "support_ratio": _safe_ratio(supported_count, claim_count),
        "unsupported_claim_rate": _safe_ratio(len(unsupported), claim_count),
        "citation_count": len(CITATION_RE.findall(answer_text or "")),
        "cited_claim_count": len(cited_claims),
        "citation_coverage": _safe_ratio(len(cited_claims), claim_count),
        "source_count": source_count,
        "retrieved_fact_count": len(_flatten_facts(facts)),
        "support_reasons": support_reasons,
        "unsupported_claims": unsupported[:5],
        "requires_review": status == "needs_review",
    }


def assess_response_grounding(
    response: Dict[str, Any],
    metadata: Dict[str, Any],
    *,
    route: str,
    retrieved_facts: Dict[str, Any],
    vector_sources: Sequence[Any],
) -> Dict[str, Any]:
    response = response or {}
    metadata = metadata or {}
    evidence_contexts = (
        response.get("grounding_context"),
        response.get("retrieved_context"),
        response.get("source_snippets"),
        metadata.get("grounding_context"),
        metadata.get("aggregated_data"),
        metadata.get("agent_data"),
    )
    source_contexts = (
        response.get("sources"),
        response.get("vector_sources"),
        vector_sources,
        metadata.get("aggregated_data"),
        metadata.get("agent_data"),
    )
    evidence_texts = _extract_texts_from_context(*evidence_contexts)
    source_names = _extract_source_names(
        response.get("sources"),
        response.get("vector_sources"),
        vector_sources,
    ) + _extract_nested_source_names(*source_contexts)

    return assess_grounding(
        answer_text=response.get("content") or "",
        route=route,
        retrieved_facts=retrieved_facts,
        vector_sources=vector_sources,
        evidence_texts=evidence_texts,
        source_names=source_names,
    )
