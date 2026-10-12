from src.azure_openai_v1 import azure_v1_base_url
from src.agents.completion_options import completion_options
import os
import logging
import time
from typing import List, Dict, Optional, Union
from openai import OpenAI, APIConnectionError, RateLimitError, APIStatusError, BadRequestError
from dotenv import load_dotenv
from src.pipeline.telemetry import record_model_call
from src.pipeline.safety_analysis import (
    response_language, response_language_instruction, response_language_mismatch,
    language_correction_messages, language_correction_preserves_evidence,
    language_failure_response,
)

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
        }

        for var, desc in required_env_vars.items():
            if not os.getenv(var):
                logger.error(f"Missing required environment variable: {var} ({desc})")
                raise ValueError(f"Missing required environment variable: {var}")

        self.endpoint = os.getenv("AZURE_ENDPOINT")
        self.deployment = os.getenv("OPENAI_RESPONSE_MODEL_DEPLOYMENT_NAME")
        self.api_key = os.getenv("OPENAI_API_KEY")

        try:
            self.client = OpenAI(
                base_url=azure_v1_base_url(self.endpoint),
                api_key=self.api_key,
            )
            logger.info("Azure OpenAI client initialized successfully for response agent.")
        except Exception as e:
            logger.error(f"Failed to initialize Azure OpenAI client: {e}")
            raise RuntimeError("Failed to initialize OpenAI client.")

    def _is_o4_family(self) -> bool:
        """Treat o3/o4 deployments with the new param names/limits."""
        name = (self.deployment or "").lower()
        return ("o4" in name) or ("o3" in name)

    def _retry_settings(self) -> tuple[int, float]:
        try:
            retries = int(os.getenv("OPENAI_RESPONSE_MAX_RETRIES", "2"))
        except (TypeError, ValueError):
            retries = 2
        try:
            base_seconds = float(os.getenv("OPENAI_RESPONSE_RETRY_BASE_SECONDS", "1.0"))
        except (TypeError, ValueError):
            base_seconds = 1.0
        return max(0, retries), max(0.0, base_seconds)

    def _create_completion_with_retries(self, params: Dict[str, object]):
        max_retries, base_seconds = self._retry_settings()
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                return self.client.chat.completions.create(**params)
            except RateLimitError as exc:
                last_error = exc
                if attempt >= max_retries:
                    raise
                sleep_for = base_seconds * (2 ** attempt)
                logger.warning(
                    "Response model rate limit hit; retrying in %.1fs (attempt %s/%s).",
                    sleep_for,
                    attempt + 1,
                    max_retries + 1,
                )
                if sleep_for > 0:
                    time.sleep(sleep_for)
        raise last_error

    def _build_messages(self, last_message: str, message_list: List[Dict[str, Union[str, int]]], *, language: Optional[str] = None):
        language = language if language in {"en", "sv"} else response_language(message_list, last_message)
        messages = [{"role": "system", "content": self.prompt_template + "\n\n" + response_language_instruction(language)}]
        for msg in (message_list or []):
            if not isinstance(msg, dict):
                logger.warning("Skipping malformed message.")
                continue
            role = msg.get("role")
            content = msg.get("content")
            if role in {"user", "assistant"} and isinstance(content, str):
                messages.append({"role": role, "content": content})
            else:
                logger.warning(f"Skipping malformed message: {msg!r}")
        # ensure we have a proper user turn at the end
        if not messages or messages[-1]["role"] != "user" or messages[-1]["content"] != last_message:
            messages.append({"role": "user", "content": last_message})
        return messages

    def generate_response(self, last_message: str, message_list: List[Dict[str, Union[str, int]]], *, language: Optional[str] = None) -> Optional[str]:
        """Generate in the chosen language, with at most one faithful correction."""
        if not isinstance(last_message, str):
            last_message = "" if last_message is None else str(last_message)
        if not isinstance(message_list, list):
            message_list = []
        language = language if language in {"en", "sv"} else response_language(message_list, last_message)
        messages = self._build_messages(last_message, message_list, language=language)
        answer = self._generate_response_once(last_message, message_list, language=language, prepared_messages=messages)
        if not isinstance(answer, str) or answer.strip().lower().startswith("error:") or not response_language_mismatch(answer, language):
            return answer
        corrected = self._generate_response_once(
            last_message, message_list, language=language,
            prepared_messages=language_correction_messages(messages, answer, language),
            correction_source=answer,
        )
        if (
            not isinstance(corrected, str) or corrected.strip().lower().startswith("error:")
            or response_language_mismatch(corrected, language)
            or not language_correction_preserves_evidence(answer, corrected)
        ):
            return "Error: " + language_failure_response(language)
        return corrected

    def _generate_response_once(self, last_message: str, message_list: List[Dict[str, Union[str, int]]], *, language: str, prepared_messages=None, correction_source=None) -> Optional[str]:
        start_time = time.time()
        started_perf = time.perf_counter()

        # be forgiving about types
        if not isinstance(last_message, str):
            last_message = "" if last_message is None else str(last_message)
        if not isinstance(message_list, list):
            message_list = []

        messages = prepared_messages if prepared_messages is not None else self._build_messages(last_message, message_list, language=language)
        params = {"model": self.deployment, "messages": messages, "stream": False}
        params.update(completion_options(self.deployment, reasoning_tokens=16384))

        try:
            model_started_at = time.perf_counter()
            response = self._create_completion_with_retries(params)
        except BadRequestError as e:
            # Auto-recover common param mismatch: swap max_tokens -> max_completion_tokens
            msg = str(e)
            if "max_tokens" in msg and "max_completion_tokens" in msg:
                logger.warning("Retrying with max_completion_tokens for o4/o3 model.")
                params.pop("max_tokens", None)
                params["max_completion_tokens"] = 800
                # Remove legacy sampling params that may cause 400 on o4/o3
                for k in ("temperature", "top_p", "frequency_penalty", "presence_penalty"):
                    params.pop(k, None)
                model_started_at = time.perf_counter()
                try:
                    response = self._create_completion_with_retries(params)
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
            else:
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

        end_time = time.time()
        logger.info(f"Response generated in {end_time - start_time:.2f} seconds.")
        missing_content = not isinstance(response_content, str) or not response_content.strip()
        service_failure = isinstance(response_content, str) and response_content.strip().lower().startswith("error:")
        mismatch = response_language_mismatch(response_content, language)
        changed_evidence = bool(correction_source is not None and not language_correction_preserves_evidence(correction_source, response_content))
        error = (
            "Response model returned no usable content." if missing_content else
            "Response model returned a service failure." if service_failure else
            f"Response language mismatch: required {language}." if mismatch else
            "Language correction changed protected facts or citations." if changed_evidence else None
        )
        record_model_call(
            component="openai_response_agent",
            model=self.deployment,
            input_messages=messages,
            output_text=response_content,
            response=response,
            latency_seconds=time.perf_counter() - model_started_at,
            success=error is None,
            error=error,
        )
        if error:
            logger.warning(error)
        return response_content if not missing_content else "Error: " + language_failure_response(language)
