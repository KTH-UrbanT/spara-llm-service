# src/agents/instances.py

from src.agents.parse_intent_agent import ParseIntentAgent
from src.database.sql_client import SQLClient
from src.database.vector_client import VectorClient, VectorClientConfig
from src.agents.simulation_agent import SimulationAgent
from src.agents.openai_agent import OpenAIResponseAgent

# Initialize once and reuse across project
parse_intent_agent = ParseIntentAgent()
sql_client = SQLClient()
vector_query_agent = VectorClient(VectorClientConfig())
simulation_agent = SimulationAgent()
openai_response_agent = OpenAIResponseAgent()
