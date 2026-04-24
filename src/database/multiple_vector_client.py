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

        # Support multiple DBs from config
        self.databases = self.config.get("vector_databases", {
            "default": {
                "indexing_policy": self.config['indexing_policy'],
                "vector_embedding_policy": self.config['vector_embedding_policy'],
                "database_name": os.getenv('database_name'),
                "container_name": os.getenv('container_name')
            }
        })

    def get_all_configs(self):
        return self.databases


class VectorClient:
    def __init__(self, config: VectorClientConfig):
        self.clients = {}

        db_configs = config.get_all_configs()

        for db_key, db_conf in db_configs.items():
            vector_db = VectorDataBase(
                indexing_policy=db_conf['indexing_policy'],
                vector_embedding_policy=db_conf['vector_embedding_policy'],
                database_name=db_conf['database_name'],
                container_name=db_conf['container_name']
            )
            vector_db.setup_connection()

            retrieval = RetrievalText(
                vector_search=vector_db.vector_search,
                openai_embeddings=vector_db.openai_embeddings
            )

            self.clients[db_key] = {
                "db": vector_db,
                "retrieval": retrieval
            }

        print(f"✅ Initialized VectorClient with databases: {list(self.clients.keys())}")

    def query(self, question: str, db_key: str = "default"):
        """
        Query a specific vector database by key.

        Args:
            question (str): The user query.
            db_key (str): Which database to query (default is 'default').

        Returns:
            list[dict]: Results from the vector search.
        """
        if db_key not in self.clients:
            raise ValueError(f"Database key '{db_key}' not found.")

        try:
            docs = self.clients[db_key]["retrieval"].search_vector(question)
            return [
                {
                    "page_content": doc.page_content,
                    "source": (doc.metadata or {}).get("source", ""),
                }
                for doc in docs
            ]
        except Exception as e:
            print(f"[VectorClient Error] Failed for DB '{db_key}': {e}")
            return []
