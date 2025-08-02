from azure.cosmos import CosmosClient, PartitionKey 
from src.knowledge_base.azure_cosmos_custom import AzureCosmosDBNoSqlVectorSearchCustom
from langchain_openai import AzureOpenAIEmbeddings
import os
from dotenv import load_dotenv

load_dotenv()

class VectorDataBase : 
    """
    A class that provides functionality to interact with Azure Cosmos DB for storing and searching 
    vector embeddings. It integrates with the Azure OpenAI embeddings to generate vector embeddings 
    and perform vector-based searches.
    """
    def __init__(self , indexing_policy : dict , vector_embedding_policy : dict , database_name : str, container_name : str):
        """
        Initializes the VectorDataBase class.

        Parameters:
        - indexing_policy (dict): Indexing configuration for Cosmos DB.
        - vector_embedding_policy (dict): Policy for vector embeddings in the Cosmos DB container.
        - database_name (str): The name of the Cosmos DB database.
        - container_name (str): The name of the Cosmos DB container.
        """
        self.indexing_policy = indexing_policy
        self.indexing_policy = {
                                    "indexingMode": "consistent",
                                    "includedPaths": [{"path": "/*"}],
                                    "excludedPaths": [{"path": '/"_etag"/?'}],
                                    "vectorIndexes": [{"path": "/embedding", "type": "quantizedFlat"}],
                                }
        self.vector_embedding_policy = vector_embedding_policy
        self.database_name = database_name
        self.container_name = container_name
        self.setup_connection() 

    def setup_connection(self): 
        """
        Establishes a connection to the Cosmos DB and initializes the vector search system 
        using Azure OpenAI embeddings.

        Sets up the Cosmos DB client and the vector search custom implementation.
        """        
        self.cosmos_client = CosmosClient(os.environ.get('HOST'), os.environ.get('KEY'))
        partition_key = PartitionKey(path="/id")
        cosmos_container_properties = {"partition_key": partition_key}
        cosmos_database_properties = {"id": self.database_name}

        self.openai_embeddings = AzureOpenAIEmbeddings(
            azure_deployment = os.environ.get('OPENAI_EMBEDDINGS_MODEL_DEPLOYMENT'),
            api_version = os.environ.get('OPENAI_API_VERSION'),
            azure_endpoint = os.environ.get('AZURE_ENDPOINT'),
            openai_api_key = os.environ.get('OPENAI_API_KEY'),
        )
        try : 
            self.vector_search = AzureCosmosDBNoSqlVectorSearchCustom(
                embedding = self.openai_embeddings,
                cosmos_client = self.cosmos_client,
                database_name = self.database_name,
                container_name = self.container_name,
                vector_embedding_policy = self.vector_embedding_policy,
                indexing_policy = self.indexing_policy,
                cosmos_container_properties=cosmos_container_properties,
                cosmos_database_properties = cosmos_database_properties
            )
            print('Connection to Vector Database established.')
        except Exception as e: 
            print('Connection to Cosmos NOSQL DB not established')
            print(e)
            
    def get_document_source(self) : 
        """
        Queries the Cosmos DB container to retrieve the distinct sources of the documents 
        stored in the database.

        Returns:
        - list: A list of distinct document sources from the Cosmos DB container.
        """
        database = self.cosmos_client.get_database_client(self.database_name)
        container = database.get_container_client(self.container_name)
        query = f"SELECT DISTINCT c.metadata.source FROM c"

        # Run the query
        items = list(container.query_items(query=query, enable_cross_partition_query=True))
        return items
    
    def upload_file_vector_db(self , docs ) : 
        """
        Uploads documents to the Cosmos DB vector database by generating vector embeddings 
        and adding the documents to the vector search system.

        Parameters:
        - docs (list): A list of documents to be added to the vector database.

        Returns:
        - list: A list of document IDs generated after adding the documents to the vector database.
        """
        try : 
            document_id_list = self.vector_search.add_documents(documents = docs)
            print('Documents have been added.')
        except Exception as e:
            print('Document addition failed. The error is \n')
            print(e)
        return document_id_list
    
    def delete_container_db(self) : 
        """
        Deletes the entire container from the Cosmos DB.

        This method is currently not complete. It should handle the deletion of the Cosmos DB container.
        """
        self.cosmos_client.delete