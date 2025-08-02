# src/pipeline/state.py
from typing import TypedDict, List, Dict, Optional

class RouterState(TypedDict):
    messages: List[Dict]                # Chat history
    last_message: str                   # User's most recent message
    metadata: Dict                      # Thread-level metadata
    thread_id: str                      # Redis thread ID
    classification: str      # Result from RouterAgent
    agent_response: str     # Final response from specialized agent
