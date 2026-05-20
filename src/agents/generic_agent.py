import os
import logging
from openai import AzureOpenAI, APIConnectionError, RateLimitError, APIStatusError
from typing import List, Dict, Optional, Union
from src.database.vector_client import VectorClient , VectorClientConfig
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
        Initializes the AzureOpenAI client.
        Ensures all necessary environment variables are set.

        Raises:
            ValueError: If any required Azure OpenAI environment variables are missing.
            RuntimeError: If AzureOpenAI client fails to initialize.
        """
        required_env_vars = {
            "AZURE_ENDPOINT": "Azure OpenAI endpoint",
            "GENERIC_MODEL_DEPLOYMENT_NAME": "Deployment name for the generic model",
            "OPENAI_API_KEY": "OpenAI API key",
            "GENERIC_MODEL_API_VERSION": "API version for the generic model"
        }

        for var, desc in required_env_vars.items():
            if not os.getenv(var):
                logger.error(f"Missing required environment variable: {var} ({desc})")
                raise ValueError(f"Missing required environment variable: {var}. Please set it in your .env file.")

        endpoint = os.getenv("AZURE_ENDPOINT")
        self.deployment = os.getenv("GENERIC_MODEL_DEPLOYMENT_NAME")
        subscription_key = os.getenv("OPENAI_API_KEY")
        api_version = os.getenv("GENERIC_MODEL_API_VERSION")
        logger.info(f"Generic Model is {self.deployment} and the api version is {api_version}. ")
        try:
            self.client = AzureOpenAI(
                azure_endpoint=endpoint,
                api_key=subscription_key,
                api_version=api_version,
            )
            logger.info("AzureOpenAI client initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize AzureOpenAI client: {e}")
            raise RuntimeError(f"Failed to initialize AzureOpenAI client: {e}")      
        
    def handle_generic_input(self, last_message: str, message_list: List[Dict[str, Union[str, int]]]) -> Optional[str]:
        start_time = time.time()  
        results = self.vector_client.query(last_message)
        content_from_doc = ' '.join(i['page_content'] for i in results)
        logger.info(f"Received generic input. Last message: '{last_message[:70]}...'")

        if not isinstance(last_message, str):
            logger.warning(f"Invalid 'last_message' type. Expected string, got {type(last_message)}. Returning None.")
            return None
        if not isinstance(message_list, list):
            logger.warning(f"Invalid 'message_list' type. Expected list, got {type(message_list)}. Returning None.")
            return None

        # Check if last_message matches the content of the last item in message_list
        if message_list:
            try:
                if message_list[-1].get('content') != last_message:
                    logger.warning(
                        f"Mismatch between 'last_message' ('{last_message[:50]}...') "
                        f"and content of the last message in 'message_list' ('{message_list[-1].get('content', '')[:50]}...'). "
                        "Ensuring 'last_message' is appended for the API call."
                    )
                else:
                    logger.info(f"Last message ('{last_message[:50]}...') matches content of last item in message_list.")
            except (KeyError, IndexError):
                logger.warning("Could not access 'content' of the last message in 'message_list'. Message list might be malformed or empty.")
        else:
            logger.info("message_list is empty. 'last_message' will be the first user message for the model.")

        # Prepare messages for the OpenAI API:
        # 1. System message from prompt_template
        # 2. Historical messages from message_list (extracting only role and content)
        messages_for_api: List[Dict[str, str]] = [{"role": "system", "content": self.prompt_template}]

        for i, msg in enumerate(message_list):
            if isinstance(msg, dict) and 'role' in msg and 'content' in msg:
                messages_for_api.append({'role': msg['role'], 'content': msg['content']})
            else:
                logger.warning(f"Skipping malformed message at index {i} in message_list: {msg}")

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
        messages_for_api[-1]['content'] = last_message + "\n Context : " + content_from_doc
        try:
            model_started_at = time.perf_counter()
            completion = self.client.chat.completions.create(
                model=self.deployment,
                messages=messages_for_api,
                max_tokens=800,
                temperature=0.2,
                top_p=0.23,
                frequency_penalty=0,
                presence_penalty=0,
                stop=None,
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
            return response_content
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
