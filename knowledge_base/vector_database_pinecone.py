# knowledge_base/vector_database.py
import os
import time
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from pinecone import Pinecone
from langchain_openai import AzureOpenAIEmbeddings
from langchain_core.documents import Document

# ---- Config via env ----
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY") or os.getenv("pinecone_api")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "doc-embeddings")
PINECONE_NAMESPACE = os.getenv("PINECONE_NAMESPACE", "docs")
PINECONE_TEXT_FIELD = os.getenv("PINECONE_TEXT_FIELD", "chunk_text")  # where chunk text lives in metadata

# Azure OpenAI (embeddings)
AZURE_DEPLOYMENT = os.getenv("OPENAI_EMBEDDINGS_MODEL_DEPLOYMENT")
AZURE_API_VERSION = os.getenv("OPENAI_API_VERSION")
AZURE_ENDPOINT = os.getenv("AZURE_ENDPOINT")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


class PineconeVectorSearch:
    """
    Thin wrapper over Pinecone index for querying with AzureOpenAIEmbeddings.
    """

    def __init__(self, pc: Pinecone, index_name: str, namespace: str, embeddings: AzureOpenAIEmbeddings):
        self.pc = pc
        self.index = pc.Index(index_name)
        self.namespace = namespace
        self.embeddings = embeddings

    def _metadata_to_text(self, md: dict) -> str:
        # Be flexible: try common keys for where the chunk content might be stored
        if not md:
            return ""
        for key in (PINECONE_TEXT_FIELD, "text", "page_content", "content", "preview"):
            if key in md and isinstance(md[key], str) and md[key].strip():
                return md[key]
        return ""

    def _to_documents(self, matches, include_score: bool = False):
        docs_or_pairs = []
        for m in matches or []:
            md = m.get("metadata", {}) or {}
            txt = self._metadata_to_text(md)
            doc = Document(page_content=txt, metadata=md)
            if include_score:
                docs_or_pairs.append((doc, float(m.get("score", 0.0))))
            else:
                docs_or_pairs.append(doc)
        return docs_or_pairs

    def query_vector(self, vector, top_k: int = 5, metadata_filter: Optional[dict] = None, with_scores: bool = False):
        res = self.index.query(
            namespace=self.namespace,
            vector=vector,
            top_k=top_k,
            include_metadata=True,
        ) if metadata_filter is None else self.index.query(
            namespace=self.namespace,
            vector=vector,
            top_k=top_k,
            include_metadata=True,
            filter=metadata_filter,
        )
        matches = res.get("matches", []) if isinstance(res, dict) else getattr(res, "matches", [])
        return self._to_documents(matches, include_score=with_scores)

    def query_text(self, text: str, top_k: int = 5, metadata_filter: Optional[dict] = None, with_scores: bool = False):
        vec = self.embeddings.embed_query(text)
        return self.query_vector(vec, top_k=top_k, metadata_filter=metadata_filter, with_scores=with_scores)


class VectorDataBase:
    """
    Creates Azure embeddings + Pinecone client and exposes:
      - self.openai_embeddings  (AzureOpenAIEmbeddings)
      - self.vector_search      (PineconeVectorSearch)
    The ctor keeps your original signature, but indexing_policy / vector_embedding_policy
    are not used here (kept for compatibility with your config).
    """

    def __init__(self, indexing_policy, vector_embedding_policy, database_name: str, container_name: str):
        if not all([AZURE_DEPLOYMENT, AZURE_API_VERSION, AZURE_ENDPOINT, OPENAI_API_KEY]):
            raise RuntimeError(
                "Missing Azure OpenAI env. Set OPENAI_EMBEDDINGS_MODEL_DEPLOYMENT, OPENAI_API_VERSION, "
                "AZURE_ENDPOINT, OPENAI_API_KEY."
            )
        if not PINECONE_API_KEY:
            raise RuntimeError("Missing Pinecone API key. Set PINECONE_API_KEY or pinecone_api.")

        # Build embeddings (Azure)
        self.openai_embeddings = AzureOpenAIEmbeddings(
            azure_deployment=AZURE_DEPLOYMENT,
            api_version=AZURE_API_VERSION,
            azure_endpoint=AZURE_ENDPOINT,
            openai_api_key=OPENAI_API_KEY,
        )

        # Pinecone client + index handle
        self.pc = Pinecone(api_key=PINECONE_API_KEY)
        self.index_name = PINECONE_INDEX_NAME
        self.namespace = PINECONE_NAMESPACE

        # Vector search wrapper (created after connection is verified)
        self.vector_search: Optional[PineconeVectorSearch] = None

    def setup_connection(self):
        # Validate index exists (we assume it was created during ingestion)
        if not self.pc.has_index(self.index_name):
            raise RuntimeError(
                f"Pinecone index '{self.index_name}' not found. "
                f"Create it and ingest vectors before searching."
            )

        # optional: quick readiness check (usually immediate)
        desc = self.pc.describe_index(self.index_name)
        if not desc.get("status", {}).get("ready", True):
            # poll briefly
            for _ in range(30):
                time.sleep(1)
                if self.pc.describe_index(self.index_name).get("status", {}).get("ready", False):
                    break

        # build the search adapter
        self.vector_search = PineconeVectorSearch(
            pc=self.pc,
            index_name=self.index_name,
            namespace=self.namespace,
            embeddings=self.openai_embeddings,
        )
