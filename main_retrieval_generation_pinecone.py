# Import necessary libraries
import json  # For loading the configuration file
from knowledge_base.vector_database_pinecone import VectorDataBase  # For interacting with the vector database
from context_retrieval.main_content_retrieval_pinecone import *  # For context retrieval logic
import os  # For environment variables
import backoff
from openai import AzureOpenAI, RateLimitError   # For interacting with OpenAI's API (Azure version)
from dotenv import load_dotenv  # For loading environment variables from .env file
import building_specs
import copy  # For creating deep copies of objects
import pandas as pd

data = pd.read_csv("buildings.csv", sep=";", usecols=["IdAdr", "El_calc", "EgiVarme_calc", "EgenAntalKallarplan", "EgenAntalPlan", "EgenAntalTrapphus", "EgiEnergiklass2020_calc", "HuvudsakligUppvarmning_calc"], skipinitialspace=True)
from typing import List, Tuple, Optional, Union

# Load environment variables from the .env file
load_dotenv()

# ----------------- Config -----------------
TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "5"))
# Optionally pass a Pinecone metadata filter as JSON (e.g., {"source_type":"policy"})
RETRIEVAL_FILTER_JSON = os.getenv("RETRIEVAL_FILTER_JSON", "").strip()
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "16000"))  # safety clamp

# Load configuration settings from a JSON file
main_config = json.load(open('main_config.json'))

# Initialize the context retrieval object (initially set to None)
context_retreival_obj = None

# Initialize and configure the vector database object with the settings from main_config
vector_database_obj = VectorDataBase(
    indexing_policy=main_config['indexing_policy'],
    vector_embedding_policy=main_config['vector_embedding_policy'],
    database_name=os.environ.get('database_name'),
    container_name=os.environ.get('container_name')
)

# Set up the connection to the vector database (Pinecone + Azure embeddings)
vector_database_obj.setup_connection()

# Initialize the context retrieval object using the vector search from vector_database_obj
context_retreival_obj = RetrievalText(
    vector_search=vector_database_obj.vector_search,
    openai_embeddings=vector_database_obj.openai_embeddings,
    top_k=TOP_K
)

# Print a confirmation message indicating that the context retrieval system has been established
print('Context Retrieval has been established')


# ----------------- Helpers -----------------
def _normalize_type(t: Optional[str]) -> str:
    return (t or "text").strip().lower()

def _parse_filter(json_str: str) -> Optional[dict]:
    if not json_str:
        return None
    try:
        return json.loads(json_str)
    except Exception:
        return None

def _gather_context_blob(
    docs: Union[List[Document], List[Tuple[Document, float]]],
    include_scores: bool
) -> str:
    """
    Turn retrieved docs (or doc,score tuples) into a context string, clamped by MAX_CONTEXT_CHARS.
    """
    parts: List[str] = []
    total = 0
    for item in docs:
        if include_scores:
            doc, score = item  # type: ignore
            line = f"{doc.page_content}"
        else:
            doc = item  # type: ignore
            line = f"{doc.page_content}"
        if not line:
            continue
        # Clamp progressively to avoid huge prompts
        remaining = max(0, MAX_CONTEXT_CHARS - total)
        if remaining <= 0:
            break
        snippet = line[:remaining]
        parts.append(snippet)
        total += len(snippet)

    return "\n\n".join(parts)

def _sources_list(docs: Union[List[Document], List[Tuple[Document, float]]], include_scores: bool) -> List[str]:
    seen = set()
    out = []
    for item in docs:
        doc = item[0] if include_scores else item  # type: ignore
        src = (doc.metadata or {}).get("source")
        if src and src not in seen:
            seen.add(src)
            out.append(str(src))
    return out


class RetrievalGeneration:
    """
    A class to handle generating responses based on Pinecone-powered context retrieval
    and existing conversation history using OpenAI's language model (Azure).
    """
    def __init__(self):
        """
        Initialize by setting up the context retrieval object and Azure OpenAI client from env.
        """
        global context_retreival_obj  # Reference the global context_retreival_obj
        self.context_retreival_obj = context_retreival_obj

        # Load the Azure endpoint, deployment name, and API key from environment variables
        endpoint = os.getenv("AZURE_ENDPOINT")
        self.deployment = os.getenv("LANGUAGE_MODEL_DEPLOYMENT_NAME")
        subscription_key = os.getenv("OPENAI_API_KEY")

        # Initialize the Azure OpenAI client
        self.client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=subscription_key,
            api_version=os.getenv("LANGUAGE_MODEL_API_VERSION"),
        )

        # Optional metadata filter passed to Pinecone
        self.metadata_filter = _parse_filter(RETRIEVAL_FILTER_JSON)

    @backoff.on_exception(backoff.expo, RateLimitError, max_tries=5, jitter=None)
    def _create_completion(self, new_conversation):
        """
        Helper function to call Azure OpenAI with automatic retries on RateLimitError.
        """
        return self.client.chat.completions.create(
            model=self.deployment,
            messages=new_conversation,
            max_tokens=800,
            temperature=0.1,
            top_p=0.95,
            frequency_penalty=0,
            presence_penalty=0,
            stop=None,
            stream=False
        )
    def find_building_rows_for_question(self , question: str, df: pd.DataFrame):
        """
        Very simple address detection:
        - For each IdAdr, if it appears as a substring in the question (case-insensitive),
          include that row.
        - This assumes users usually type the full address ("Annebodavägen 39").
        """
        q_lower = question.lower()
        matches = []
    
        for _, row in df.iterrows():
            addr = str(row["IdAdr"])
            if addr and addr.lower() in q_lower:
                matches.append(row.to_dict())
    
        return matches
    def generate_reply_texts(self, question, existing_conversation, type):
        """
        Generate a reply based on the question and the type of search ('text', 'text_with_score', 'vector', 'hybrid').
        Returns updated conversation.
        """
        # Keep only role/content from prior messages
        new_conversation = copy.deepcopy([
            {key: value for key, value in message.items() if key in ["role", "content"]}
            for message in existing_conversation
        ])

        t = _normalize_type(type)

        try:
            # Retrieve context from Pinecone (via RetrievalText)
            if t == 'text':
                docs = self.context_retreival_obj.search_text(
                    question, top_k=TOP_K, metadata_filter=self.metadata_filter
                )
                content_from_doc = _gather_context_blob(docs, include_scores=False)

            elif t == 'text_with_score':
                docs = self.context_retreival_obj.search_text_with_score(
                    question, top_k=TOP_K, metadata_filter=self.metadata_filter
                )
                content_from_doc = _gather_context_blob(docs, include_scores=True)

            elif t == 'vector':
                # Here we embed the question to a vector internally (kept for API compat).
                docs = self.context_retreival_obj.search_vector(
                    question, top_k=TOP_K, metadata_filter=self.metadata_filter
                )
                content_from_doc = _gather_context_blob(docs, include_scores=False)

            elif t == 'hybrid':
                # Currently same as semantic text unless you add sparse signals in RetrievalText
                docs = self.context_retreival_obj.hybrid_search(
                    question, top_k=TOP_K, metadata_filter=self.metadata_filter
                )
                content_from_doc = _gather_context_blob(docs, include_scores=False)

            else:
                # Fallback to text search if unknown type provided
                docs = self.context_retreival_obj.search_text(
                    question, top_k=TOP_K, metadata_filter=self.metadata_filter
                )
                content_from_doc = _gather_context_blob(docs, include_scores=False)

        except Exception as e:
            print(f"[Retrieval Error] {e}")
            docs = []
            content_from_doc = ""

        # Optional: include a compact sources list under the context
        srcs = _sources_list(docs, include_scores=(t == "text_with_score"))
        sources_block = ""
        if srcs:
            sources_block = "\n\nSources:\n- " + "\n- ".join(srcs)
        matched_rows = self.find_building_rows_for_question(question, data)
        buildings_json = json.dumps(matched_rows, ensure_ascii=False)
        # Append the user's question along with the retrieved context and domain-specific addendum
        new_conversation.append({
            "role": "user",
            "content": (
                question
                + "\n\nContext:\n"
                + (content_from_doc or "")
                + sources_block
                + "\n\nBUILDINGS_JSON:\n"
                + buildings_json
            )
        })

        try:
            completion = self._create_completion(new_conversation)
        except Exception as e:
            print(f"[Error] Failed to generate completion: {e}")
            return new_conversation  # Return partial conversation if completion fails

        # Reset the last 'user' message content to just the question
        new_conversation[-1]['content'] = question
        # Append the generated assistant reply
        new_conversation.append({
            "role": "assistant",
            "content": completion.choices[0].message.content
        })

        return new_conversation
