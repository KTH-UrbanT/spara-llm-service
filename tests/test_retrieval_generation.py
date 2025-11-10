import os
import json
from unittest.mock import patch, MagicMock
import pytest

from main_retrieval_generation_pinecone import *

DUMMY_SYSTEM_MSG = {"role": "system", "content": "You are a test assistant."}
DUMMY_USER_QUESTION = "Hello, can you hear me?"


# -------------------------------------------------------------------
# 2. Live integration test: only runs when ENV vars are present
# -------------------------------------------------------------------
required_env = ["AZURE_ENDPOINT", "OPENAI_API_KEY", "LANGUAGE_MODEL_DEPLOYMENT_NAME"]

@pytest.mark.skipif(any(not os.getenv(v) for v in required_env), reason="Azure creds not set")
def test_generate_reply_live():
    """Quick smoke‑test against live Azure OpenAI (runs only when creds are available)."""
    
    rg = RetrievalGeneration()
    
    # a tiny one‑turn conversation
    conversation = [DUMMY_SYSTEM_MSG]
    result = rg.generate_reply_texts(
        question=DUMMY_USER_QUESTION,
        existing_conversation=conversation,
        type="text"
    )

    # The assistant reply should not be empty
    assert result[-1]["role"] == "assistant"
    assert isinstance(result[-1]["content"], str) and len(result[-1]["content"]) > 0