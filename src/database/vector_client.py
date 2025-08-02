import os
import json
import sys
from dotenv import load_dotenv

# ✅ Ensure project root is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from knowledge_base.vector_database import VectorDataBase
from context_retrieval.main_context_retrieval import RetrievalText


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

        print("✅ VectorClient initialized and connected.")

    def query(self, question: str):
        """
        Perform a vector search and return structured results.

        Args:
            question (str): User input question.

        Returns:
            list[dict]: List of results with page_content and metadata.
        """
        try:
            docs = self.context_retriever.search_vector(question)
            return [
                {
                    "page_content": doc.page_content,
                    "metadata": doc.metadata
                }
                for doc in docs
            ]
        except Exception as e:
            print(f"[VectorClient Error] Failed to retrieve documents: {e}")
            return []
