from src.agents.base_agent import BaseAgent
import os
import logging
from openai import AzureOpenAI, APIConnectionError, RateLimitError, APIStatusError
from typing import Dict, Optional, Union
from dotenv import load_dotenv
import multiprocessing
import sys # Import sys for checking if BaseAgent exists
import time
from src.pipeline.telemetry import record_model_call
# Load environment variables from .env file
load_dotenv()

# --- Logging Configuration ---
# You can adjust the logging level (INFO, DEBUG, WARNING, ERROR, CRITICAL)
# and the format as needed for your application.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    # handlers=[
    #     logging.FileHandler("router_agent.log"), # Log to a file
    #     logging.StreamHandler(sys.stdout)       # Log to console
    # ]
)
# Get a specific logger for this module
logger = logging.getLogger(__name__)
logging.getLogger("azure.core.pipeline").setLevel(logging.WARNING)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.ai.inference").setLevel(logging.WARNING)

# Also suppress HTTP client logs if using httpx or requests
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
# --- Global OpenAI Client Initialization ---
# It's generally better to encapsulate this within a class or function
# if you want to apply robust error handling to its initialization as well.
# For now, we'll add checks here.

# Validate environment variables before creating the client
required_env_vars = {
    "AZURE_ENDPOINT": "Azure OpenAI endpoint",
    "ROUTER_MODEL_DEPLOYMENT_NAME": "Deployment name for the router model",
    "OPENAI_API_KEY": "OpenAI API key",
    "ROUTER_MODEL_API_VERSION": "API version for the router model"
}

for var, desc in required_env_vars.items():
    if not os.getenv(var):
        logger.error(f"Missing required environment variable: {var} ({desc}). Exiting.")
        # In a real application, you might raise an exception or exit more gracefully.
        # For now, we'll set these to None and handle potential errors in client creation.
        globals()[var.lower()] = None # Set corresponding variable to None
        sys.exit(1) # Exit if critical variables are missing

endpoint = os.getenv("AZURE_ENDPOINT")
deployment = os.getenv("ROUTER_MODEL_DEPLOYMENT_NAME")
subscription_key = os.getenv("OPENAI_API_KEY")
api_version = os.getenv("ROUTER_MODEL_API_VERSION")
logger.info(f"Router Model is {deployment} and the api version is {api_version}. ")
client = None
try:
    client = AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=subscription_key,
        api_version=api_version,
    )
    logger.info("AzureOpenAI client for RouterAgent initialized successfully.")
except Exception as e:
    logger.critical(f"Failed to initialize AzureOpenAI client for RouterAgent: {e}")
    # Depending on your application's tolerance for this, you might exit or
    # ensure subsequent calls gracefully handle the missing client.
    sys.exit(1) # Exit if client cannot be initialized


def openai_call_worker(prompt_template: str, message: str, return_dict: Dict):
    """
    Worker function to make an OpenAI API call in a separate process.
    Results or errors are stored in a shared dictionary.
    """
    worker_logger = logging.getLogger(f"{__name__}.openai_call_worker")
    worker_logger.debug(f"OpenAI worker started for message: '{message[:50]}...'")
    try:
        if client is None:
            raise RuntimeError("OpenAI client not initialized in worker process.")

        messages = [
            {"role": "system", "content": prompt_template},
            {"role": "user", "content": message}
        ]
        worker_logger.debug(f"Messages sent to OpenAI API: {messages}")

        completion = client.chat.completions.create(
            model=deployment,
            messages=messages,
            max_tokens=800,
            temperature=0,
            top_p=0.23,
            frequency_penalty=0,
            presence_penalty=0,
            stop=None,
            stream=False
        )
        response_content = completion.choices[0].message.content
        return_dict["result"] = response_content
        worker_logger.info("OpenAI API call successful in worker.")
        worker_logger.debug(f"Received content: '{response_content[:100]}...'")
    except APIConnectionError as e:
        error_msg = f"API Connection Error: {e}"
        worker_logger.error(error_msg, exc_info=True)
        return_dict["error"] = error_msg
    except RateLimitError as e:
        error_msg = f"Rate Limit Error: {e}"
        worker_logger.warning(error_msg, exc_info=True)
        return_dict["error"] = error_msg
    except APIStatusError as e:
        error_msg = f"API Status Error {e.status_code}: {e.response}"
        worker_logger.error(error_msg, exc_info=True)
        return_dict["error"] = error_msg
    except Exception as e:
        error_msg = f"An unexpected error occurred in openai_call_worker: {e}"
        worker_logger.error(error_msg, exc_info=True)
        return_dict["error"] = error_msg


class RouterAgent(BaseAgent):
    """
    RouterAgent classifies incoming user questions to route them to appropriate handlers.
    It uses an Azure OpenAI model for classification, running the API call in a separate process
    to handle potential timeouts.
    """
    def __init__(self, prompt_path: Optional[str] = None):
        """
        Initializes the RouterAgent.

        Args:
            prompt_path (Optional[str]): Path to the classification prompt file.
                                         Defaults to 'classification_prompt.txt' in parent directory.
        """
        super().__init__() # Call the constructor of BaseAgent if it needs initialization
        logger.info("Initializing RouterAgent.")
        default_path = os.path.join(os.path.dirname(__file__), "..", "prompts", "classification_prompt.txt")
        try:
            self.prompt_template = self._load_prompt(prompt_path or default_path)
            logger.info(f"RouterAgent prompt template loaded from: {prompt_path or default_path}")
        except ValueError as e:
            logger.error(f"Failed to load prompt for RouterAgent: {e}")
            raise # Re-raise to prevent agent from running without a prompt
        except IOError as e:
            logger.error(f"IOError loading prompt for RouterAgent: {e}")
            raise


    def _load_prompt(self, path: str) -> str:
        """
        Loads the prompt content from the specified file path.

        Args:
            path (str): The file path to the prompt.

        Returns:
            str: The content of the prompt file.

        Raises:
            FileNotFoundError: If the prompt file does not exist.
            IOError: If there's an error reading the file.
        """
        logger.debug(f"Attempting to load prompt from: {path}")
        try:
            with open(path, "r", encoding="utf-8") as f:
                prompt_content = f.read()
            logger.debug(f"Successfully loaded prompt from {path}, content length: {len(prompt_content)} chars.")
            return prompt_content
        except FileNotFoundError:
            logger.error(f"Prompt file not found at: {path}")
            raise FileNotFoundError(f"Prompt file not found at: {path}. Please check the path.")
        except IOError as e:
            logger.error(f"Error reading prompt file {path}: {e}")
            raise IOError(f"Could not read prompt file {path}: {e}")

    def wants_expert_handoff(self, message: str) -> bool:
        if not message:
            return False

        lowered = message.lower()
        explicit_phrases = [
            "send this to an expert",
            "talk to an expert",
            "contact an expert",
            "send to ekr",
            "email ekr",
            "email this conversation",
            "send this conversation",
            "send the email",
            "send email",
            "please send the email",
            "send it to the expert",
            "send it to ekr",
            "have an expert look at this",
            "someone should look at this",
            "can an expert help",
        ]
        implicit_phrases = [
            "this is too hard",
            "this is complicated",
            "i need more help",
            "i need human help",
            "i need an expert",
            "i want an expert",
            "can someone help me",
            "i want to talk to someone",
            "this should be handled by an expert",
        ]

        return any(phrase in lowered for phrase in explicit_phrases + implicit_phrases)

    def wants_draft_report(self, message: str) -> bool:
        if not message:
            return False

        lowered = message.strip().lower()
        explicit_phrases = [
            "draft energy report",
            "draft report",
            "energy report",
            "generate a report",
            "generate report",
            "create a report",
            "create report",
            "prepare a report",
            "prepare report",
            "download a report",
            "download report",
        ]

        return any(phrase in lowered for phrase in explicit_phrases)

    def _normalize_binary_reply(self, message: str) -> str:
        return " ".join(message.strip().lower().strip(" \t\r\n.,!?").split())

    def is_confirmation(self, message: str) -> bool:
        if not message:
            return False

        normalized = self._normalize_binary_reply(message)
        positive_responses = {
            "yes",
            "yes please",
            "sure",
            "sure please",
            "sure thing",
            "ok",
            "okay",
            "okay please",
            "yep",
            "yeah",
            "absolutely",
            "please do",
            "go ahead",
            "send it",
            "send it please",
            "please send it",
            "send email",
            "please send email",
            "send the email",
            "please send the email",
            "yes send the email",
            "yes please send the email",
        }
        return normalized in positive_responses

    def is_rejection(self, message: str) -> bool:
        if not message:
            return False

        normalized = self._normalize_binary_reply(message)
        negative_responses = {
            "no",
            "no thanks",
            "no thank you",
            "don't send",
            "do not send",
            "cancel",
            "not now",
            "never mind",
        }
        return normalized in negative_responses



    def classify_question(self, message: str, previous_classification: Optional[str] = None) -> str:
        """
        Classifies a user question by sending it to an Azure OpenAI model.
        The classification runs in a separate process with a timeout.

        Args:
            message (str): The user's question to classify.
            previous_classification (Optional[str]): The previous classification, returned on timeout or error.

        Returns:
            str: The classification result (e.g., "building_specific", "generic"),
                 or `previous_classification` if the API call fails or times out.
        """
        logger.info(f"Classifying question: '{message[:70]}...'")
        logger.debug(f"Previous classification (fallback): {previous_classification}")
        start_time = time.time()
        started_perf = time.perf_counter()
        telemetry_messages = [
            {"role": "system", "content": self.prompt_template},
            {"role": "user", "content": message},
        ]
        if not message:
            logger.warning("Attempted to classify an empty message. Returning previous classification.")
            return previous_classification if previous_classification is not None else "generic" # Default if no previous

        if client is None:
            logger.error("OpenAI client is not initialized. Cannot classify question.")
            record_model_call(
                component="router_agent",
                model=deployment,
                input_messages=telemetry_messages,
                latency_seconds=time.perf_counter() - started_perf,
                success=False,
                error="OpenAI client is not initialized.",
            )
            return previous_classification if previous_classification is not None else "generic"


        manager = multiprocessing.Manager()
        return_dict = manager.dict()

        p = multiprocessing.Process(
            target=openai_call_worker,
            args=(self.prompt_template, message, return_dict)
        )
        
        logger.debug("Starting OpenAI API call in a separate process.")
        p.start()
        
        # Set a reasonable timeout. 5 seconds might be too short for some API calls.
        # Consider making this configurable or dynamic.
        process_timeout = 1000 # seconds
        p.join(timeout=process_timeout)

        if p.is_alive():
            logger.warning(f"[RouterAgent] Timeout ({process_timeout}s) occurred. Terminating process.")
            p.terminate()
            p.join() # Ensure the process is truly terminated
            return_value = previous_classification if previous_classification is not None else "generic"
            logger.info(f"Returning previous_classification due to timeout: {return_value}")
            record_model_call(
                component="router_agent",
                model=deployment,
                input_messages=telemetry_messages,
                latency_seconds=time.perf_counter() - started_perf,
                success=False,
                error=f"Router classification timed out after {process_timeout}s.",
            )
            return return_value
        else:
            if "result" in return_dict:
                classification_result = return_dict["result"]
                logger.info(f"Question classified successfully: '{classification_result}' for message: '{message[:70]}...'")
                end_time = time.time()
                print(f"Classification Agent responded in {end_time - start_time} seconds")
                record_model_call(
                    component="router_agent",
                    model=deployment,
                    input_messages=telemetry_messages,
                    output_text=classification_result,
                    latency_seconds=time.perf_counter() - started_perf,
                    success=True,
                )
                return classification_result
            else:
                error_msg = return_dict.get('error', 'Unknown error (no result or error key in return_dict)')
                logger.error(f"[RouterAgent] Error during classification for message '{message[:70]}...': {error_msg}")
                return_value = previous_classification if previous_classification is not None else "generic"
                logger.info(f"Returning previous_classification due to error: {return_value}")
                record_model_call(
                    component="router_agent",
                    model=deployment,
                    input_messages=telemetry_messages,
                    latency_seconds=time.perf_counter() - started_perf,
                    success=False,
                    error=error_msg,
                )
                return return_value
