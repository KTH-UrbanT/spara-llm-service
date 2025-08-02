from src.agents.base_agent import BaseAgent
import os
import logging
from azure.ai.inference import ChatCompletionsClient
from azure.ai.inference.models import SystemMessage, UserMessage
from azure.core.credentials import AzureKeyCredential
from typing import Dict, Optional, Union
from dotenv import load_dotenv
import multiprocessing
import sys # Import sys for checking if BaseAgent exists
import time
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
# --- Global  Client Initialization ---
# It's generally better to encapsulate this within a class or function
# if you want to apply robust error handling to its initialization as well.
# For now, we'll add checks here.

# Validate environment variables before creating the client
required_env_vars = {
    "AZURE_INFERENCE_SDK_ENDPOINT": "Azure  endpoint",
    "DEPLOYMENT_NAME": "Deployment name for the router model",
    "AZURE_INFERENCE_SDK_KEY": " API key"
}

for var, desc in required_env_vars.items():
    if not os.getenv(var):
        logger.error(f"Missing required environment variable: {var} ({desc}). Exiting.")
        # In a real application, you might raise an exception or exit more gracefully.
        # For now, we'll set these to None and handle potential errors in client creation.
        globals()[var.lower()] = None # Set corresponding variable to None
        sys.exit(1) # Exit if critical variables are missing

endpoint = os.getenv("AZURE_INFERENCE_SDK_ENDPOINT")
model_name  = os.getenv("DEPLOYMENT_NAME")
subscription_key = os.getenv("AZURE_INFERENCE_SDK_KEY")
logger.info(f"Router Model is {model_name }")
client = None
try:
    client = ChatCompletionsClient(endpoint=endpoint, credential=AzureKeyCredential(subscription_key))
    logger.info("Azure client for RouterAgent initialized successfully.")
except Exception as e:
    logger.critical(f"Failed to initialize Azure client for RouterAgent: {e}")
    # Depending on your application's tolerance for this, you might exit or
    # ensure subsequent calls gracefully handle the missing client.
    sys.exit(1) # Exit if client cannot be initialized


def openai_call_worker(prompt_template: str, message: str, return_dict: Dict):
    """
    Worker function to make an  API call in a separate process.
    Results or errors are stored in a shared dictionary.
    """
    worker_logger = logging.getLogger(f"{__name__}._call_worker")
    worker_logger.debug(f" worker started for message: '{message[:50]}...'")
    try:
        if client is None:
            raise RuntimeError(" client not initialized in worker process.")

        # messages = [
        #     {"role": "system", "content": prompt_template},
        #     {"role": "user", "content": message}
        # ]
        worker_logger.debug(f"Messages sent to  API: {message}")
        completion = client.complete(
                            messages=[
                                SystemMessage(content=prompt_template),
                                UserMessage(content=message)
                            ],
                            model = model_name,
                            max_tokens=1000
                            )
        # completion = client.chat.completions.create(
        #     model=deployment,
        #     messages=messages,
        #     max_tokens=800,
        #     temperature=0,
        #     top_p=0.23,
        #     frequency_penalty=0,
        #     presence_penalty=0,
        #     stop=None,
        #     stream=False
        # )
        response_content = completion.choices[0].message.content
        return_dict["result"] = response_content
        worker_logger.info("API call successful in worker.")
        worker_logger.debug(f"Received content: '{response_content[:100]}...'")
    except Exception as e:
        error_msg = f"An unexpected error occurred in _call_worker: {e}"
        worker_logger.error(error_msg, exc_info=True)
        return_dict["error"] = error_msg


class RouterAgent(BaseAgent):
    """
    RouterAgent classifies incoming user questions to route them to appropriate handlers.
    It uses an Azure  model for classification, running the API call in a separate process
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


    def classify_question(self, message: str, previous_classification: Optional[str] = None) -> str:
        """
        Classifies a user question by sending it to an Azure  model.
        The classification runs in a separate process with a timeout.

        Args:
            message (str): The user's question to classify.
            previous_classification (Optional[str]): The previous classification, returned on timeout or error.

        Returns:
            str: The classification result (e.g., "building_specific", "general"),
                 or `previous_classification` if the API call fails or times out.
        """
        logger.info(f"Classifying question: '{message[:70]}...'")
        logger.debug(f"Previous classification (fallback): {previous_classification}")
        start_time = time.time()
        if not message:
            logger.warning("Attempted to classify an empty message. Returning previous classification.")
            return previous_classification if previous_classification is not None else "general" # Default if no previous

        if client is None:
            logger.error(" client is not initialized. Cannot classify question.")
            return previous_classification if previous_classification is not None else "general"


        manager = multiprocessing.Manager()
        return_dict = manager.dict()

        p = multiprocessing.Process(
            target=openai_call_worker,
            args=(self.prompt_template, message, return_dict)
        )
        
        logger.debug("Starting  API call in a separate process.")
        p.start()
        
        # Set a reasonable timeout. 5 seconds might be too short for some API calls.
        # Consider making this configurable or dynamic.
        process_timeout = 10 # seconds
        p.join(timeout=process_timeout)

        if p.is_alive():
            logger.warning(f"[RouterAgent] Timeout ({process_timeout}s) occurred. Terminating process.")
            p.terminate()
            p.join() # Ensure the process is truly terminated
            return_value = previous_classification if previous_classification is not None else "general"
            logger.info(f"Returning previous_classification due to timeout: {return_value}")
            return return_value
        else:
            if "result" in return_dict:
                classification_result = return_dict["result"]
                logger.info(f"Question classified successfully: '{classification_result}' for message: '{message[:70]}...'")
                end_time = time.time()
                print(f"Classification Agent responded in {end_time - start_time} seconds")
                return classification_result
            else:
                error_msg = return_dict.get('error', 'Unknown error (no result or error key in return_dict)')
                logger.error(f"[RouterAgent] Error during classification for message '{message[:70]}...': {error_msg}")
                return_value = previous_classification if previous_classification is not None else "general"
                logger.info(f"Returning previous_classification due to error: {return_value}")
                return return_value