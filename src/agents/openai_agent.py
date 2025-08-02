

import os
import logging
import time
from typing import List, Dict, Optional, Union
from openai import AzureOpenAI, APIConnectionError, RateLimitError, APIStatusError
from dotenv import load_dotenv

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

    def generate_response(self, last_message: str, message_list: List[Dict[str, Union[str, int]]]) -> Optional[str]:
        start_time = time.time()

        if not isinstance(last_message, str) or not isinstance(message_list, list):
            logger.warning("Invalid inputs to generate_response. Expecting string and list.")
            return None

        messages = [{"role": "system", "content": self.prompt_template}]
        for msg in message_list:
            if 'role' in msg and 'content' in msg:
                messages.append({'role': msg['role'], 'content': msg['content']})
            else:
                logger.warning(f"Skipping malformed message: {msg}")

        if not messages or messages[-1].get('content') != last_message or messages[-1].get('role') != 'user':
            messages.append({"role": "user", "content": last_message})

        try:
            response = self.client.chat.completions.create(
                model=self.deployment,
                messages=messages,
                max_tokens=800,
                temperature=0.2,
                top_p=0.23,
                frequency_penalty=0,
                presence_penalty=0,
                stop=None,
                stream=False
            )
            response_content = response.choices[0].message.content
            end_time = time.time()
            logger.info(f"Response generated in {end_time - start_time:.2f} seconds.")
            return response_content
        except APIConnectionError as e:
            logger.error(f"Connection error: {e}", exc_info=True)
            return "Error: Cannot connect to AI service."
        except RateLimitError as e:
            logger.error(f"Rate limit exceeded: {e}", exc_info=True)
            return "Error: Rate limit exceeded. Please retry shortly."
        except APIStatusError as e:
            logger.error(f"API status error: {e.status_code} - {e.response}", exc_info=True)
            return f"Error: API returned status code {e.status_code}."
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            return "Error: An unexpected issue occurred."

