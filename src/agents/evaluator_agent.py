import json
import logging
import os
from typing import Any, Dict

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

    DEFAULT_PROMPT_RELATIVE_PATH = os.path.join("..", "prompts", "evaluator_prompt.txt")

    def __init__(self, prompt_path: str | None = None):
        """Load prompt template and initialize Azure OpenAI client."""
        self.prompt_template = self._load_prompt(prompt_path)
        self._initialize_openai_client()

    def _load_prompt(self, path: str | None) -> str:
        """Load evaluator system prompt from disk."""
        if path is None:
            script_dir = os.path.dirname(__file__)
            full_path = os.path.join(script_dir, self.DEFAULT_PROMPT_RELATIVE_PATH)
        else:
            full_path = path

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
        msg = str(error).lower()
        return (
            "temperature" in msg
            and (
                "unsupported value" in msg
                or "does not support" in msg
                or "default (1)" in msg
            )
        )

    def evaluate(self, question: str, aggregated_data: Dict[str, Any], answer: str) -> Dict[str, Any]:
        """Run evaluation and return structured verdict dict.

        On any failure, returns {"verdict": "pass"} to keep the main response path available.
        """
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
                "temperature": 0.0,
            }

            try:
                response = self.client.chat.completions.create(**params)
            except Exception as e:
                if not self._is_temperature_unsupported_error(e):
                    raise
                # Some Azure deployments only allow the default temperature.
                logger.info("Evaluator model rejected temperature override; retrying without temperature.")
                params.pop("temperature", None)
                response = self.client.chat.completions.create(**params)

            content = (response.choices[0].message.content or "").strip()
            parsed = self._safe_parse_json(content)
            return parsed
        except Exception as e:
            logger.warning("Evaluator failed; fail-open pass used. Reason: %s", e)
            return {"verdict": "pass"}

    def run(self, user_input: Dict[str, Any], thread_context: Dict[str, Any]) -> Dict[str, Any]:
        """BaseAgent-compatible wrapper around evaluate()."""
        question = (user_input or {}).get("question", "")
        aggregated_data = (user_input or {}).get("aggregated_data", {})
        answer = (user_input or {}).get("answer", "")
        return self.evaluate(question=question, aggregated_data=aggregated_data, answer=answer)
