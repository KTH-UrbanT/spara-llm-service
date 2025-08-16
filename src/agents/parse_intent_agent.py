import os
import logging
import multiprocessing
import sys
import json
from typing import Dict, Any
from dotenv import load_dotenv
from azure.core.credentials import AzureKeyCredential
from azure.ai.inference import ChatCompletionsClient
from azure.ai.inference.models import SystemMessage, UserMessage
from src.agents.base_agent import BaseAgent

# Load environment variables
load_dotenv()

# Setup logger
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)
logging.getLogger("azure.core.pipeline").setLevel(logging.WARNING)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.ai.inference").setLevel(logging.WARNING)

# Also suppress HTTP client logs if using httpx or requests
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# Azure client configuration
endpoint = os.getenv("AZURE_INFERENCE_SDK_ENDPOINT")
model_name = os.getenv("DEPLOYMENT_NAME")
subscription_key = os.getenv("AZURE_INFERENCE_SDK_KEY")

if not all([endpoint, model_name, subscription_key]):
    logger.critical("Missing one or more Azure environment variables.")
    sys.exit(1)

try:
    client = ChatCompletionsClient(endpoint=endpoint, credential=AzureKeyCredential(subscription_key))
    logger.info("Azure ChatCompletionsClient initialized for ParseIntentAgent.")
except Exception as e:
    logger.critical(f"Failed to initialize Azure client: {e}")
    sys.exit(1)


def openai_call_worker(prompt_template: str, message: str, return_dict: Dict):
    try:
        completion = client.complete(
            messages=[
                SystemMessage(content=prompt_template),
                UserMessage(content=message)
            ],
            model=model_name,
            max_tokens=800
        )
        response = completion.choices[0].message.content
        return_dict["result"] = response
    except Exception as e:
        return_dict["error"] = str(e)

class ParseIntentAgent(BaseAgent):
    def __init__(self, prompt_path: str = None):
        super().__init__()
        default_prompt_path = os.path.join(os.path.dirname(__file__), "..", "prompts", "building_intent_classification_prompt.txt")
        try:
            self.prompt_template = self._load_prompt(prompt_path or default_prompt_path)
        except Exception as e:
            logger.critical(f"Prompt loading failed: {e}")
            raise

    def _load_prompt(self, path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        messages = state.get("messages", [])
        user_input = messages[-1]["content"] if messages else ""

        manager = multiprocessing.Manager()
        return_dict = manager.dict()

        p = multiprocessing.Process(
            target=openai_call_worker,
            args=(self.prompt_template, user_input, return_dict)
        )

        p.start()
        p.join(timeout=100)

        if p.is_alive():
            p.terminate()
            p.join()
            logger.warning("Intent classification timed out.")
            state["context"] = {
                "intent": "unknown",
                "entities": [],
                "ambiguous": False,
            }
        else:
            result = return_dict.get("result")
            if result:
                logger.info(f"LLM classification result: {result}")
                parsed = self._parse_response(result)
                state["context"] = parsed
            else:
                logger.error(f"ParseIntentAgent error: {return_dict.get('error')}")
                state["context"] = {
                    "intent": "unknown",
                    "entities": [],
                    "ambiguous": False,
                }

        return state


    def _parse_response(self, text: str) -> Dict[str, Any]:
        """
        Parses LLM response in JSON format:
        {
        "intent": "<intent>",
        "entities": [<entity1>, <entity2>, ...],
        "ambiguous": true/false
        }
        """
        try:
            parsed = json.loads(text)
            return {
                "intent": parsed.get("intent", "unknown"),
                "entities": parsed.get("entities", []),
                "ambiguous": parsed.get("ambiguous", False)
            }
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from model response: {e}")
            return {
                "intent": "unknown",
                "entities": [],
                "ambiguous": False
            }