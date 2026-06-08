import os
import logging
import time
from typing import List, Dict, Optional, Union
from openai import AzureOpenAI, APIConnectionError, RateLimitError, APIStatusError, BadRequestError
from dotenv import load_dotenv
from src.pipeline.telemetry import record_model_call
from src.pipeline.response_language import (
    choose_language_text,
    language_instruction_for_message,
    response_language_for_message,
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

    def _build_messages(self, last_message: str, message_list: List[Dict[str, Union[str, int]]]):
        messages = [{"role": "system", "content": self.prompt_template}]
        messages.append(
            {
                "role": "system",
                "content": language_instruction_for_message(last_message, messages=message_list),
            }
        )
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

    def generate_response(self, last_message: str, message_list: List[Dict[str, Union[str, int]]]) -> Optional[str]:
        start_time = time.time()
        started_perf = time.perf_counter()

        # be forgiving about types
        if not isinstance(last_message, str):
            last_message = "" if last_message is None else str(last_message)
        if not isinstance(message_list, list):
            message_list = []

        response_language = response_language_for_message(last_message, messages=message_list)
        messages = self._build_messages(last_message, message_list)
        params = {"model": self.deployment, "messages": messages, "stream": False}
        if self._is_o4_family():
            params["max_completion_tokens"] = 4000
        else:
            params["max_tokens"] = 800
            params["temperature"] = 0.2
            params["top_p"] = 0.23
            params["frequency_penalty"] = 0
            params["presence_penalty"] = 0

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
                for k in ("top_p", "frequency_penalty", "presence_penalty"):
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
                    return choose_language_text(
                        response_language,
                        english="Error: An unexpected issue occurred.",
                        swedish="Fel: Ett oväntat problem uppstod.",
                    )
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
                return choose_language_text(
                    response_language,
                    english=f"Error: {getattr(e, 'message', str(e)) or 'Bad request'}",
                    swedish=f"Fel: {getattr(e, 'message', str(e)) or 'Felaktig begäran'}",
                )
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
            return choose_language_text(
                response_language,
                english="Error: Cannot connect to AI service.",
                swedish="Fel: Det går inte att ansluta till AI-tjänsten.",
            )
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
            return choose_language_text(
                response_language,
                english="Error: Rate limit exceeded. Please retry shortly.",
                swedish="Fel: Hastighetsgränsen överskreds. Försök igen strax.",
            )
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
            return choose_language_text(
                response_language,
                english=f"Error: API returned status code {e.status_code}.",
                swedish=f"Fel: API:t returnerade statuskod {e.status_code}.",
            )
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
            return choose_language_text(
                response_language,
                english="Error: An unexpected issue occurred.",
                swedish="Fel: Ett oväntat problem uppstod.",
            )

        try:
            
            response_content = response.choices[0].message.content
        except Exception:
            response_content = None

        end_time = time.time()
        logger.info(f"Response generated in {end_time - start_time:.2f} seconds.")
        record_model_call(
            component="openai_response_agent",
            model=self.deployment,
            input_messages=messages,
            output_text=response_content,
            response=response,
            latency_seconds=time.perf_counter() - model_started_at,
            success=True,
        )
        return response_content or choose_language_text(
            response_language,
            english="No content returned from the model.",
            swedish="Modellen returnerade inget innehåll.",
        )
