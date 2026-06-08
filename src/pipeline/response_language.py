from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional


SWEDISH_MARKER_WORDS = {
    "adress",
    "adressen",
    "anvanda",
    "använd",
    "ar",
    "att",
    "behover",
    "behöver",
    "bor",
    "byggnad",
    "byggnaden",
    "den",
    "det",
    "du",
    "eller",
    "energiforbrukning",
    "energiförbrukning",
    "energiklass",
    "energiprestanda",
    "ett",
    "fastighet",
    "fastigheten",
    "finns",
    "fjarrvarme",
    "fjärrvärme",
    "for",
    "för",
    "har",
    "hej",
    "hur",
    "jag",
    "kan",
    "med",
    "min",
    "mina",
    "mitt",
    "och",
    "om",
    "ska",
    "tack",
    "uppvarmning",
    "uppvärmning",
    "vad",
    "var",
    "varfor",
    "varför",
    "ventilationen",
    "vi",
    "vilka",
    "vilken",
    "vilket",
    "vill",
    "vår",
    "våra",
    "vårt",
    "är",
}

ENGLISH_MARKER_WORDS = {
    "address",
    "advice",
    "are",
    "building",
    "can",
    "class",
    "could",
    "do",
    "does",
    "energy",
    "for",
    "heating",
    "hello",
    "help",
    "hi",
    "how",
    "is",
    "my",
    "our",
    "performance",
    "please",
    "should",
    "system",
    "thanks",
    "the",
    "this",
    "use",
    "ventilation",
    "we",
    "what",
    "which",
    "why",
    "with",
    "you",
}

SWEDISH_PHRASES = (
    r"\bjag\s+bor\b",
    r"\bvi\s+bor\b",
    r"\bmin\s+adress\b",
    r"\bv[åa]r\s+(?:byggnad|fastighet|brf)\b",
    r"\bvad\s+[äa]r\b",
    r"\bhur\s+kan\b",
    r"\bkan\s+(?:du|vi|ni)\b",
    r"\bvilken\s+(?:byggnad|adress|energiklass)\b",
)

ENGLISH_PHRASES = (
    r"\bwhat\s+is\b",
    r"\bhow\s+can\b",
    r"\bcan\s+(?:you|we|i)\b",
    r"\bdo\s+(?:we|i|you)\b",
    r"\bshould\s+(?:we|i)\b",
    r"\bmy\s+building\b",
    r"\bour\s+building\b",
    r"\bwhich\s+(?:building|address|energy)\b",
)


def _normalize_language(value: Any) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    if normalized in {"sv", "se", "swe", "swedish", "svenska"}:
        return "sv"
    if normalized in {"en", "eng", "english"}:
        return "en"
    return None


def _word_tokens(text: str) -> List[str]:
    return re.findall(r"[A-Za-zÅÄÖåäö]+", text.lower())


def _score_language(text: str) -> tuple[int, int]:
    lowered = str(text or "").lower()
    tokens = _word_tokens(lowered)

    swedish = sum(2 for token in tokens if token in SWEDISH_MARKER_WORDS)
    english = sum(2 for token in tokens if token in ENGLISH_MARKER_WORDS)

    swedish += sum(3 for pattern in SWEDISH_PHRASES if re.search(pattern, lowered, re.IGNORECASE))
    english += sum(3 for pattern in ENGLISH_PHRASES if re.search(pattern, lowered, re.IGNORECASE))

    if re.search(r"[åäö]", lowered):
        swedish += 1

    if re.search(
        r"\b[A-Za-zÅÄÖåäö.'-]*(?:gatan|vägen|vagen|gränd|grand|allén|allen|allé|alle|väg)\b",
        lowered,
    ):
        swedish += 1

    return swedish, english


def detect_response_language(text: Any) -> Optional[str]:
    """Detect whether the response should be Swedish or English.

    Question words get more weight than Swedish address names, so "What is the
    energy class for Öregrundsgatan 9?" stays English.
    """
    if not isinstance(text, str) or not text.strip():
        return None

    explicit = re.search(
        r"response[_\s-]*language\s*[:=]\s*[\"']?(swedish|sv|english|en)\b",
        text,
        flags=re.IGNORECASE,
    )
    if explicit:
        return _normalize_language(explicit.group(1))

    swedish, english = _score_language(text)

    if swedish >= english + 2:
        return "sv"
    if english >= swedish + 2:
        return "en"
    if swedish > 0 and english == 0:
        return "sv"
    if english > 0 and swedish == 0:
        return "en"
    return None


def _iter_recent_user_messages(messages: Optional[Iterable[Dict[str, Any]]]) -> Iterable[str]:
    try:
        recent_messages = list(messages or [])
    except TypeError:
        return
    for message in reversed(recent_messages):
        if not isinstance(message, dict):
            continue
        if str((message or {}).get("role") or "").lower() != "user":
            continue
        content = (message or {}).get("content")
        if isinstance(content, str) and content.strip():
            yield content


def response_language_for_message(
    message: Any,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    messages: Optional[Iterable[Dict[str, Any]]] = None,
    default: str = "en",
) -> str:
    detected = detect_response_language(message)
    if detected:
        return detected

    metadata_language = _normalize_language((metadata or {}).get("response_language"))
    if metadata_language:
        return metadata_language

    for recent_message in _iter_recent_user_messages(messages):
        detected = detect_response_language(recent_message)
        if detected:
            return detected

    return _normalize_language(default) or "en"


def is_swedish_response(language: Any) -> bool:
    return _normalize_language(language) == "sv"


def language_name(language: Any) -> str:
    return "Swedish" if is_swedish_response(language) else "English"


def language_instruction_for_message(
    message: Any,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    messages: Optional[Iterable[Dict[str, Any]]] = None,
) -> str:
    language = response_language_for_message(message, metadata=metadata, messages=messages)
    if is_swedish_response(language):
        return (
            "Response language: Swedish. The user's latest question is in Swedish, "
            "so answer in Swedish. Keep official Swedish terms such as EKR, PBF, "
            "Atemp, Energiklass, Fjärrvärme, and byggnadsid untranslated."
        )
    return (
        "Response language: English. The user's latest question is in English, "
        "so answer in English. Keep official Swedish terms such as EKR, PBF, "
        "Atemp, Energiklass, Fjärrvärme, and byggnadsid untranslated."
    )


def choose_language_text(language: Any, *, english: str, swedish: str) -> str:
    return swedish if is_swedish_response(language) else english
