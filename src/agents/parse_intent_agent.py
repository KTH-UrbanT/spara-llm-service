from src.azure_openai_v1 import azure_v1_base_url
from src.agents.completion_options import completion_options
import os
import re
import logging
import sys
import time
import json
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from openai import OpenAI
from src.agents.base_agent import BaseAgent
from src.pipeline.telemetry import record_model_call
from src.pipeline.safety_analysis import normalize_address_candidate

# Load environment variables
load_dotenv()

# Setup logger
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)
logging.getLogger("azure.core.pipeline").setLevel(logging.WARNING)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.ai.inference").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# Azure client configuration
endpoint = os.getenv("AZURE_ENDPOINT")
deployment = os.getenv("BUILDING_MODEL_DEPLOYMENT_NAME")
subscription_key = os.getenv("OPENAI_API_KEY")

if not all([endpoint, deployment, subscription_key]):
    logger.critical("Missing one or more Azure environment variables.")
    sys.exit(1)

try:
    client = OpenAI(
        base_url=azure_v1_base_url(endpoint),
        api_key=subscription_key,
    )
    logger.info("OpenAI client initialized for ParseIntentAgent.")
except Exception as e:
    logger.critical(f"Failed to initialize Azure client: {e}")
    sys.exit(1)

# ---- Retry/backoff config (override with env if desired) ----
MAX_RETRIES = int(os.getenv("AZURE_OPENAI_MAX_RETRIES", "6"))          # total attempts = MAX_RETRIES + 1
BACKOFF_BASE = float(os.getenv("AZURE_OPENAI_BACKOFF_BASE", "1.5"))    # seconds (exponential base)
BACKOFF_CAP = float(os.getenv("AZURE_OPENAI_BACKOFF_CAP", "30"))       # max sleep per attempt (seconds)
JITTER_FRAC = float(os.getenv("AZURE_OPENAI_JITTER_FRAC", "0.25"))     # +/- jitter fraction


def _is_rate_limit_error(exc: Exception) -> bool:
    """Heuristically detect rate-limit/429 errors across SDK versions."""
    name = exc.__class__.__name__.lower()
    msg = str(exc).lower()
    # Common signals
    if "ratelimit" in name or "rate limit" in msg or "too many requests" in msg:
        return True
    if "429" in msg:
        return True
    # Azure core HTTP exceptions sometimes surface as HttpResponseError
    # We'll treat anything with Retry-After header as rate-limit below.
    try:
        resp = getattr(exc, "response", None)
        if resp is not None and getattr(resp, "status_code", None) == 429:
            return True
    except Exception:
        pass
    return False


def _retry_after_seconds(exc: Exception) -> Optional[float]:
    """Read Retry-After header if available."""
    try:
        resp = getattr(exc, "response", None)
        if resp is None:
            return None
        headers = getattr(resp, "headers", {}) or {}
        # headers may be case-insensitive mapping
        for key in ("Retry-After", "retry-after", "RETRY-AFTER"):
            if key in headers:
                raw = headers[key]
                # Usually an integer number of seconds
                try:
                    return float(raw)
                except Exception:
                    return None
    except Exception:
        return None
    return None


def _exp_backoff_sleep(attempt: int, retry_after: Optional[float] = None) -> float:
    """Sleep with exponential backoff + jitter; prefer Retry-After if larger."""
    # base * 2^attempt (capped)
    delay = min(BACKOFF_CAP, BACKOFF_BASE * (2 ** attempt))
    # jitter +/- JITTER_FRAC
    jitter = delay * JITTER_FRAC
    delay_low = max(0.0, delay - jitter)
    delay_high = delay + jitter
    computed = (delay_low + delay_high) / 2.0  # deterministic midpoint; swap for random if desired

    if retry_after is not None:
        computed = max(computed, retry_after)

    sleep_s = min(computed, BACKOFF_CAP)
    time.sleep(sleep_s)
    return sleep_s


# Allowed single intents
ALLOWED = {
    "SQL database",
    "Specialized SQL database",
    "vector database",
}

# Minimal default
DEFAULT_CONTEXT = {
    "intents": ["SQL database"],
    "address": None,
    "ambigious": True,   # keeping original key as in your codebase
    "parsed_intent": "SQL database",
}

SEP_REGEX = re.compile(r"\s*(?:;|,|\+|/|\||&|\band\b)\s*", re.IGNORECASE)


def _intent_input(state: Dict[str, Any]) -> Dict[str, Any]:
    """Keep routing context without sending credentials or promoting stored roles."""
    history = []
    for item in state.get("messages") or []:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            history.append({"role": item["role"], "content": content})

    current = state.get("last_message")
    if not isinstance(current, str) or not current.strip():
        current = next((item["content"] for item in reversed(history) if item["role"] == "user"), "")
    if history and history[-1] == {"role": "user", "content": current}:
        history.pop()

    # Allowlist context fields, including only routing information nested under
    # pending requests. Raw retrieval results and unrelated metadata stay local.
    allowed = {
        "byggnadsid", "building_id", "building_id_from_user", "address",
        "address_from_user", "brf_name", "building_type", "selected_brf_building_id",
        "selected_brf_addresses", "selected_brf_lookup_address", "clarification",
        "needed", "reason", "question_asked", "resolved", "building_identity_check",
        "status", "pending_building_advice_request", "pending_brf_resolution",
        "question", "original_question", "context", "original_context", "type",
        "parsed_intent", "intent_list", "intents", "effective_query", "answer_focus",
        "advice_type", "brf_resolution", "selected_building_id", "selected_addresses",
        "pending_building_request", "pending_address_query", "user_message",
        "address_location_hint", "request_parts", "kind", "sources",
        "selected_address_building_id", "selected_address_lookup_address",
        "address_identity_evidence", "address_selection_evidence",
        "matched_building_id", "matched_address", "missing_fields",
        "clarification_started_user_turn", "clarification_resolved_user_turn",
        "clarification_turn_count",
    }

    def clean(value, depth=0, field=None):
        if depth > 7:
            return None
        if isinstance(value, dict):
            return {key: clean(item, depth + 1, key) for key, item in value.items() if key in allowed}
        if isinstance(value, list):
            items = value if field == "request_parts" else value[:12]
            return [clean(item, depth + 1) for item in items]
        if isinstance(value, str):
            return value[:4000]
        return value if value is None or isinstance(value, (bool, int, float)) else None

    session_context = {}
    for key in ("metadata", "prior_context"):
        if isinstance(state.get(key), dict):
            session_context[key] = clean(state[key])
    return {
        "current_message": current,
        "history": [{**item, "content": item["content"][:4000]} for item in history[-16:]],
        "session_context": session_context,
    }


def _address_in_current_message(address: str, message: str) -> bool:
    """Address extraction may normalize punctuation, never add missing content."""
    def normalized(value):
        return " ".join(re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE))

    candidate = normalized(address)
    return bool(candidate) and f" {candidate} " in f" {normalized(message)} "


def openai_call_worker(prompt_template: str, message: str) -> str:
    """Synchronous call to Azure OpenAI with rate-limit retries; returns raw content string (JSON expected)."""
    messages = [
        {"role": "system", "content": prompt_template},
        {"role": "user", "content": message},
    ]

    last_err: Optional[Exception] = None
    for attempt in range(MAX_RETRIES + 1):
        model_started_at = time.perf_counter()
        try:
            completion = client.chat.completions.create(
                model=deployment,
                messages=messages,
                **completion_options(deployment, temperature=0.9),
                stream=False,
                response_format={"type": "json_object"},
            )
            response_content = completion.choices[0].message.content
            record_model_call(
                component="parse_intent_agent",
                model=deployment,
                input_messages=messages,
                output_text=response_content,
                response=completion,
                latency_seconds=time.perf_counter() - model_started_at,
                success=True,
            )
            return response_content
        except Exception as e:
            last_err = e
            record_model_call(
                component="parse_intent_agent",
                model=deployment,
                input_messages=messages,
                latency_seconds=time.perf_counter() - model_started_at,
                success=False,
                error=e,
            )
            # Rate limit or transient? If yes, backoff and retry.
            if _is_rate_limit_error(e):
                ra = _retry_after_seconds(e)
                slept = _exp_backoff_sleep(attempt, ra)
                logger.warning(
                    f"Rate limit hit (attempt {attempt+1}/{MAX_RETRIES+1}); "
                    f"sleeping {slept:.1f}s (Retry-After={ra})"
                )
                continue

            # Network hiccups often present as HTTP/connection errors; treat first few as transient
            transient = any(t in str(e).lower() for t in ("timeout", "temporarily unavailable", "connection reset", "dns", "server error", "503"))
            if transient and attempt < MAX_RETRIES:
                slept = _exp_backoff_sleep(attempt)
                logger.warning(
                    f"Transient error (attempt {attempt+1}/{MAX_RETRIES+1}); sleeping {slept:.1f}s then retry. Error: {e}"
                )
                continue

            # Non-retryable
            logger.error(f"OpenAI call failed (non-retryable): {e}")
            break

    # Exhausted retries
    raise RuntimeError(f"OpenAI call failed after retries: {last_err}")


class ParseIntentAgent(BaseAgent):
    def __init__(self, prompt_path: str = None):
        super().__init__()
        default_prompt_path = os.path.join(
            os.path.dirname(__file__), "..", "prompts", "building_intent_classification_prompt.txt"
        )
        try:
            self.prompt_template = self._load_prompt(prompt_path or default_prompt_path)
        except Exception as e:
            logger.critical(f"Prompt loading failed: {e}")
            raise

    def _load_prompt(self, path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        routing_input = _intent_input(state)
        user_input = routing_input["current_message"]

        if not user_input:
            logger.warning("No user input found; using DEFAULT_CONTEXT.")
            state["context"] = DEFAULT_CONTEXT.copy()
            return state

        try:
            raw = openai_call_worker(self.prompt_template, json.dumps(routing_input, ensure_ascii=False))
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            state["context"] = DEFAULT_CONTEXT.copy()
            return state

        try:
            parsed = self._parse_response(raw, user_input=user_input)
            if parsed["address"] and not _address_in_current_message(parsed["address"], user_input):
                # Stored identity is carried separately. A model must not turn an
                # earlier or invented address into a new user-supplied address.
                parsed["address"] = None
            # logger.info(f"LLM classification parsed: {parsed}")
            state["context"] = parsed
        except Exception as e:
            logger.error(f"ParseIntentAgent parsing error: {e}")
            state["context"] = DEFAULT_CONTEXT.copy()

        return state

    def _parse_response(self, text: str, *, user_input: Optional[str] = None) -> Dict[str, Any]:
        """
        Expects the model to return JSON. Validates/normalizes:
          - 'parsed_intent' (string; can include separators)
          - 'intents' (list[str]) derived from parsed_intent
          - 'address' (string or null; default None)
          - 'ambigious' (boolean; default True)
          - optional 'request_parts', each with a question, kind and source list
        Falls back to DEFAULT_CONTEXT on any validation issue.
        """
        d = json.loads(text)
        if not isinstance(d, dict):
            raise ValueError("Intent response must be a JSON object")

        # Prefer explicit parsed_intent from the model; otherwise derive from 'intents'
        raw_pi = str(d.get("parsed_intent", "")).strip()
        if not raw_pi and isinstance(d.get("intents"), list):
            raw_pi = "; ".join(map(str, d["intents"]))

        # Split using permissive separators, validate against ALLOWED
        parts = [p.strip() for p in SEP_REGEX.split(raw_pi) if p.strip()] if raw_pi else []
        if not parts and isinstance(d.get("intents"), list):
            parts = [str(p).strip() for p in d["intents"] if str(p).strip()]

        # Validate intents are within ALLOWED
        if any(p not in ALLOWED for p in parts):
            raise ValueError(f"Disallowed intent(s) in parsed_intent: {parts}")

        # Normalize
        intents = list(dict.fromkeys(parts))
        request_parts = []
        raw_request_parts = d.get("request_parts")
        for part in raw_request_parts if isinstance(raw_request_parts, list) else []:
            if not isinstance(part, dict):
                continue
            question = part.get("question")
            kind = part.get("kind", "other")
            kind = {"explanation": "interpretation", "next_step": "advice", "next_steps": "advice"}.get(kind, kind) if isinstance(kind, str) else "other"
            sources = part.get("sources")
            if not isinstance(question, str) or not question.strip():
                continue
            if kind not in {"fact", "interpretation", "advice", "comparison", "other"}:
                kind = "other"
            sources = [source for source in sources if isinstance(source, str) and source in ALLOWED] if isinstance(sources, list) else []
            if not sources:
                sources = list(intents) or ["SQL database"]
                if kind in {"interpretation", "advice", "comparison"}:
                    sources.append("vector database")
            sources = list(dict.fromkeys(sources))
            request_parts.append({"question": question.strip(), "kind": kind, "sources": sources})
            # Every subquestion contributes its retrieval needs. A top-level
            # classification must not discard the advisory half of a request.
            intents = list(dict.fromkeys([*intents, *sources]))
        if not intents:
            return DEFAULT_CONTEXT.copy()
        address = d.get("address", None)
        if address is not None and not isinstance(address, str):
            raise ValueError("Intent address must be a string or null")
        address = address.strip() or None if isinstance(address, str) else None
        if address:
            # Check every original token before removing question grammar. In
            # particular, an invented city/postcode must not become acceptable
            # just because a cleaner can recover a real street from the value.
            if user_input is not None and not _address_in_current_message(address, user_input):
                address = None
            else:
                address = normalize_address_candidate(address, allow_four_digit_number=True)
        ambigious = d.get("ambigious", True)
        if not isinstance(ambigious, bool):
            raise ValueError("Intent ambigious must be a JSON boolean")
        parsed_intent = " ; ".join(intents)  # keep your format with spaces around semicolons

        context = {
            "intents": intents,
            "address": address,
            "ambigious": ambigious,
            "parsed_intent": parsed_intent,
        }
        if request_parts:
            context["request_parts"] = request_parts
        return context
