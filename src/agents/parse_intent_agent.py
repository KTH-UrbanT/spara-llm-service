import os
import re
import logging
import sys
import time
import json
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from openai import AzureOpenAI
from src.agents.base_agent import BaseAgent
from src.pipeline.telemetry import record_model_call

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
api_version = os.getenv("BUILDING_MODEL_API_VERSION")

if not all([endpoint, deployment, subscription_key]):
    logger.critical("Missing one or more Azure environment variables.")
    sys.exit(1)

try:
    client = AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=subscription_key,
        api_version=api_version,
    )
    logger.info("AzureOpenAI client initialized for ParseIntentAgent.")
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
                max_tokens=800,
                temperature=0.9,
                top_p=0.23,
                frequency_penalty=0,
                presence_penalty=0,
                stop=None,
                stream=False,
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
        # Prefer the most recent user message; fall back to 'last_message' if messages list is empty
        messages = state.get("messages", [])
        user_input = messages[-1]["content"] if messages else state.get("last_message", "")

        if not user_input:
            logger.warning("No user input found; using DEFAULT_CONTEXT.")
            state["context"] = DEFAULT_CONTEXT.copy()
            return state

        try:
            raw = openai_call_worker(self.prompt_template, user_input)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            state["context"] = DEFAULT_CONTEXT.copy()
            return state

        try:
            parsed = self._parse_response(raw)
            # logger.info(f"LLM classification parsed: {parsed}")
            state["context"] = parsed
        except Exception as e:
            logger.error(f"ParseIntentAgent parsing error: {e}")
            state["context"] = DEFAULT_CONTEXT.copy()

        return state

    def _parse_response(self, text: str) -> Dict[str, Any]:
        """
        Expects the model to return JSON. Validates/normalizes:
          - 'parsed_intent' (string; can include separators)
          - 'intents' (list[str]) derived from parsed_intent
          - 'address' (pass-through; default None)
          - 'ambigious' (pass-through; default True)
        Falls back to DEFAULT_CONTEXT on any validation issue.
        """
        d = json.loads(text)

        # Prefer explicit parsed_intent from the model; otherwise derive from 'intents'
        raw_pi = str(d.get("parsed_intent", "")).strip()
        if not raw_pi and isinstance(d.get("intents"), list):
            raw_pi = "; ".join(map(str, d["intents"]))

        # Split using permissive separators, validate against ALLOWED
        parts = [p.strip() for p in SEP_REGEX.split(raw_pi) if p.strip()] if raw_pi else []
        if not parts and isinstance(d.get("intents"), list):
            parts = [str(p).strip() for p in d["intents"] if str(p).strip()]

        if not parts:
            # No valid intents -> default
            return DEFAULT_CONTEXT.copy()

        # Validate intents are within ALLOWED
        if any(p not in ALLOWED for p in parts):
            raise ValueError(f"Disallowed intent(s) in parsed_intent: {parts}")

        # Normalize
        intents = parts
        address = d.get("address", None)
        ambigious = bool(d.get("ambigious", True))
        parsed_intent = " ; ".join(intents)  # keep your format with spaces around semicolons

        return {
            "intents": intents,
            "address": address,
            "ambigious": ambigious,
            "parsed_intent": parsed_intent,
        }
