import os
import logging
import time
from typing import List, Dict, Optional, Union
from openai import AzureOpenAI, APIConnectionError, RateLimitError, APIStatusError, BadRequestError
from dotenv import load_dotenv
from src.pipeline.telemetry import record_model_call

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class OpenAIResponseAgent:
    DEFAULT_PROMPT_RELATIVE_PATH = os.path.join("..", "prompts", "openai_response_prompt.txt")

    def __init__(self, prompt_path: Optional[str] = None):
        logger.info("Initializing OpenAIResponseAgent.")
        self.prompt_template = self._load_prompt(prompt_path)
        self._initialize_openai_client()

    def _load_prompt(self, path: Optional[str]) -> str:
        if path is None:
            script_dir = os.path.dirname(__file__)
            full_path = os.path.join(script_dir, self.DEFAULT_PROMPT_RELATIVE_PATH)
        else:
            full_path = path

        try:
            with open(full_path, "r", encoding="utf-8") as file:
                prompt = file.read()
            logger.info(f"Prompt loaded successfully from: {full_path}")
            return prompt
        except FileNotFoundError:
            logger.error(f"Prompt file not found at: {full_path}")
            raise ValueError(f"Prompt file not found at: {full_path}.")
        except IOError as e:
            logger.error(f"Error reading prompt file: {e}")
            raise

    def _initialize_openai_client(self):
        required_env_vars = {
            "AZURE_ENDPOINT": "Azure OpenAI endpoint",
            "OPENAI_RESPONSE_MODEL_DEPLOYMENT_NAME": "Deployment name for response model",
            "OPENAI_API_KEY": "OpenAI API key",
            "OPENAI_RESPONSE_MODEL_API_VERSION": "API version for the model"
        }

        for var, desc in required_env_vars.items():
            if not os.getenv(var):
                logger.error(f"Missing required environment variable: {var} ({desc})")
                raise ValueError(f"Missing required environment variable: {var}")

        self.endpoint = os.getenv("AZURE_ENDPOINT")
        self.deployment = os.getenv("OPENAI_RESPONSE_MODEL_DEPLOYMENT_NAME")
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.api_version = os.getenv("OPENAI_RESPONSE_MODEL_API_VERSION")

        try:
            self.client = AzureOpenAI(
                azure_endpoint=self.endpoint,
                api_key=self.api_key,
                api_version=self.api_version
            )
            logger.info("Azure OpenAI client initialized successfully for response agent.")
        except Exception as e:
            logger.error(f"Failed to initialize Azure OpenAI client: {e}")
            raise RuntimeError("Failed to initialize OpenAI client.")

    def _is_o4_family(self) -> bool:
        """Return True if the deployment is an o3/o4-series model.

        o3/o4 models use `max_completion_tokens` instead of `max_tokens`, and
        reject sampling parameters like `top_p`, `frequency_penalty`, and
        `presence_penalty` with a 400 error. Callers use this flag to send the
        right parameter set for the deployed model family.
        """
        name = (self.deployment or "").lower()
        return ("o4" in name) or ("o3" in name)

    def _build_messages(self, last_message: str, message_list: List[Dict[str, Union[str, int]]]):
        messages = [{"role": "system", "content": self.prompt_template}]
        for msg in (message_list or []):
            role = msg.get("role")
            content = msg.get("content")
            if isinstance(role, str) and isinstance(content, str):
                messages.append({"role": role, "content": content})
            else:
                logger.warning(f"Skipping malformed message: {msg!r}")
        # ensure we have a proper user turn at the end
        if not messages or messages[-1]["role"] != "user" or messages[-1]["content"] != last_message:
            messages.append({"role": "user", "content": last_message})
        return messages

    def _extract_token_usage(self, response) -> Optional[Dict[str, int]]:
        """Convert the Azure SDK usage object into a plain JSON-serializable dict.

        The SDK returns a `CompletionUsage` object (not a dict), and some attributes
        can be None when the API omits them. We coerce everything to int with a 0
        fallback so the trace writer never sees non-serializable types.
        Returns None if the response has no usage attribute at all.
        """
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

    def _get_summarizer_temperature(self) -> float:
        """Read the SUMMARIZER_TEMPERATURE env var.

        Default is 0.2 (production tone). Experiment runs should pin this to 0.0
        via the env var to minimize stochastic variance across arms.
        Invalid values fall back to the default with a warning.
        """
        raw = os.getenv("SUMMARIZER_TEMPERATURE")
        if not raw:
            return 0.2
        try:
            return float(raw)
        except ValueError:
            logger.warning(
                "Invalid SUMMARIZER_TEMPERATURE=%r; falling back to 0.2.", raw
            )
            return 0.2

    def _is_temperature_unsupported_error(self, error: Exception) -> bool:
        """Detect Azure deployments (typically o-series) that reject explicit temperature."""
        if getattr(error, "param", None) == "temperature":
            return True
        if (
            getattr(error, "code", None) == "unsupported_value"
            and getattr(error, "status_code", None) == 400
        ):
            return "temperature" in str(error).lower()
        msg = str(error).lower()
        return (
            "temperature" in msg
            and (
                "unsupported value" in msg
                or "does not support" in msg
                or "default (1)" in msg
            )
        )

    def _record_call_meta(
        self,
        latency_ms: int,
        token_usage: Optional[Dict[str, int]],
        temperature_requested: Optional[float] = None,
        temperature_unsupported: bool = False,
    ) -> None:
        """Store per-call metadata so the calling node can read it after generate_response.

        Using a side-channel rather than changing the return type lets callers that
        don't need the metadata ignore it while callers that do (e.g. the summarizer
        node writing to the evaluation trace) can read latency, token usage, and the
        temperature provenance without any API change.
        """
        self._last_call_meta = {
            "latency_ms": int(latency_ms),
            "token_usage": token_usage,
            "temperature_requested": temperature_requested,
            "temperature_unsupported": temperature_unsupported,
        }

    def generate_response(self, last_message: str, message_list: List[Dict[str, Union[str, int]]]) -> Optional[str]:
        start_time = time.time()
        temperature = self._get_summarizer_temperature()
        temperature_unsupported = False
        started_perf = time.perf_counter()

        # Reset side-channel meta so callers always see THIS call's data, never stale.
        self._last_call_meta = {
            "latency_ms": None,
            "token_usage": None,
            "temperature_requested": temperature,
            "temperature_unsupported": False,
        }

        # Callers sometimes pass None or non-string types from upstream graph nodes.
        # Normalizing here avoids a confusing TypeError deep inside the API call.
        if not isinstance(last_message, str):
            last_message = "" if last_message is None else str(last_message)
        if not isinstance(message_list, list):
            message_list = []

        messages = self._build_messages(last_message, message_list)
        # Temperature is set unconditionally for ALL model families here,
        # then the BadRequestError handler below removes it for deployments that
        # reject the override (o-series). This makes the env var honored uniformly.
        params = {
            "model": self.deployment,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
        }
        if self._is_o4_family():
            params["max_completion_tokens"] = 4000
        else:
            params["max_tokens"] = 800
            params["top_p"] = 0.23
            params["frequency_penalty"] = 0
            params["presence_penalty"] = 0

        try:
            model_started_at = time.perf_counter()
            response = self.client.chat.completions.create(**params)
        except BadRequestError as e:
            # Auto-recover common param mismatch: swap max_tokens -> max_completion_tokens.
            msg = str(e)
            if "max_tokens" in msg and "max_completion_tokens" in msg:
                logger.warning("Retrying with max_completion_tokens for o4/o3 model.")
                params.pop("max_tokens", None)
                params["max_completion_tokens"] = 800
                # o4/o3 models reject top_p, frequency_penalty, and presence_penalty
                # with a 400 error; strip them before retrying.
                for k in ("top_p", "frequency_penalty", "presence_penalty"):
                    params.pop(k, None)
                model_started_at = time.perf_counter()
                try:
                    response = self.client.chat.completions.create(**params)
                except Exception as retry_exc:
                    logger.error(f"Retry after BadRequestError failed: {retry_exc}", exc_info=True)
                    record_model_call(
                        component="openai_response_agent",
                        model=self.deployment,
                        input_messages=messages,
                        latency_seconds=time.perf_counter() - model_started_at,
                        success=False,
                        error=retry_exc,
                    )
                    return "Error: An unexpected issue occurred."
            elif self._is_temperature_unsupported_error(e):
                # o-series deployments reject explicit temperature — retry without it.
                logger.warning(
                    "Summarizer model rejected temperature=%s; retrying without. "
                    "This run is NOT fully deterministic (model default applies).",
                    temperature,
                )
                params.pop("temperature", None)
                temperature_unsupported = True
                response = self.client.chat.completions.create(**params)
            else:
                self._record_call_meta(
                    int((time.time() - start_time) * 1000),
                    None,
                    temperature_requested=temperature,
                    temperature_unsupported=temperature_unsupported,
                )
                logger.error(f"BadRequestError: {e}", exc_info=True)
                record_model_call(
                    component="openai_response_agent",
                    model=self.deployment,
                    input_messages=messages,
                    latency_seconds=time.perf_counter() - started_perf,
                    success=False,
                    error=e,
                )
                return f"Error: {getattr(e, 'message', str(e)) or 'Bad request'}"
        except APIConnectionError as e:
            self._record_call_meta(
                int((time.time() - start_time) * 1000), None,
                temperature_requested=temperature, temperature_unsupported=temperature_unsupported,
            )
            logger.error(f"Connection error: {e}", exc_info=True)
            record_model_call(
                component="openai_response_agent",
                model=self.deployment,
                input_messages=messages,
                latency_seconds=time.perf_counter() - started_perf,
                success=False,
                error=e,
            )
            return "Error: Cannot connect to AI service."
        except RateLimitError as e:
            self._record_call_meta(
                int((time.time() - start_time) * 1000), None,
                temperature_requested=temperature, temperature_unsupported=temperature_unsupported,
            )
            logger.error(f"Rate limit exceeded: {e}", exc_info=True)
            record_model_call(
                component="openai_response_agent",
                model=self.deployment,
                input_messages=messages,
                latency_seconds=time.perf_counter() - started_perf,
                success=False,
                error=e,
            )
            return "Error: Rate limit exceeded. Please retry shortly."
        except APIStatusError as e:
            self._record_call_meta(
                int((time.time() - start_time) * 1000), None,
                temperature_requested=temperature, temperature_unsupported=temperature_unsupported,
            )
            logger.error(f"API status error: {e.status_code} - {e.response}", exc_info=True)
            record_model_call(
                component="openai_response_agent",
                model=self.deployment,
                input_messages=messages,
                latency_seconds=time.perf_counter() - started_perf,
                success=False,
                error=e,
            )
            return f"Error: API returned status code {e.status_code}."
        except Exception as e:
            self._record_call_meta(
                int((time.time() - start_time) * 1000), None,
                temperature_requested=temperature, temperature_unsupported=temperature_unsupported,
            )
            logger.error(f"Unexpected error: {e}", exc_info=True)
            record_model_call(
                component="openai_response_agent",
                model=self.deployment,
                input_messages=messages,
                latency_seconds=time.perf_counter() - started_perf,
                success=False,
                error=e,
            )
            return "Error: An unexpected issue occurred."

        try:
            response_content = response.choices[0].message.content
        except Exception:
            response_content = None

        latency_ms = int((time.time() - start_time) * 1000)
        token_usage = self._extract_token_usage(response)
        self._record_call_meta(
            latency_ms, token_usage,
            temperature_requested=temperature,
            temperature_unsupported=temperature_unsupported,
        )
        record_model_call(
            component="openai_response_agent",
            model=self.deployment,
            input_messages=messages,
            output_text=response_content,
            response=response,
            latency_seconds=time.perf_counter() - model_started_at,
            success=True,
        )
        logger.info(
            "Response generated in %d ms (tokens: %s, temperature_requested=%s, "
            "temperature_unsupported=%s).",
            latency_ms, token_usage, temperature, temperature_unsupported,
        )
        return response_content or "No content returned from the model."
