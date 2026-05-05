import json
import logging
import os
import time
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from openai import AzureOpenAI

from src.agents.base_agent import BaseAgent


load_dotenv()

logger = logging.getLogger(__name__)


class EvaluatorAgent(BaseAgent):
    """LLM-as-judge agent that evaluates answer faithfulness and completeness.

    The evaluator is intentionally fail-open: any model or parsing failure returns
    a "pass" verdict so user-facing responses are never blocked by this component.
    """

    DEFAULT_PROMPT_FILENAME = "evaluator_prompt.txt"
    PROMPTS_DIR_RELATIVE = os.path.join("..", "prompts")

    def __init__(self, prompt_path: str | None = None):
        """Load prompt template and initialize Azure OpenAI client.

        Prompt resolution (highest priority first):
        1. Explicit `prompt_path` argument.
        2. `EVALUATOR_PROMPT_VERSION` env var. If absolute, used as-is. Otherwise
           treated as a filename within the prompts directory (e.g. "evaluator_prompt_v2.txt").
        3. Default: "evaluator_prompt.txt" in the prompts directory.

        The resolved path is stored on `self.prompt_path` so callers/tracers can
        record exactly which file was loaded.
        """
        self.prompt_path = self._resolve_prompt_path(prompt_path)
        self.prompt_template = self._load_prompt(self.prompt_path)
        self._initialize_openai_client()

    def _resolve_prompt_path(self, path: str | None) -> str:
        """Resolve the prompt file path. See __init__ docstring for resolution order."""
        if path is not None:
            return path

        prompts_dir = os.path.join(os.path.dirname(__file__), self.PROMPTS_DIR_RELATIVE)
        env_value = os.getenv("EVALUATOR_PROMPT_VERSION")

        if env_value:
            if os.path.isabs(env_value):
                return env_value
            return os.path.join(prompts_dir, env_value)

        return os.path.join(prompts_dir, self.DEFAULT_PROMPT_FILENAME)

    def _load_prompt(self, full_path: str) -> str:
        """Load evaluator system prompt from a fully-resolved path."""
        try:
            with open(full_path, "r", encoding="utf-8") as file:
                return file.read()
        except FileNotFoundError as e:
            raise ValueError(f"Prompt file not found at: {full_path}") from e
        except OSError as e:
            raise RuntimeError(f"Error reading prompt file at: {full_path}") from e

    def _initialize_openai_client(self) -> None:
        """Initialize Azure OpenAI client with evaluator-specific env fallbacks."""
        endpoint = os.getenv("AZURE_ENDPOINT")
        deployment = os.getenv("EVALUATOR_MODEL_DEPLOYMENT_NAME") or os.getenv(
            "OPENAI_RESPONSE_MODEL_DEPLOYMENT_NAME"
        )
        api_key = os.getenv("OPENAI_API_KEY")
        api_version = os.getenv("EVALUATOR_MODEL_API_VERSION") or os.getenv(
            "OPENAI_RESPONSE_MODEL_API_VERSION"
        )

        missing = []
        if not endpoint:
            missing.append("AZURE_ENDPOINT")
        if not deployment:
            missing.append("EVALUATOR_MODEL_DEPLOYMENT_NAME|OPENAI_RESPONSE_MODEL_DEPLOYMENT_NAME")
        if not api_key:
            missing.append("OPENAI_API_KEY")
        if not api_version:
            missing.append("EVALUATOR_MODEL_API_VERSION|OPENAI_RESPONSE_MODEL_API_VERSION")

        if missing:
            raise ValueError(f"Missing required environment variable(s): {', '.join(missing)}")

        self.endpoint = endpoint
        self.deployment = deployment
        self.api_key = api_key
        self.api_version = api_version

        try:
            self.client = AzureOpenAI(
                azure_endpoint=self.endpoint,
                api_key=self.api_key,
                api_version=self.api_version,
            )
        except Exception as e:
            raise RuntimeError("Failed to initialize evaluator Azure OpenAI client") from e

    def _safe_parse_json(self, content: str) -> Dict[str, Any]:
        """Parse model output into a JSON object, tolerating light output noise."""
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Empty evaluator response")

        # Try direct parse first.
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        # Fallback: extract first JSON object from noisy output.
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("No JSON object found in evaluator response")

        candidate = content[start : end + 1]
        parsed = json.loads(candidate)
        if not isinstance(parsed, dict):
            raise ValueError("Evaluator response JSON is not an object")
        return parsed

    def _is_temperature_unsupported_error(self, error: Exception) -> bool:
        """Detect model families that reject non-default temperature settings."""
        # Prefer structured SDK attributes — more reliable than string matching
        if getattr(error, "param", None) == "temperature":
            return True
        if getattr(error, "code", None) == "unsupported_value" and getattr(error, "status_code", None) == 400:
            return "temperature" in str(error).lower()
        # Fallback: string matching for edge cases or older SDK versions
        msg = str(error).lower()
        return (
            "temperature" in msg
            and (
                "unsupported value" in msg
                or "does not support" in msg
                or "default (1)" in msg
            )
        )

    def _extract_token_usage(self, response) -> Optional[Dict[str, int]]:
        """Coerce the OpenAI usage object into a plain dict, defensively."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        try:
            return {
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            }
        except (TypeError, ValueError):
            return None

    def evaluate(self, question: str, aggregated_data: Dict[str, Any], answer: str) -> Dict[str, Any]:
        """Run evaluation and return structured verdict dict.

        On any failure, returns {"verdict": "pass"} to keep the main response path available.
        The result dict always includes `_latency_ms` (int, this call's wall time) and
        `_token_usage` (dict or None) for trace capture per Section 0.4 of plan-eil-v1.md.
        """
        start_time = time.time()
        payload = {
            "question": question or "",
            "aggregated_data": aggregated_data or {},
            "answer": answer or "",
        }

        messages = [
            {"role": "system", "content": self.prompt_template},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

        try:
            params = {
                "model": self.deployment,
                "messages": messages,
                "temperature": 0.0,  # Deterministic judging: same answer must yield the same verdict.
            }

            try:
                response = self.client.chat.completions.create(**params)
            except Exception as e:
                if not self._is_temperature_unsupported_error(e):
                    raise
                # Some Azure deployments (e.g. o-series) reject explicit temperature; fall back to model default.
                logger.warning(
                    "Evaluator model rejected temperature=0.0; retrying with model default. "
                    "Verdicts on this deployment may not be deterministic."
                )
                params.pop("temperature", None)
                response = self.client.chat.completions.create(**params)

            token_usage = self._extract_token_usage(response)
            content = (response.choices[0].message.content or "").strip()
            parsed = self._safe_parse_json(content)
            if isinstance(parsed, dict):
                parsed.setdefault("evaluator_failed", False)
                parsed.setdefault("evaluator_failure_reason", "")
                parsed["_latency_ms"] = int((time.time() - start_time) * 1000)
                parsed["_token_usage"] = token_usage
            return parsed
        except Exception as e:
            logger.warning("Evaluator failed; fail-open pass used. Reason: %s", e)
            return {
                "verdict": "pass",
                "evaluator_failed": True,
                "evaluator_failure_reason": str(e),
                "_latency_ms": int((time.time() - start_time) * 1000),
                "_token_usage": None,
            }

    def run(self, user_input: Dict[str, Any], thread_context: Dict[str, Any]) -> Dict[str, Any]:
        """BaseAgent-compatible wrapper around evaluate()."""
        question = (user_input or {}).get("question", "")
        aggregated_data = (user_input or {}).get("aggregated_data", {})
        answer = (user_input or {}).get("answer", "")
        return self.evaluate(question=question, aggregated_data=aggregated_data, answer=answer)
