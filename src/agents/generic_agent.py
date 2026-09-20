from src.azure_openai_v1 import azure_v1_base_url
from src.agents.completion_options import completion_options
import os
import logging
import json
import re
from openai import OpenAI, APIConnectionError, RateLimitError, APIStatusError
from typing import Any, List, Dict, Optional, Union
from src.database.vector_client import VectorClient , VectorClientConfig
from src.services.source_link_registry import resolve_source_links
from dotenv import load_dotenv
import time
from src.pipeline.telemetry import record_model_call
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

    # Simple street-number heuristic for addresses like "Main Street 12".
    return bool(re.search(r"\b[\wåäöÅÄÖ.-]+\s+\d+[A-Za-z]?(?:,\s*[\wåäöÅÄÖ\s.-]+)?\b", message or ""))


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
        r"which (?:one|of those)\b|why\b|how so\b|does that\b|is that\b)",
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
        
    def handle_generic_input(self, last_message: str, message_list: List[Dict[str, Union[str, int]]]) -> Optional[Dict[str, Any]]:
        if not isinstance(last_message, str) or not isinstance(message_list, list):
            logger.warning("Generic input must contain a string and a message list.")
            return None
        start_time = time.time()  
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
            {"role": "system", "content": self.prompt_template}
        ]
        if results:
            references = [
                {"Source": item.get("source", ""), "Content": item.get("page_content", "")}
                for item in results if isinstance(item, dict)
            ]
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
            model_started_at = time.perf_counter()
            completion = self.client.chat.completions.create(
                model=self.deployment,
                messages=messages_for_api,
                **completion_options(self.deployment, temperature=0.2),
                stream=False
            )
            response_content = completion.choices[0].message.content
            record_model_call(
                component="generic_agent",
                model=self.deployment,
                input_messages=messages_for_api,
                output_text=response_content,
                response=completion,
                latency_seconds=time.perf_counter() - model_started_at,
                success=True,
            )
            end_time = time.time()
            print(f"Generic Agent responded in {end_time - start_time} seconds")
            logger.info("Successfully received response from Azure OpenAI.")
            logger.debug(f"Received content: '{response_content[:100]}...'")
            return {
                "content": response_content,
                "sources": resolve_source_links(raw_sources),
            }
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
            return {"content": "Error: Unable to connect to the AI service. Please check your network connection.", "sources": []}
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
            return {"content": "Error: The AI service is currently busy. Please try again shortly.", "sources": []}
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
            return {"content": f"Error: An issue occurred with the AI service. Status code: {e.status_code}", "sources": []}
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
            return {"content": "Error: An unexpected error occurred while processing your request.", "sources": []}
