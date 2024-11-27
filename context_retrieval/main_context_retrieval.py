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
        self.vector_search = vector_search
        self.openai_embeddings = openai_embeddings
        
    def search_text(self , query , kind = 'vector-ivf' , number_of_docs = 5) : 
        try : 
            return self.vector_search.similarity_search(query , k = number_of_docs , kind = kind)
        except Exception as e : 
            print(e)
            return e
        
    def search_text_with_score(self , query ,  number_of_docs = 5) : 
        try : 
            return self.vector_search.similarity_search_with_score(query , k = number_of_docs)
        except Exception as e : 
            print(e)
            return None
    
    def search_vector(self , query , number_of_docs = 5) : 
        '''
        Not used
        '''
        try : 
            return self.vector_search.similarity_search_by_vector( self.openai_embeddings.embed_query(query) ,number_of_docs)
        except Exception as e : 
            print(e)
            return None
    
    def hybrid_search(self , query , kind = 'vector-ivf' , number_of_docs = 5) : 
        '''
        Not used
        '''
        text_result = self.search_text(query , kind , number_of_docs) 
        embeddings_result = self.search_vector(query , number_of_docs)
        return text_result , embeddings_result
    
    '''def _similarity_search_with_score(
        self,
        embeddings: List[float],
        k: int = 4,
        pre_filter: Optional[Dict] = None,
        with_embedding: bool = False,
    ) -> List[Tuple[Document, float]]:
        query = "SELECT "

        # If limit_offset_clause is not specified, add TOP clause
        if pre_filter is None or pre_filter.get("limit_offset_clause") is None:
            query += "TOP @limit "

        query += (
            "c.id, c[@embeddingKey], c.text, c.metadata, "
            "VectorDistance(c[@embeddingKey], @embeddings) AS SimilarityScore FROM c"
        )

        # Add where_clause if specified
        if pre_filter is not None and pre_filter.get("where_clause") is not None:
            query += " {}".format(pre_filter["where_clause"])

        query += " ORDER BY VectorDistance(c[@embeddingKey], @embeddings)"

        # Add limit_offset_clause if specified
        if pre_filter is not None and pre_filter.get("limit_offset_clause") is not None:
            query += " {}".format(pre_filter["limit_offset_clause"])
        parameters = [
            {"name": "@limit", "value": k},
            {"name": "@embeddingKey", "value": self._embedding_key},
            {"name": "@embeddings", "value": embeddings},
        ]

        docs_and_scores = []

        items = list(
            self.container.query_items(
                query=query, parameters=parameters, enable_cross_partition_query=True
            )
        )
        for item in items:
            text = item["text"]
            metadata = item["metadata"]
            score = item["SimilarityScore"]
            if with_embedding:
                metadata[self._embedding_key] = item[self._embedding_key]
            docs_and_scores.append(
                (Document(page_content=text, metadata=metadata), score)
            )
        return docs_and_scores
    
    def max_marginal_relevance_search_by_vector(
        self,
        embedding: List[float],
        k: int = 4,
        fetch_k: int = 20,
        lambda_mult: float = 0.5,
        **kwargs: Any,
    ) -> List[Document]:
        # Retrieves the docs with similarity scores
        pre_filter = {}
        with_embedding = False
        if kwargs["pre_filter"]:
            pre_filter = kwargs["pre_filter"]
        if kwargs["with_embedding"]:
            with_embedding = kwargs["with_embedding"]
        docs = self._similarity_search_with_score(
            embeddings=embedding,
            k=fetch_k,
            pre_filter=pre_filter,
            with_embedding=with_embedding,
        )

        # Re-ranks the docs using MMR
        mmr_doc_indexes = maximal_marginal_relevance(
            np.array(embedding),
            [self.openai_embeddings.embed_query(doc) for doc , _ in docs],
            #[doc.metadata[self._embedding_key] for doc, _ in docs],
            k=k,
            lambda_mult=lambda_mult,
        )

        mmr_docs = [docs[i][0] for i in mmr_doc_indexes]
        return mmr_docs
    '''