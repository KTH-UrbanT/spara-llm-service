# context_retrieval/main_context_retrieval.py
from typing import List, Tuple, Optional
from langchain_core.documents import Document

DEFAULT_TOP_K = 5

class RetrievalText:
    """
    Uses the vector_search (Pinecone) + Azure embeddings to provide:
      - search_text: semantic search returning Documents
      - search_text_with_score: semantic search returning (Document, score)
      - search_vector: explicit vector search (if you precomputed a query vector)
      - hybrid_search: semantic search placeholder (same as search_text unless you add sparse/BM25 later)
    """

    def __init__(self, vector_search, openai_embeddings, top_k: int = DEFAULT_TOP_K):
        self.vector_search = vector_search
        self.openai_embeddings = openai_embeddings
        self.top_k = top_k

    # --- TEXT QUERIES (compute embedding from text) ---
    def search_text(self, query: str, top_k: Optional[int] = None, metadata_filter: Optional[dict] = None) -> List[Document]:
        k = top_k or self.top_k
        return self.vector_search.query_text(query, top_k=k, metadata_filter=metadata_filter, with_scores=False)

    def search_text_with_score(self, query: str, top_k: Optional[int] = None, metadata_filter: Optional[dict] = None) -> List[Tuple[Document, float]]:
        k = top_k or self.top_k
        return self.vector_search.query_text(query, top_k=k, metadata_filter=metadata_filter, with_scores=True)

    # --- VECTOR QUERIES (you provide a vector) ---
    def search_vector(self, query: str, top_k: Optional[int] = None, metadata_filter: Optional[dict] = None) -> List[Document]:
        """
        To keep your original API, we allow a string 'query' but treat it as text and embed it.
        If you actually have a pre-computed vector, add a sibling method that accepts a list[float].
        """
        k = top_k or self.top_k
        vec = self.openai_embeddings.embed_query(query)
        return self.vector_search.query_vector(vec, top_k=k, metadata_filter=metadata_filter, with_scores=False)

    # --- HYBRID QUERIES ---
    def hybrid_search(self, query: str, top_k: Optional[int] = None, metadata_filter: Optional[dict] = None) -> List[Document]:
        """
        Placeholder hybrid (semantic only). To do true hybrid, you’d store sparse signals (e.g., BM25)
        alongside dense vectors and pass both to Pinecone. Until then, use the dense semantic search.
        """
        return self.search_text(query, top_k=top_k, metadata_filter=metadata_filter)
