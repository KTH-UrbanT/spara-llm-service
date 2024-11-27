import json 
from knowledge_base.main_KB import *
from context_retrieval.main_context_retrieval import *

from openai import AzureOpenAI  
from dotenv import load_dotenv

load_dotenv()


main_config = json.load(open('main_config.json'))



vector_database_obj = VectorDataBase(indexing_policy = main_config['indexing_policy'] , 
                                    vector_embedding_policy = main_config['vector_embedding_policy'] , 
                                    database_name = main_config['database_name'] , 
                                    container_name = main_config['container_name'])
vector_database_obj.setup_connection()

context_retreival_obj = RetrievalText(vector_search = vector_database_obj.vector_search , 
                                        openai_embeddings = vector_database_obj.openai_embeddings)

print('Context Retreival has been established')



class RetrievalGeneration:
    def __init__(self , context_retreival_obj):
        self.context_retreival_obj = context_retreival_obj
        endpoint = os.getenv("ENDPOINT_URL")  
        self.deployment = os.getenv("DEPLOYMENT_NAME")  
        subscription_key = os.getenv("AZURE_OPENAI_API_KEY")
        self.client = AzureOpenAI(  
                azure_endpoint=endpoint,  
                api_key=subscription_key,  
                api_version="2024-05-01-preview",  
            )  

    def generate_reply(self , question , prompt) :
        docs = context_retreival_obj.search_text_with_score(question , 3)
        content_from_doc = ' '.join(i[0].page_content for  i in docs)
        prompt.append({'role' : 'user' , 'content' : content_from_doc})
        completion = self.client.chat.completions.create(  
        model=self.deployment,  
        messages=prompt,  
        max_tokens=800,  
        temperature=0.7,  
        top_p=0.95,  
        frequency_penalty=0,  
        presence_penalty=0,  
        stop=None,  
        stream=False  
    )
        return completion.choices[0].message.content
        
