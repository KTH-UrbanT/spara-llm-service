# Import necessary libraries
import json  # For loading the configuration file
from knowledge_base.vector_database import VectorDataBase  # For interacting with the vector database
from context_retrieval.main_context_retrieval import *  # For context retrieval logic
import os  # For environment variables
import backoff
from openai import AzureOpenAI, RateLimitError   # Correctly import error classes  # For interacting with OpenAI's API (Azure version)
from dotenv import load_dotenv  # For loading environment variables from .env file
import building_specs
# Load environment variables from the .env file
load_dotenv()

import copy  # For creating deep copies of objects

# Load configuration settings from a JSON file
main_config = json.load(open('main_config.json'))

# Initialize the context retrieval object (initially set to None)
context_retreival_obj = None

# Initialize and configure the vector database object with the settings from main_config
vector_database_obj = VectorDataBase(
    indexing_policy=main_config['indexing_policy'], 
    vector_embedding_policy=main_config['vector_embedding_policy'], 
    database_name=os.environ.get('database_name'), 
    container_name=os.environ.get('container_name')
)

# Set up the connection to the vector database
vector_database_obj.setup_connection()

# Initialize the context retrieval object using the vector search from vector_database_obj
context_retreival_obj = RetrievalText(
    vector_search=vector_database_obj.vector_search, 
    openai_embeddings=vector_database_obj.openai_embeddings
)

# Print a confirmation message indicating that the context retrieval system has been established
print('Context Retrieval has been established')



class RetrievalGeneration:
    """
    A class to handle the process of generating responses based on context retrieval 
    and existing conversation history using OpenAI's language model (Azure version).
    """
    def __init__(self):
        """
        Initialize the RetrievalGeneration object by setting up the context retrieval object 
        and Azure OpenAI client using environment variables.
        """
        global context_retreival_obj # Reference the global context_retreival_obj
        self.context_retreival_obj = context_retreival_obj
        self.buildings = building_specs.Building_specs()
        
        # Load the Azure endpoint, deployment name, and API key from environment variables
        endpoint = os.getenv("AZURE_ENDPOINT")  
        self.deployment = os.getenv("LANGUAGE_MODEL_DEPLOYMENT_NAME")  
        subscription_key = os.getenv("OPENAI_API_KEY")
        
        # Initialize the Azure OpenAI client
        self.client = AzureOpenAI(  
                azure_endpoint=endpoint,  
                api_key=subscription_key,  
                api_version = os.getenv("LANGUAGE_MODEL_API_VERSION"),  
            )  
    @backoff.on_exception(backoff.expo,
                          RateLimitError,
                          max_tries=5,
                          jitter=None)
    def _create_completion(self, new_conversation):
        """
        Helper function to call Azure OpenAI with automatic retries on RateLimitError.
        """
        return self.client.chat.completions.create(
            model=self.deployment,
            messages=new_conversation,
            max_tokens=800,
            temperature=0.1,
            top_p=0.95,
            frequency_penalty=0,
            presence_penalty=0,
            stop=None,
            stream=False
        )
    def generate_reply_texts(self, question, existing_conversation, session_id_int,type):
        """
        Generate a reply based on the question and the type of search (text-based or vector-based).

        Parameters:
        - question: The user's question to generate a reply for.
        - existing_conversation: The previous conversation history to provide context.
        - type: The type of search ('text', 'text_with_score', 'vector', or 'hybrid') to retrieve context.

        Returns:
        - new_conversation: The updated conversation with the newly generated assistant reply.
        """
        #new_conversation = copy.deepcopy(existing_conversation)
        # new_conversation = copy.deepcopy([
        #                             {key: value for key, value in message.items() if key not in ["timestamp" , "added_to_database"]}
        #                             for message in existing_conversation
        #                         ])
        new_conversation = copy.deepcopy([
                                        {key: value for key, value in message.items() if key in ["role", "content"]}
                                        for message in existing_conversation
                                    ])
        try:
            # Depending on the 'type' parameter, retrieve relevant documents from the context retrieval system
            if type == 'text':
                docs = self.context_retreival_obj.search_text(question)
                content_from_doc = ' '.join(i.page_content for i in docs)
            elif type == 'text_with_score':
                docs = self.context_retreival_obj.search_text_with_score(question)
                content_from_doc = ' '.join(i[0].page_content for i in docs)
            elif type == 'vector':
                docs = self.context_retreival_obj.search_vector(question)
                content_from_doc = ' '.join(i.page_content for i in docs)
            elif type == 'hybrid':
                docs = self.context_retreival_obj.hybrid_search(question)
                content_from_doc = ' '.join(i.page_content for i in docs)
        except Exception as e:
            print(f"Error during context retrieval: {e}")
            docs = []  # Set docs to an empty list if an exception occurs
            content_from_doc = ""  # Set content to empty string

        # Append the user's question along with the context from the retrieved documents to the conversation
        new_conversation.append({
            "role": "user",
            "content": question + "\n Context : " + content_from_doc +
               (self.buildings.update_address(question) if (session_id_int % 2 == 0) else "")
            })


        print(new_conversation)


        try:
            completion = self._create_completion(new_conversation)
        except Exception as e:
            print(f"[Error] Failed to generate completion: {e}")
            return new_conversation  # Return partial conversation if completion fails

        # Generate the assistant's reply using the OpenAI model based on the updated conversation
        # completion = self.client.chat.completions.create(
        #     model=self.deployment,
        #     messages=new_conversation,  # Provide the conversation history as context
        #     max_tokens=800,  # Limit the maximum number of tokens in the reply
        #     temperature=0.1,  # Set the temperature to control randomness of the output
        #     top_p=0.95,  # Set the cumulative probability for sampling
        #     frequency_penalty=0,  # No penalty for frequent tokens
        #     presence_penalty=0,  # No penalty for repeating content
        #     stop=None,  # No explicit stop sequence
        #     stream=False  # Do not stream the response
        # )

        # Update the conversation with the assistant's reply
        new_conversation[-1]['content'] = question  # Reset the last 'user' message content to just the question
        new_conversation.append({
            "role": "assistant",
            "content": completion.choices[0].message.content  # Append the generated assistant reply
        })

        # Return the updated conversation
        return new_conversation
