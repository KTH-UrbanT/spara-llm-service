# src/agents/building_agent.py

import re
import datetime
import logging
import os
import json 
from typing import List, Dict, Optional, Union
from openai import AzureOpenAI, APIConnectionError, RateLimitError, APIStatusError
from dotenv import load_dotenv

# Import components from their new locations
from src.agents.base_agent import BaseAgent
from src.database.sql_client import SQLClient
from src.database.building_info_client import BuildingInfoClient 
from src.utility import load_prompt # Import the utility function

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BuildingAgent(BaseAgent):
    """
    Building agent functionalities.
    1. Understand which address a user belongs to.
    2. Show a small snapshot of the building information we have.
    3. If the user confirms it's the right building, query the asked measures.
    4. If the user asks to run some simulations.
    This agent uses OpenAI to classify user intent and generate natural language responses.
    It operates as a specialized agentic RAG component.
    """

    # Paths for prompt files relative to the 'src' directory
    # The load_prompt utility will handle resolving these paths from src
    INTENT_CLASSIFICATION_PROMPT_PATH = os.path.join("src/prompts", "building_intent_classification_prompt.txt")
    RESPONSE_GENERATION_PROMPT_PATH = os.path.join("src/prompts", "building_response_generation_prompt.txt")

    def __init__(self):
        super().__init__()
        self.sql_agent = SQLClient()
        self.building_info_client = BuildingInfoClient()
        self._initialize_openai_client()
        self._load_prompts()
    
    def _initialize_openai_client(self):
        """Initializes the AzureOpenAI client."""
        required_env_vars = {
            "AZURE_ENDPOINT": "Azure OpenAI endpoint",
            "BUILDING_MODEL_DEPLOYMENT_NAME": "Deployment name for the building model",
            "OPENAI_API_KEY": "OpenAI API key",
            "BUILDING_MODEL_API_VERSION": "API version for the building model"
        }

        for var, desc in required_env_vars.items():
            if not os.getenv(var):
                logger.error(f"Missing required environment variable: {var} ({desc})")
                raise ValueError(f"Missing required environment variable: {var}. Please set it in your .env file.")

        endpoint = os.getenv("AZURE_ENDPOINT")
        self.deployment = os.getenv("BUILDING_MODEL_DEPLOYMENT_NAME")
        subscription_key = os.getenv("OPENAI_API_KEY")
        api_version = os.getenv("BUILDING_MODEL_API_VERSION")
        logger.info(f"Building Model is {self.deployment} and the api version is {api_version}. ")
        try:
            self.client = AzureOpenAI(
                azure_endpoint=endpoint,
                api_key=subscription_key,
                api_version=api_version,
            )
            logger.info("AzureOpenAI client initialized successfully for BuildingAgent.")
        except Exception as e:
            logger.error(f"Failed to initialize AzureOpenAI client for BuildingAgent: {e}")
            raise RuntimeError(f"Failed to initialize AzureOpenAI client for BuildingAgent: {e}")


    def _load_prompts(self):
        """Loads all necessary prompt templates using the utility function."""
        self.intent_prompt_template = self._load_prompt(self.INTENT_CLASSIFICATION_PROMPT_PATH)
        self.response_prompt_template = self._load_prompt(self.RESPONSE_GENERATION_PROMPT_PATH) 
        logger.info("All BuildingAgent prompts loaded.")
        
    def _load_prompt(self, relative_path: str) -> str:
        """
        Loads a prompt file relative to the project root.

        Args:
            relative_path (str): Relative path from the project root (e.g., 'prompts/foo.txt').

        Returns:
            str: The content of the prompt file.

        Raises:
            ValueError: If the file is not found.
        """
        # Resolve full path relative to the root directory (1 level above 'src')
        src_dir = os.path.dirname(os.path.abspath(__file__))  # e.g., src/agents
        root_dir = os.path.abspath(os.path.join(src_dir, "..", ".."))  # project root
        full_path = os.path.join(root_dir, relative_path)

        logger.debug(f"Loading prompt from: {full_path}")
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
            logger.info(f"Loaded prompt from: {full_path}")
            return content
        except FileNotFoundError:
            logger.error(f"Prompt file not found: {full_path}")
            raise ValueError(f"Prompt file not found: {full_path}")
        except IOError as e:
            logger.error(f"Failed to read prompt file {full_path}: {e}")
            raise

    def _get_llm_response(self, messages: List[Dict[str, str]]) -> Optional[str]:
        """Generic helper to call the OpenAI API."""
        try:
            completion = self.client.chat.completions.create(
                model=self.deployment,
                messages=messages,
                max_tokens=500,
                temperature=0.0, # Low temperature for classification/factual responses
            )
            return completion.choices[0].message.content
        except (APIConnectionError, RateLimitError, APIStatusError) as e:
            logger.error(f"OpenAI API error: {e}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"An unexpected error occurred during LLM call: {e}", exc_info=True)
            return None

    def _classify_intent(self, user_input: str, conversation_history: List[Dict[str, str]], metadata: Dict) -> Dict:
        """Uses LLM to classify user intent and extract entities."""
        context = {
            "known_addresses": [item['address_of_building'] for item in metadata.get('building_information', []) if 'address_of_building' in item],
            "last_confirmed_address": metadata.get('last_confirmed_address'),
            "pending_address_confirmation": metadata.get('pending_address_confirmation'),
            "previous_mismatched_addresses": metadata.get('mismatched_addresses', [])
        }
        
        messages = [
            {"role": "system", "content": self.intent_prompt_template.format(
                context=json.dumps(context), user_input=user_input 
            )},
            {"role": "user", "content": user_input}
        ]
        
        raw_response = self._get_llm_response(messages)
        if raw_response:
            try:
                match = re.search(r'\{.*\}', raw_response, re.DOTALL)
                if match:
                    intent_data = json.loads(match.group(0))
                    logger.info(f"LLM classified intent: {intent_data}")
                    return intent_data
                else:
                    logger.warning(f"LLM response not in expected JSON format: {raw_response}")
                    return {"intent": "general_query"} 
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse LLM intent response as JSON: {raw_response}. Error: {e}")
                return {"intent": "general_query"} 
        return {"intent": "general_query"} 

    def _generate_response(self, user_input: str, history: List[Dict[str, str]], current_address: Optional[str],
                           action_description: str, results: Dict, metadata: Dict) -> str:
        """Uses LLM to generate a natural language response based on actions/results."""
        cleaned_history = []
        for msg in history:
            if 'role' in msg and 'content' in msg:
                cleaned_history.append(f"{msg['role']}: {msg['content']}")

        messages = [
            {"role": "system", "content": self.response_prompt_template.format(
                user_input=user_input,
                current_address=current_address if current_address else "not specified",
                history="\n".join(cleaned_history[-5:]), 
                action_description=action_description,
                results=json.dumps(results), 
                metadata=json.dumps(metadata) 
            )},
            {"role": "user", "content": f"Generate a helpful assistant response for the user's last message: '{user_input}'"}
        ]
        response = self._get_llm_response(messages)
        return response if response else "I'm sorry, I couldn't generate a response at this moment due to an internal issue."


    def check_building_info_status(self, user_input: str, metadata: dict):
        """
        Determines the current state of building info based on user input and existing metadata.
        This function primarily extracts addresses if mentioned and compares them to metadata.
        It's used internally to supplement LLM intent with direct address extraction for robustness.
        """
        address_pattern = re.compile(
            r"\b(?:bor\ på|address\ är|live\ at|live\ in|address\ is|live\ on|of)\b\W*([^\.\,\n]+)",
            re.IGNORECASE
        )
        found_address_match = address_pattern.search(user_input)
        
        user_provided_address = None
        if found_address_match:
            user_provided_address = found_address_match.group(1).strip().rstrip('.,')

        normalized_user_provided_address = user_provided_address.lower() if user_provided_address else None

        if "building_information" in metadata and metadata["building_information"]:
            building_data = metadata['building_information']
            building_addresses_in_metadata = [
                item['address_of_building'].lower() 
                for item in building_data if 'address_of_building' in item
            ]

            if normalized_user_provided_address:
                if normalized_user_provided_address in building_addresses_in_metadata:
                    return {'status': 'address_in_input_matches_metadata', 'address': user_provided_address}
                else:
                    return {'status': 'address_in_input_mismatches_metadata', 'address': user_provided_address, 'known_addresses': [item['address_of_building'] for item in building_data if 'address_of_building' in item]}
            else:
                return {'status': 'no_address_in_input_metadata_present', 'address': None}
        else: # Metadata does not contain building information
            if normalized_user_provided_address:
                return {"status": "address_found_no_metadata", "address": user_provided_address}
            else:
                return {"status": "no_address_in_input_metadata_empty", "address": None}
    
    def user_lands(self, last_message: str, messages: list, metadata: dict, thread_id: str): 
        """
        Orchestrates the building information flow based on user input and metadata.
        Returns the updated metadata and a response string (or None if no immediate response).
        """
        user_id = thread_id.split(':')[0]
        assistant_response = None
        current_address = metadata.get('last_confirmed_address') or \
                          (metadata['building_information'][0]['address_of_building'] if metadata.get('building_information') else None)

        # First, classify the user's intent using LLM
        intent_data = self._classify_intent(last_message, messages, metadata)
        user_intent = intent_data.get('intent')
        
        # Then, check building info status for address handling (can be redundant with LLM, but adds robustness)
        address_status_result = self.check_building_info_status(last_message, metadata)

        action_description = "No specific building action."
        action_results = {}

        logger.info(f"User intent classified: {user_intent}. Address status: {address_status_result['status']}")

        # --- Handle User Confirmation ---
        if user_intent == 'confirm_address' and metadata.get('pending_address_confirmation'):
            confirmed_address = metadata['pending_address_confirmation']
            metadata['building_information'] = [{'address_of_building': confirmed_address}] # Ensure it's stored
            metadata['last_confirmed_address'] = confirmed_address
            metadata.pop('pending_address_confirmation', None) # Clear pending state
            action_description = f"User confirmed address: {confirmed_address}."
            logger.info(action_description)
            snapshot = self._get_building_snapshot(confirmed_address)
            action_results = {"snapshot": snapshot}
            assistant_response = self._generate_response(
                last_message, messages, confirmed_address,
                "Address confirmed and snapshot provided.", action_results, metadata
            )
            return metadata, assistant_response
        
        # --- Address Resolution Scenarios ---
        
        # Scenario: User provides an address (e.g., "Kistagången 16")
        if user_intent == 'provide_address' and intent_data.get('address'):
            user_provided_address = intent_data['address']
            
            if not metadata.get('building_information'):
                metadata['building_information'] = [{'address_of_building': user_provided_address}]
                metadata['last_confirmed_address'] = user_provided_address
                action_description = f"User provided and set address: {user_provided_address}."
                action_results = {"address_set": user_provided_address}
                logger.info(action_description)
                assistant_response = self._generate_response(
                    last_message, messages, user_provided_address,
                    "User explicitly provided address.", action_results, metadata
                )
            else: 
                normalized_user_provided = user_provided_address.lower()
                known_addresses = [item['address_of_building'].lower() for item in metadata['building_information']]
                
                if normalized_user_provided in known_addresses:
                    metadata['last_confirmed_address'] = user_provided_address
                    action_description = f"User provided address '{user_provided_address}' matches known address."
                    action_results = {"address_matched": user_provided_address}
                    logger.info(action_description)
                    assistant_response = self._generate_response(
                        last_message, messages, user_provided_address,
                        "User confirmed existing address.", action_results, metadata
                    )
                else: 
                    if 'mismatched_addresses' not in metadata:
                        metadata['mismatched_addresses'] = []
                    metadata['mismatched_addresses'].append({
                        'user_provided_address': user_provided_address,
                        'system_known_addresses': [addr for addr in known_addresses],
                        'timestamp': self._get_current_timestamp()
                    })
                    action_description = f"User provided address '{user_provided_address}' does not match known addresses. Prompting for clarification."
                    action_results = {"mismatch_logged": user_provided_address, "known": known_addresses}
                    logger.info(action_description)
                    assistant_response = self._generate_response(
                        last_message, messages, current_address,
                        "Address mismatch detected.", action_results, metadata
                    )
            return metadata, assistant_response


        # Scenario: User asks a building question without specifying address, and metadata is empty (or intent is ask_for_address).
        if user_intent == 'ask_for_address' or address_status_result['status'] == 'no_address_in_input_metadata_empty':
            logger.info("Debug: No address in input, metadata empty or user explicitly asked for address. Fetching user's default address.")
            user_default_address = self.sql_agent.get_address_by_user_name(user_id)
            if user_default_address: 
                metadata['building_information'] = [{'address_of_building': user_default_address}]
                metadata['pending_address_confirmation'] = user_default_address 
                action_description = f"Fetched default address: {user_default_address}. Requesting user confirmation."
                action_results = {"suggested_address": user_default_address}
                logger.info(action_description)
                assistant_response = self._generate_response(
                    last_message, messages, user_default_address,
                    "Suggested user's default address.", action_results, metadata
                )
            else:
                action_description = "No default address found in records. Asking user for address."
                action_results = {"no_address_found": True}
                logger.warning(action_description)
                assistant_response = self._generate_response(
                    last_message, messages, None,
                    "No default address found.", action_results, metadata
                )
            return metadata, assistant_response

        # Scenario: No address in input, and building information is already in metadata.
        if address_status_result['status'] == 'no_address_in_input_metadata_present':
            if not current_address: 
                 current_address = metadata['building_information'][0]['address_of_building']
                 metadata['last_confirmed_address'] = current_address 

            logger.info(f"Debug: No address in input, metadata present. Using address: {current_address}")

            if user_intent == 'get_building_snapshot':
                snapshot = self._get_building_snapshot(current_address)
                action_description = "Provided building snapshot."
                action_results = {"snapshot": snapshot}
                logger.info(action_description)
                assistant_response = self._generate_response(
                    last_message, messages, current_address,
                    action_description, action_results, metadata
                )
            elif user_intent == 'get_measure' and intent_data.get('measure_name'):
                measure = intent_data['measure_name']
                measure_value = self._query_building_measures(current_address, measure)
                action_description = f"Queried measure '{measure}'."
                action_results = {"measure": measure, "value": measure_value}
                logger.info(action_description)
                assistant_response = self._generate_response(
                    last_message, messages, current_address,
                    action_description, action_results, metadata
                )
            elif user_intent == 'run_simulation' and intent_data.get('simulation_type'):
                simulation_params = intent_data.get('parameters', {})
                simulation_params['type'] = intent_data['simulation_type'] 
                simulation_result = self._run_simulation(current_address, simulation_params)
                action_description = f"Ran simulation '{simulation_params['type']}'."
                action_results = {"simulation_result": simulation_result}
                logger.info(action_description)
                assistant_response = self._generate_response(
                    last_message, messages, current_address,
                    action_description, action_results, metadata
                )
            elif user_intent == 'general_query':
                action_description = "General query with known address. Suggesting relevant actions."
                snapshot = self._get_building_snapshot(current_address) 
                action_results = {"snapshot_offered": snapshot}
                assistant_response = self._generate_response(
                    last_message, messages, current_address,
                    "No specific request, offering general building information or help.", action_results, metadata
                )

            return metadata, assistant_response

        # Fallback if no specific scenario matched, and if current_address is available
        if current_address:
            if user_intent == 'general_query':
                assistant_response = self._generate_response(
                    last_message, messages, current_address,
                    "General query with known address.", {"status": "ready_for_queries"}, metadata
                )
            else:
                 assistant_response = self._generate_response(
                    last_message, messages, current_address,
                    "No specific action for this query, but address is known.", {"status": "ready_for_queries"}, metadata
                )
                
        else: # No address known, and no address provided or asked for explicitly
            assistant_response = self._generate_response(
                last_message, messages, None,
                "No building context available.", {"status": "needs_address_to_proceed"}, metadata
            )

        return metadata, assistant_response

    # --- Helper Methods for Data Retrieval and Simulations (using BuildingInfoClient) ---

    def _get_building_snapshot(self, address: str) -> Dict:
        """
        Retrieves a small snapshot of building information using BuildingInfoClient.
        """
        details = self.building_info_client.get_building_details(address)
        if details:
            return details
        return {"error": "Could not retrieve snapshot for this building."}

    def _query_building_measures(self, address: str, measure_name: str) -> Optional[str]:
        """
        Queries a specific measure for the building using BuildingInfoClient.
        """
        value = self.building_info_client.get_building_measure(address, measure_name)
        return str(value) if value is not None else None

    def _run_simulation(self, address: str, simulation_params: Dict) -> Dict:
        """
        Runs a simulation based on user request using BuildingInfoClient.
        """
        result = self.building_info_client.run_building_simulation(address, simulation_params)
        return result if result else {"error": "Could not run simulation for this building."}

    def _get_current_timestamp(self):
        """Helper to get a timestamp (for mismatched addresses)."""
        return datetime.datetime.now().isoformat()

# Example usage (for testing purposes, in a real app this would be called by a router)
if __name__ == '__main__':
    logging.getLogger().setLevel(logging.INFO) # Set to INFO for less verbose output in main

    # IMPORTANT: For testing this refactored structure, you must run this from the project root
    # (the directory containing 'src'). Otherwise, the relative imports (e.g., from src.agents.base_agent)
    # and prompt loading (via src.utility.load_prompt) will fail.

    # Dummy environment variables for testing
    os.environ['AZURE_ENDPOINT'] = 'https://your-openai-resource.openai.azure.com/'
    os.environ['BUILDING_MODEL_DEPLOYMENT_NAME'] = 'gpt-4o' # Or your specific deployment
    os.environ['OPENAI_API_KEY'] = 'your_openai_api_key' # Replace with a valid key
    os.environ['BUILDING_MODEL_API_VERSION'] = '2024-02-15-preview'

    # Ensure prompts directory exists and contains the .txt files for testing
    # Note: These paths are relative to the *current working directory* of the script that runs this __main__ block.
    # If you run 'python src/agents/building_agent.py', the current working directory is 'src/agents'.
    # If you run 'python -m src.agents.building_agent' from the project root, the cwd is the project root.
    # For this test block, we assume execution from the project root, so paths are 'src/prompts/...'
    try:
        os.makedirs("src/prompts", exist_ok=True)
        with open("src/prompts/building_intent_classification_prompt.txt", "w") as f:
            f.write("""You are an AI assistant designed to classify user intents related to building information and simulations.
Analyze the user's message and current conversation context to identify their primary goal.
Return a JSON object with the 'intent' and any relevant 'entities'.

Possible intents:
- 'confirm_address': User is confirming a previously suggested address.
- 'provide_address': User is explicitly stating an address. Extract the address.
- 'get_building_snapshot': User wants general information or a summary about their building.
- 'get_measure': User is asking for a specific measure (e.g., energy class, size, year built). Extract the 'measure_name'.
- 'run_simulation': User wants to run a simulation (e.g., energy improvement). Extract 'simulation_type' and any 'parameters'.
- 'ask_for_address': User is asking a building-related question but hasn't provided an address yet.
- 'general_query': The intent is not specifically covered by the above, or it's a general follow-up.

Context: {context}
User: {user_input}

Strictly return only the JSON object.
Example output for "What is the energy class of my building?":
{{
  "intent": "get_measure",
  "measure_name": "energy class"
}}

Example output for "Yes, that's right.":
{{
  "intent": "confirm_address"
}}

Example output for "Give me information about Kistagången 16.":
{{
  "intent": "provide_address",
  "address": "Kistagången 16"
}}

Example output for "How can I improve my energy class?":
{{
  "intent": "run_simulation",
  "simulation_type": "energy_improvement"
}}

Example output for "Tell me about my building.":
{{
  "intent": "get_building_snapshot"
}}

Example output for "I need information.":
{{
    "intent": "general_query"
}}""")
        with open("src/prompts/building_response_generation_prompt.txt", "w") as f:
            f.write("""You are an helpful AI assistant providing information about buildings.
Based on the provided context and action results, generate a concise and helpful response to the user.
Keep the tone professional and informative.

Context:
- User input: {user_input}
- Current building address: {current_address}
- Previous conversation history: {history}
- Action performed: {action_description}
- Data retrieved/Simulation results: {results}
- Metadata: {metadata}

Generate a response:""")
    except Exception as e:
        logger.error(f"Failed to create dummy prompt files: {e}")
        # Exit or handle gracefully if prompts cannot be set up for test
        exit(1)


    agent = BuildingAgent()

    test_cases = [
        ("user123", "what is the energy class of my building?", {"building_information": [{"address_of_building": "Teknikringen 10B BRF ABS"}], "last_confirmed_address": "Teknikringen 10B BRF ABS"}), 
        ("user123", "yes thats right.", {"pending_address_confirmation": "Teknikringen 10B BRF ABS"}), 
        ("user456", "give me information about Kistagången 16.", {"building_information": [{"address_of_building": "Drottning Kristinas Väg 15"}], "last_confirmed_address": "Drottning Kristinas Väg 15"}), 
        ("user789", "how can i improve my energy class of my building?", {"building_information": [{"address_of_building": "Professorlingan 51"}], "last_confirmed_address": "Professorlingan 51"}), 
        ("user123", "Tell me about my building.", {"last_confirmed_address": "Teknikringen 10B BRF ABS", "building_information": [{"address_of_building": "Teknikringen 10B BRF ABS"}]}), 
        ("user123", "What's its size?", {"last_confirmed_address": "Teknikringen 10B BRF ABS", "building_information": [{"address_of_building": "Teknikringen 10B BRF ABS"}]}), 
        ("user123", "Run an energy improvement simulation.", {"last_confirmed_address": "Teknikringen 10B BRF ABS", "building_information": [{"address_of_building": "Teknikringen 10B BRF ABS"}]}), 
        ("user999", "Tell me about my building.", {}), 
        ("user123", "I actually live at Drottning Kristinas Väg 15.", {"last_confirmed_address": "Teknikringen 10B BRF ABS", "building_information": [{"address_of_building": "Teknikringen 10B BRF ABS"}]}),
        ("user100", "What's my energy class?", {}), # Completely new user, no address in metadata or SQLClient
    ]

    print("\n--- Running BuildingAgent Test Cases ---")
    for i, (user_id, user_input, initial_metadata) in enumerate(test_cases):
        print(f"\n--- Test Case {i+1} ---")
        print(f"User: {user_id}")
        print(f"Input: {user_input}")
        print(f"Initial Metadata: {initial_metadata}")

        messages_history = [{"role": "user", "content": user_input}]
        
        updated_metadata, response = agent.user_lands(user_input, messages_history, initial_metadata.copy(), f"{user_id}:thread1")
        
        print(f"Assistant: {response}")
        print(f"Updated Metadata: {updated_metadata}")
        print("-" * 30)