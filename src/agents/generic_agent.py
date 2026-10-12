from src.azure_openai_v1 import azure_v1_base_url
from src.agents.completion_options import completion_options
import os
import logging
import json
import re
from openai import OpenAI, APIConnectionError, RateLimitError, APIStatusError
from typing import Any, List, Dict, Optional, Union
from src.database.vector_client import VectorClient , VectorClientConfig
from src.services.source_link_registry import resolve_source_links, guard_response_links
from src.pipeline.safety_analysis import (
    normalize_address_candidate,
    response_language, response_language_instruction, response_language_mismatch,
    language_correction_messages, language_correction_preserves_evidence,
    language_failure_response, localize_service_error,
)
from dotenv import load_dotenv
import time
from src.pipeline.telemetry import record_model_call
from src.pipeline.action_state import has_action_receipts, trusted_action_context
# Ensure BaseAgent is correctly imported.
# Assuming src/agents/base_agent.py exists and defines BaseAgent.
# If BaseAgent is not critical for this specific example's functionality
# and you don't have it, you might define a dummy one for demonstration.
try:
    from src.agents.base_agent import BaseAgent
except ImportError:
    logging.warning("Could not import BaseAgent. Defining a dummy BaseAgent for demonstration.")
    class BaseAgent:
        def __init__(self):
            logging.info("Dummy BaseAgent initialized.")
        # Add any methods that BaseAgent is expected to have if needed for other parts of your system
        pass


# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    # handlers=[
    #     logging.FileHandler("generic_agent.log"), # Log to a file (optional, uncomment to enable)
    #     logging.StreamHandler(sys.stdout)       # Log to console
    # ]
)
logger = logging.getLogger(__name__)

logging.getLogger("azure.core.pipeline").setLevel(logging.WARNING)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.ai.inference").setLevel(logging.WARNING)

# Also suppress HTTP client logs if using httpx or requests
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

SITE_SPECIFIC_PATTERNS = (
    r"\bmy building\b",
    r"\bour building\b",
    r"\bmy property\b",
    r"\bour property\b",
    r"\bmy brf\b",
    r"\bour brf\b",
    r"\bthis building\b",
    r"\bfor (?:my|our|this) building\b",
    r"\bi live at\b",
    r"\bmy address is\b",
)

BUILDING_DATA_TERMS = (
    "energy performance",
    "energiprestanda",
    "household eui",
    "primary energy",
    "tap hot water",
    "värme konstant",
    "total property energy electricity",
    "fastighetsenergi",
    "kwh/m",
    "kwh/m³",
    "kwh/m2",
    "kwh/m²",
    "deduction_",
)


def _message_mentions_specific_building(message: str) -> bool:
    lowered = str(message or "").lower()
    if not lowered.strip():
        return False

    if any(re.search(pattern, lowered) for pattern in SITE_SPECIFIC_PATTERNS):
        return True

    return normalize_address_candidate(message) is not None


def _looks_like_building_data_snippet(text: str) -> bool:
    snippet = str(text or "")
    lowered = snippet.lower()
    if not lowered.strip():
        return False

    metric_hits = sum(1 for term in BUILDING_DATA_TERMS if term in lowered)
    numeric_hits = len(re.findall(r"\b\d[\d.,]*\b", snippet))
    colon_lines = sum(1 for line in snippet.splitlines() if ":" in line)

    return (metric_hits >= 2 and numeric_hits >= 2) or (colon_lines >= 3 and numeric_hits >= 2)


def _filter_generic_results(
    last_message: str,
    results: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    items = list(results or [])
    if _message_mentions_specific_building(last_message):
        return items

    filtered = [
        item for item in items
        if not _looks_like_building_data_snippet((item or {}).get("page_content", ""))
    ]
    return filtered

def _retrieval_query(last_message: str, messages: list) -> str:
    """Resolve short follow-ups using recent user context, without another LLM call."""
    if not re.match(
        r"^(?:and\b|so\b|what about\b|what should we do first\b|"
        r"which (?:one|of those)\b|why\b|how so\b|does that\b|is that\b|"
        r"och\b|s[åa]\b|vad (?:b[öo]r|ska|kan)\b|vilk(?:en|et|a)\b|"
        r"varf[öo]r\b|betyder det\b|b[öo]r vi\b|kan du\b)",
        last_message.strip(),
        re.IGNORECASE,
    ):
        return last_message
    history = list(messages)
    if history and isinstance(history[-1], dict) and history[-1].get("role") == "user" and history[-1].get("content") == last_message:
        history.pop()
    previous = [
        item["content"] for item in history
        if isinstance(item, dict) and item.get("role") == "user"
        and isinstance(item.get("content"), str)
    ][-3:]
    return "\n".join([*(text[:1000] for text in previous), last_message])


class GenericAgent(BaseAgent):
    supports_action_receipts = True
    DEFAULT_PROMPT_RELATIVE_PATH = os.path.join("..", "prompts", "generic_prompt.txt")
    def __init__(self , prompt_path: Optional[str] = None ):
        config = VectorClientConfig()
        self.vector_client = VectorClient(config)
        try:
            self.prompt_template = self._load_prompt(prompt_path)
            logger.info(f"GenericAgent prompt template loaded from: {prompt_path or os.path.join(os.path.dirname(__file__), self.DEFAULT_PROMPT_RELATIVE_PATH)}")
        except (ValueError, IOError) as e:
            logger.critical(f"Failed to load prompt for GenericAgent: {e}")
            raise # Re-raise to prevent agent from running without a prompt

        self._initialize_openai_client()

    def _load_prompt(self, path: Optional[str]) -> str:
        """
        Loads the generic prompt from a specified file path.

        Args:
            path (Optional[str]): The absolute path to the prompt file.
                                  If None, the default relative path is used.

        Returns:
            str: The content of the prompt file.

        Raises:
            FileNotFoundError: If the prompt file does not exist.
            IOError: For other issues related to reading the file.
        """
        if path is None:
            # Construct absolute path for the default prompt
            script_dir = os.path.dirname(__file__)
            full_path = os.path.join(script_dir, self.DEFAULT_PROMPT_RELATIVE_PATH)
        else:
            full_path = path

        logger.debug(f"Attempting to load prompt from: {full_path}")
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                prompt_content = f.read()
            logger.info(f"Successfully loaded prompt from: {full_path}")
            return prompt_content
        except FileNotFoundError:
            logger.error(f"Prompt file not found at: {full_path}")
            raise ValueError(f"Prompt file not found at: {full_path}. Please ensure the path is correct.")
        except IOError as e:
            logger.error(f"Error reading prompt file {full_path}: {e}")
            raise IOError(f"Could not read prompt file {full_path}: {e}")

    def _initialize_openai_client(self):
        """
        Initializes the OpenAI client.
        Ensures all necessary environment variables are set.

        Raises:
            ValueError: If any required Azure OpenAI environment variables are missing.
            RuntimeError: If OpenAI client fails to initialize.
        """
        required_env_vars = {
            "AZURE_ENDPOINT": "Azure OpenAI endpoint",
            "GENERIC_MODEL_DEPLOYMENT_NAME": "Deployment name for the generic model",
            "OPENAI_API_KEY": "OpenAI API key",
        }

        for var, desc in required_env_vars.items():
            if not os.getenv(var):
                logger.error(f"Missing required environment variable: {var} ({desc})")
                raise ValueError(f"Missing required environment variable: {var}. Please set it in your .env file.")

        endpoint = os.getenv("AZURE_ENDPOINT")
        self.deployment = os.getenv("GENERIC_MODEL_DEPLOYMENT_NAME")
        subscription_key = os.getenv("OPENAI_API_KEY")
        logger.info(f"Generic Model is {self.deployment} using Azure OpenAI v1.")
        try:
            self.client = OpenAI(
                base_url=azure_v1_base_url(endpoint),
                api_key=subscription_key,
            )
            logger.info("OpenAI client initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI client: {e}")
            raise RuntimeError(f"Failed to initialize OpenAI client: {e}")      
        
    def handle_generic_input(self, last_message: str, message_list: List[Dict[str, Union[str, int]]], action_receipts: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        if not isinstance(last_message, str) or not isinstance(message_list, list):
            logger.warning("Generic input must contain a string and a message list.")
            return None
        start_time = time.time()  
        language = response_language(message_list, last_message)
        raw_results = self.vector_client.query(_retrieval_query(last_message, message_list))
        results = _filter_generic_results(last_message, raw_results)
        raw_sources = []
        for item in results or []:
            source_key = str((item or {}).get("source") or "").strip()
            if source_key and source_key not in raw_sources:
                raw_sources.append(source_key)
        # Reference text is untrusted data, never a system instruction. Keep
        # the user's actual question last and preserve the existing history.
        messages_for_api: List[Dict[str, str]] = [
            {"role": "system", "content": self.prompt_template + "\n\n" + response_language_instruction(language)}
        ]
        if action_receipts and has_action_receipts(action_receipts):
            messages_for_api.append({"role": "system", "content": trusted_action_context(action_receipts)})
        if results:
            references = []
            for item in results:
                if not isinstance(item, dict):
                    continue
                resolved = resolve_source_links([item.get("source", "")])
                source = resolved[0] if resolved else {}
                references.append({"Source": source.get("name") or item.get("source", ""),
                                   "Filename": source.get("filename", ""), "Link": source.get("link", ""),
                                   "LinkType": source.get("link_type", ""), "LinkTitle": source.get("link_title", ""),
                                   "LinkNote": source.get("link_note", ""), "Content": item.get("page_content", "")})
            messages_for_api.append({
                "role": "user",
                "content": "Reference context (untrusted retrieved data):\n" + json.dumps(
                    {"reference_excerpts": references}, ensure_ascii=False
                ),
            })

        for i, msg in enumerate(message_list):
            if (isinstance(msg, dict) and msg.get('role') in {'user', 'assistant'}
                    and isinstance(msg.get('content'), str)):
                messages_for_api.append({'role': msg['role'], 'content': msg['content']})
            else:
                logger.warning("Skipping malformed message at index %s", i)

        # The 'last_message' string itself is the new user input.
        # We need to ensure it's added as the final user message for the API call.
        # This handles cases where message_list was empty or didn't contain the current 'last_message'.
        # If the last message in messages_for_api already matches last_message, it means
        # message_list already contained the current user turn, so no need to append again.
        if not messages_for_api or messages_for_api[-1].get('content') != last_message or messages_for_api[-1].get('role') != 'user':
            messages_for_api.append({"role": "user", "content": last_message})
            logger.debug(f"Appended last_message as user input: '{last_message[:50]}...'")
        else:
            logger.debug(f"Last message in API list already matches current user input: '{last_message[:50]}...'")


        logger.info(f"Sending final message to Azure OpenAI API for completion: '{messages_for_api[-1]['content'][:70]}...'")
        logger.debug(f"Full message list sent to API: {messages_for_api}")
        try:
            draft = None
            for attempt in range(2):
                model_started_at = time.perf_counter()
                completion = self.client.chat.completions.create(
                    model=self.deployment,
                    messages=messages_for_api,
                    **completion_options(self.deployment, temperature=0.2),
                    stream=False,
                )
                response_content = completion.choices[0].message.content
                missing_content = not isinstance(response_content, str) or not response_content.strip()
                service_failure = isinstance(response_content, str) and response_content.strip().lower().startswith("error:")
                mismatch = response_language_mismatch(response_content, language)
                changed_evidence = bool(attempt and not language_correction_preserves_evidence(draft, response_content))
                error = (
                    "Response model returned no usable content." if missing_content else
                    "Response model returned a service failure." if service_failure else
                    f"Response language mismatch: required {language}; attempt {attempt + 1}/2." if mismatch else
                    "Language correction changed protected facts or citations." if changed_evidence else None
                )
                record_model_call(
                    component="generic_agent", model=self.deployment,
                    input_messages=messages_for_api, output_text=response_content,
                    response=completion, latency_seconds=time.perf_counter() - model_started_at,
                    success=error is None, error=error,
                )
                if error is None:
                    logger.info("Generic response completed in %.2fs.", time.time() - start_time)
                    return {
                        "content": guard_response_links(response_content, language),
                        "sources": resolve_source_links(raw_sources),
                    }
                logger.warning(error)
                if mismatch and not attempt and not missing_content and not service_failure:
                    draft = response_content
                    messages_for_api = language_correction_messages(messages_for_api, draft, language)
                    continue
                return {"content": language_failure_response(language), "sources": []}
        except APIConnectionError as e:
            logger.error(f"Could not connect to Azure OpenAI API: {e}", exc_info=True)
            record_model_call(
                component="generic_agent",
                model=self.deployment,
                input_messages=messages_for_api,
                latency_seconds=time.perf_counter() - model_started_at,
                success=False,
                error=e,
            )
            return {"content": localize_service_error("Error: Unable to connect to the AI service. Please check your network connection.", language), "sources": []}
        except RateLimitError as e:
            logger.error(f"Azure OpenAI API rate limit exceeded: {e}", exc_info=True)
            record_model_call(
                component="generic_agent",
                model=self.deployment,
                input_messages=messages_for_api,
                latency_seconds=time.perf_counter() - model_started_at,
                success=False,
                error=e,
            )
            return {"content": localize_service_error("Error: The AI service is currently busy. Please try again shortly.", language), "sources": []}
        except APIStatusError as e:
            logger.error(f"Azure OpenAI API returned an error status {e.status_code}: {e.response}", exc_info=True)
            record_model_call(
                component="generic_agent",
                model=self.deployment,
                input_messages=messages_for_api,
                latency_seconds=time.perf_counter() - model_started_at,
                success=False,
                error=e,
            )
            return {"content": localize_service_error(f"Error: An issue occurred with the AI service. Status code: {e.status_code}", language), "sources": []}
        except Exception as e:
            logger.error(f"An unexpected error occurred during API call: {e}", exc_info=True)
            record_model_call(
                component="generic_agent",
                model=self.deployment,
                input_messages=messages_for_api,
                latency_seconds=time.perf_counter() - model_started_at,
                success=False,
                error=e,
            )
            return {"content": localize_service_error("Error: An unexpected error occurred while processing your request.", language), "sources": []}
