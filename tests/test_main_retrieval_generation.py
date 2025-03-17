import pytest
from unittest.mock import MagicMock, patch
import os
from main_retrieval_generation import RetrievalGeneration
from openai import AzureOpenAI

# Mock environment variables for testing
@pytest.fixture(autouse=True)
def mock_env_vars():
    with patch.dict(os.environ, {
        "AZURE_ENDPOINT": "mock_endpoint", 
        "LANGUAGE_MODEL_DEPLOYMENT_NAME": "mock_deployment",
        "OPENAI_API_KEY": "mock_api_key", 
        "LANGUAGE_MODEL_API_VERSION": "v1"
    }):
        yield

# Test initialization of RetrievalGeneration
def test_initialization():
    # Mock context_retreival_obj and Azure OpenAI client
    with patch('main_retrieval_generation.context_retreival_obj', autospec=True) as mock_context_obj:
        retriever_gen = RetrievalGeneration()
        
        # Check that the context retrieval object was initialized correctly
        assert retriever_gen.context_retreival_obj == mock_context_obj

        # Check that Azure OpenAI client is correctly initialized
        assert isinstance(retriever_gen.client, AzureOpenAI)

# Test generate_reply_texts method with mocked context retrieval (text search)
def test_generate_reply_texts_text_search():
    question = "What is the capital of France?"
    existing_conversation = [{"role": "user", "content": "Tell me about France."}]
    search_type = "text"

    # Mocking context retrieval to return mock document data
    mock_docs = [
        MagicMock(page_content="Paris is the capital of France."),
        MagicMock(page_content="The Eiffel Tower is in Paris.")
    ]
    
    with patch('main_retrieval_generation.context_retreival_obj.search_text', return_value=mock_docs):
        retriever_gen = RetrievalGeneration()

        # Mocking OpenAI response
        mock_openai_response = MagicMock()
        mock_openai_response.choices = [MagicMock(message=MagicMock(content="The capital of France is Paris."))]
        with patch.object(retriever_gen.client.chat.completions, 'create', return_value=mock_openai_response):
            new_conversation = retriever_gen.generate_reply_texts(question, existing_conversation, search_type)
        
        # Check that the assistant response is appended correctly
        assert len(new_conversation) == 3
        assert new_conversation[-1]["role"] == "assistant"
        assert new_conversation[-1]["content"] == "The capital of France is Paris."

# Test generate_reply_texts method with mocked context retrieval (hybrid search)
def test_generate_reply_texts_hybrid_search():
    question = "What is the capital of Germany?"
    existing_conversation = [{"role": "user", "content": "Tell me about Germany."}]
    search_type = "hybrid"

    # Mocking context retrieval to return mock document data
    mock_docs = [
        MagicMock(page_content="Berlin is the capital of Germany."),
        MagicMock(page_content="The Brandenburg Gate is in Berlin.")
    ]
    
    with patch('main_retrieval_generation.context_retreival_obj.hybrid_search', return_value=mock_docs):
        retriever_gen = RetrievalGeneration()

        # Mocking OpenAI response
        mock_openai_response = MagicMock()
        mock_openai_response.choices = [MagicMock(message=MagicMock(content="The capital of Germany is Berlin."))]
        with patch.object(retriever_gen.client.chat.completions, 'create', return_value=mock_openai_response):
            new_conversation = retriever_gen.generate_reply_texts(question, existing_conversation, search_type)
        
        # Check that the assistant response is appended correctly
        assert len(new_conversation) == 3
        assert new_conversation[-1]["role"] == "assistant"
        assert new_conversation[-1]["content"] == "The capital of Germany is Berlin."

# Test handling of search with no documents found (empty results)
def test_generate_reply_texts_no_documents():
    question = "What is the capital of Japan?"
    existing_conversation = [{"role": "user", "content": "Tell me about Japan."}]
    search_type = "text"

    # Mocking context retrieval to return empty results
    with patch('main_retrieval_generation.context_retreival_obj.search_text', return_value=[]):
        retriever_gen = RetrievalGeneration()

        # Mocking OpenAI response
        mock_openai_response = MagicMock()
        mock_openai_response.choices = [MagicMock(message=MagicMock(content="The capital of Japan is Tokyo."))]
        with patch.object(retriever_gen.client.chat.completions, 'create', return_value=mock_openai_response):
            new_conversation = retriever_gen.generate_reply_texts(question, existing_conversation, search_type)
        
        # Check that the assistant response is appended correctly despite no context
        assert len(new_conversation) == 3
        assert new_conversation[-1]["role"] == "assistant"
        assert new_conversation[-1]["content"] == "The capital of Japan is Tokyo."

# Test exception handling in generate_reply_texts method
def test_generate_reply_texts_exception_handling():
    question = "What is the capital of Spain?"
    existing_conversation = [{"role": "user", "content": "Tell me about Spain."}]
    search_type = "text"
    
    # Mock context retrieval to raise an exception
    with patch('main_retrieval_generation.context_retreival_obj.search_text', side_effect=Exception("Test error")):
        retriever_gen = RetrievalGeneration()

        # Mock OpenAI response (though it will not be used due to the exception)
        mock_openai_response = MagicMock()
        mock_openai_response.choices = [MagicMock(message=MagicMock(content="The capital of Spain is Madrid."))]

        with patch.object(retriever_gen.client.chat.completions, 'create', return_value=mock_openai_response):
            # Generate the reply text, which should handle the exception during context retrieval
            new_conversation = retriever_gen.generate_reply_texts(question, existing_conversation, search_type)
        
        # Check that an exception is raised and handled
        assert len(new_conversation) == 3  # Conversation should not change due to the exception
        assert new_conversation[-2]["role"] == "user"
        assert new_conversation[-2]["content"] == question