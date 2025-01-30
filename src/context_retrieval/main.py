from abc import ABC, abstractmethod
from typing import List
from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.vectorstores import FAISS
from langchain.chains import RetrievalQA
from langchain.docstore.document import Document


# Document class to store content and metadata
class Document:
    def __init__(self, doc_id: str, content: str, metadata: dict = None):
        self.id = doc_id
        self.content = content
        self.metadata = metadata or {}

    def __repr__(self):
        return f"Document(id={self.id}, content={self.content[:30]}, metadata={self.metadata})"


# Abstract class defining a retrieval method
class RetrievalMethod(ABC):
    @abstractmethod
    def retrieve(self, query: str) -> List[Document]:
        pass


# Concrete class for keyword-based retrieval
class KeywordRetrieval(RetrievalMethod):
    def __init__(self, document_store):
        self.document_store = document_store

    def retrieve(self, query: str) -> List[Document]:
        # Mocking keyword retrieval, replace with actual logic
        return [doc for doc in self.document_store if query.lower() in doc.content.lower()]


# Concrete class for embedding-based retrieval
class EmbeddingRetrieval(RetrievalMethod):
    def __init__(self, vector_store):
        self.vector_store = vector_store

    def retrieve(self, query: str) -> List[Document]:
        # Retrieve by embedding using LangChain (FAISS in this case)
        docs = self.vector_store.similarity_search(query, k=3)
        return docs


# Concrete class for hybrid retrieval (combination of keyword and embeddings)
class HybridRetrieval(RetrievalMethod):
    def __init__(self, keyword_retriever: KeywordRetrieval, embedding_retriever: EmbeddingRetrieval):
        self.keyword_retriever = keyword_retriever
        self.embedding_retriever = embedding_retriever

    def retrieve(self, query: str) -> List[Document]:
        # Retrieve by both keyword and embeddings
        keyword_results = self.keyword_retriever.retrieve(query)
        embedding_results = self.embedding_retriever.retrieve(query)
        return list(set(keyword_results + embedding_results))  # Combine results, removing duplicates


# Main RAG context retriever class
class RAGContextRetriever:
    def __init__(self, keyword_retriever: KeywordRetrieval, embedding_retriever: EmbeddingRetrieval, hybrid_retriever: HybridRetrieval):
        self.keyword_retriever = keyword_retriever
        self.embedding_retriever = embedding_retriever
        self.hybrid_retriever = hybrid_retriever

    def retrieveContext(self, query: str) -> List[Document]:
        # Use hybrid retrieval by default
        return self.hybrid_retriever.retrieve(query)

    def retrieveByKeyword(self, query: str) -> List[Document]:
        return self.keyword_retriever.retrieve(query)

    def retrieveByEmbedding(self, query: str) -> List[Document]:
        return self.embedding_retriever.retrieve(query)

    def retrieveByHybrid(self, query: str) -> List[Document]:
        return self.hybrid_retriever.retrieve(query)

    def aggregateResults(self, results: List[Document]) -> List[Document]:
        # Mock aggregation logic, simply returning the results without duplicates
        return list(set(results))


# Example usage of the classes
def main():
    # Sample document store (for keyword retrieval)
    doc1 = Document(doc_id="1", content="This is a document about AI.", metadata={"source": "doc1"})
    doc2 = Document(doc_id="2", content="This is a document about machine learning.", metadata={"source": "doc2"})
    doc3 = Document(doc_id="3", content="This document talks about deep learning.", metadata={"source": "doc3"})
    document_store = [doc1, doc2, doc3]

    # Initialize LangChain's FAISS vector store (for embedding retrieval)
    embeddings = OpenAIEmbeddings()
    vector_store = FAISS.from_texts([doc.content for doc in document_store], embeddings)

    # Initialize retrieval methods
    keyword_retriever = KeywordRetrieval(document_store)
    embedding_retriever = EmbeddingRetrieval(vector_store)
    hybrid_retriever = HybridRetrieval(keyword_retriever, embedding_retriever)

    # Initialize the main RAG context retriever
    rag_context_retriever = RAGContextRetriever(keyword_retriever, embedding_retriever, hybrid_retriever)

    # Retrieve context using hybrid retrieval
    query = "AI"
    results = rag_context_retriever.retrieveContext(query)
    print("Hybrid Retrieval Results:")
    for doc in results:
        print(doc)

    # Retrieve by keyword
    keyword_results = rag_context_retriever.retrieveByKeyword(query)
    print("\nKeyword Retrieval Results:")
    for doc in keyword_results:
        print(doc)

    # Retrieve by embedding
    embedding_results = rag_context_retriever.retrieveByEmbedding(query)
    print("\nEmbedding Retrieval Results:")
    for doc in embedding_results:
        print(doc)

    # Aggregate results
    aggregated_results = rag_context_retriever.aggregateResults(results)
    print("\nAggregated Results:")
    for doc in aggregated_results:
        print(doc)


if __name__ == "__main__":
    main()
