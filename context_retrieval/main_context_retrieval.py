'''
steps to follow. 
write code to extract docs based on 
1. text 
2. text then convert into embeddings and then search 
3. combine results from both search. 
'''

from  langchain_community.vectorstores.azure_cosmos_db import CosmosDBSimilarityType
import numpy as np

class RetrievalText : 
    def __init__(self , vector_search , openai_embeddings):
        """
        Initializes the RetrievalText class with vector search and OpenAI embeddings objects.

        Parameters:
        - vector_search: An instance of the vector search object (likely connected to Azure Cosmos DB).
        - openai_embeddings: An instance used to convert text queries into embeddings using OpenAI models.
        """
        self.vector_search = vector_search
        self.openai_embeddings = openai_embeddings
        
    def search_text(self , query , kind = 'vector-ivf' , number_of_docs = 5) : 
        """
        Performs a text-based search to find similar documents in the vector database.

        Parameters:
        - query: The text query to search for.
        - kind: The kind of similarity search to perform (default is 'vector-ivf').
        - number_of_docs: The number of documents to return (default is 5).

        Returns:
        - A list of the most similar documents based on the text search.
        """
        try : 
            return self.vector_search.similarity_search(query , k = number_of_docs , kind = kind)
        except Exception as e : 
            print(e)
            return e
        
    def search_text_with_score(self , query ,  number_of_docs = 5) :
        """
        Performs a text-based search and returns the similarity scores for each document.

        Parameters:
        - query: The text query to search for.
        - number_of_docs: The number of documents to return (default is 5).

        Returns:
        - A list of the most similar documents with their similarity scores.
        """
        try : 
            return self.vector_search.similarity_search_with_score(query , k = number_of_docs)
        except Exception as e : 
            print(e)
            return None
    
    def search_vector(self , query , number_of_docs = 5) : 
        """
        Converts the query into embeddings and performs a search using those embeddings. (Not used in current implementation)

        Parameters:
        - query: The text query to search for.
        - number_of_docs: The number of documents to return (default is 5).

        Returns:
        - A list of the most similar documents based on the embeddings search.
        """
        try : 
            return self.vector_search.max_marginal_relevance_search_by_vector( self.openai_embeddings.embed_query(query) , k= number_of_docs , pre_filter=None , with_embedding=None)
        except Exception as e : 
            print(e)
            return None
    
    def hybrid_search(self , query , kind = 'vector-ivf' , number_of_docs = 5) : 
        """
        Performs a hybrid search, combining both text-based and embedding-based searches.

        Parameters:
        - query: The text query to search for.
        - kind: The kind of similarity search to perform (default is 'vector-ivf').
        - number_of_docs: The number of documents to return (default is 5).

        Returns:
        - A list of documents combining results from both text-based and embedding-based searches.
        """
        text_result = self.search_text(query , kind , number_of_docs) 
        embeddings_result = self.search_vector(query , number_of_docs)
        final_list = [*text_result, *embeddings_result]
        return final_list
    
    