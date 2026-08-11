from __future__ import annotations
import os
import json
import sys
import time
from typing import Optional


from dotenv import load_dotenv
from typing import List, Tuple, Optional, Union
# ✅ Ensure project root is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.knowledge_base.vector_database_pinecone import VectorDataBase
from src.pipeline.telemetry import record_retrieval_call
from src.context_retrieval.main_content_retrieval_pinecone import RetrievalText

TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "5"))
# Optionally pass a Pinecone metadata filter as JSON (e.g., {"source_type":"policy"})
RETRIEVAL_FILTER_JSON = os.getenv("RETRIEVAL_FILTER_JSON", "").strip()
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "16000"))  # safety clamp


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


class VectorClientConfig:
    def __init__(self, config_path=None):
        load_dotenv()
        if config_path is None:
            config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'main_config.json')

        with open(config_path, 'r') as f:
            self.config = json.load(f)

        self.database_name = os.getenv('database_name')
        self.container_name = os.getenv('container_name')

    def get_vector_config(self):
        return {
            "indexing_policy": self.config['indexing_policy'],
            "vector_embedding_policy": self.config['vector_embedding_policy'],
            "database_name": self.database_name,
            "container_name": self.container_name
        }


class VectorClient:
    def __init__(self, config: VectorClientConfig):
        vector_config = config.get_vector_config()

        self.vector_db = VectorDataBase(
            indexing_policy=vector_config['indexing_policy'],
            vector_embedding_policy=vector_config['vector_embedding_policy'],
            database_name=vector_config['database_name'],
            container_name=vector_config['container_name']
        )

        self.vector_db.setup_connection()

        self.context_retriever = RetrievalText(
            vector_search=self.vector_db.vector_search,
            openai_embeddings=self.vector_db.openai_embeddings
        )
        self.metadata_filter = _parse_filter(RETRIEVAL_FILTER_JSON)
        print("✅ VectorClient initialized and connected.")

    def query(self, question: str):
        """
        Perform a vector search and return structured results.

        Args:
            question (str): User input question.

        Returns:
            list[dict]: List of results with page_content and metadata.
        """
        started_at = time.perf_counter()
        try:
            docs = self.context_retriever.search_vector(
                question,
                top_k=TOP_K,
                metadata_filter=self.metadata_filter,
            )
            results = [
                {
                    "page_content": doc['metadata']['text'],
                    "metadata": doc['metadata']['source']
                }
                for doc in docs
            ]
            record_retrieval_call(
                component="vector_retrieval",
                latency_seconds=time.perf_counter() - started_at,
                result_count=len(results),
                success=True,
            )
            return results
        except Exception as e:
            print(f"[VectorClient Error] Failed to retrieve documents: {e}")
            record_retrieval_call(
                component="vector_retrieval",
                latency_seconds=time.perf_counter() - started_at,
                result_count=0,
                success=False,
                error=e,
            )
            return []
