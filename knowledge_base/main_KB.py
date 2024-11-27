from langchain_community.document_loaders.pdf import PyPDFLoader
from langchain_community.document_loaders.word_document import UnstructuredWordDocumentLoader
from langchain_community.document_loaders.excel import UnstructuredExcelLoader
from langchain_community.document_loaders.text import TextLoader
from langchain_community.document_loaders.json_loader import JSONLoader
from langchain_community.document_loaders.powerpoint import UnstructuredPowerPointLoader
from langchain_community.document_loaders.html import UnstructuredHTMLLoader
from langchain_community.document_loaders.csv_loader import CSVLoader
from langchain_core.documents import Document

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

from azure.cosmos import CosmosClient, PartitionKey 
from langchain_community.vectorstores.azure_cosmos_db_no_sql import (
    AzureCosmosDBNoSqlVectorSearch,
)
from langchain_openai import AzureOpenAIEmbeddings
import os 
import random , string
import pandas as pd 

from langchain_text_splitters import RecursiveCharacterTextSplitter 


from typing import List, Optional

from bs4 import BeautifulSoup
from requests_html import HTMLSession

from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class Azure_Blob : 
    def __init__(self , account_url , container_name_blob):
        self.account_url = account_url
        self.container_name_blob = container_name_blob
        
    def setup_blob_connection(self) : 
        try : 
            default_credential = DefaultAzureCredential()
            blob_service_client = BlobServiceClient(self.account_url, credential=default_credential)
            print('Blob connecttion established')
        except Exception as e:
            print('Blob Connection error')
            print(e)
        container_list = list(blob_service_client.list_containers())
        container_list_names = [i.name for i in container_list]
        if self.container_name_blob not in container_list_names : 
            print('The container name does not exists')
            return None
        container_client = blob_service_client.get_container_client(self.container_name_blob)
        print('The container connection established.')
        return container_client
    
    def get_document_list(self , container_client) : 
        azure_file_list = container_client.list_blobs()
        azure_file_list_names = [i.name for i in azure_file_list]
        if len(azure_file_list_names) == 0 : 
            print('No files exist. Please upload files')
            return None
        print('The number of files present are ' + str(len(azure_file_list_names)))
        return azure_file_list_names
            
    def download_file_local(self, container_client , local_directory , file_name) : 
        try : 
            download_file_path = os.path.join(local_directory, file_name)
            print("\nDownloading blob to \n\t" + download_file_path)

            with open(file=download_file_path, mode="wb") as download_file:
                download_file.write(container_client.download_blob(file_name).readall())
            print('File download successful')
            
        except Exception as e: 
            print('File download unsuccesful. See error below')
            print(e)
            
    def upload_file_local(self , container_client , filename , directory_name) : 
        try : 
            directory_path = Path.joinpath(Path().resolve() , directory_name)
            with open(file=Path.joinpath(directory_path, filename), mode="rb") as data:
                blob_client = container_client.upload_blob(name=filename, data=data, overwrite=True)  
        except Exception as e : 
            print('File not uploaded') 
            print(e)        
            
            
class DocumentStore:
    def __init__(self, id: str, content: Optional[str] = None, metadata: Optional[dict] = None, type: Optional[str] = None):
        self.id = id
        self.content = content
        self.metadata = metadata
        self.type = type

    def get_content(self) -> str:
        return self.content
    
class VectorDataBase : 
    def __init__(self , indexing_policy : dict , vector_embedding_policy : dict , database_name : str, container_name : str):
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

    def setup_connection(self): 
        
        self.cosmos_client = CosmosClient(os.environ.get('HOST'), os.environ.get('KEY'))
        partition_key = PartitionKey(path="/id")
        cosmos_container_properties = {"partition_key": partition_key}
        cosmos_database_properties = {"id": self.database_name}

        self.openai_embeddings = AzureOpenAIEmbeddings(
            azure_deployment = os.environ.get('OPENAI_EMBEDDINGS_MODEL_DEPLOYMENT'),
            api_version = os.environ.get('OPENAI_API_VERSION'),
            azure_endpoint = os.environ.get('azure_endpoint'),
            openai_api_key = os.environ.get('OPENAI_API_KEY'),
        )
        try : 
            self.vector_search = AzureCosmosDBNoSqlVectorSearch(
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
        database = self.cosmos_client.get_database_client(self.database_name)
        container = database.get_container_client(self.container_name)
        query = f"SELECT DISTINCT c.metadata.source FROM c"

        # Run the query
        items = list(container.query_items(query=query, enable_cross_partition_query=True))
        return items
    
    def upload_file_vector_db(self , docs ) : 
        try : 
            document_id_list = self.vector_search.add_documents(documents = docs)
            print('Documents have been added.')
        except Exception as e:
            print('Document addition failed. The error is \n')
            print(e)
        return document_id_list
    
    def delete_container_db(self) : 
        self.cosmos_client.delete
    
class Directory(DocumentStore) : 
    def __init__(self , vector_search ):
        self.vector_search = vector_search
        self.document_list = []
        self.supported_types = {
            'pdf': PyPDFLoader,
            'docx': UnstructuredWordDocumentLoader,
            'txt': TextLoader,
            'xlsx' :  UnstructuredExcelLoader ,
            'ppt' : UnstructuredPowerPointLoader , 
            'html' : UnstructuredHTMLLoader, 
            'csv' : CSVLoader , 
            'json': JSONLoader , 
            'geojson': JSONLoader
        }
    
    # identifies which files needs to be updated in the 
    def identify_documents_not_present(self, knowledge_base_data ,azure_file_list_names) : 
        if len(knowledge_base_data) != 0 : 
            knowledge_base_source = [i['source'].split('\\')[-1] for i in knowledge_base_data]
            knowledge_base_update_files = [i for i in azure_file_list_names if i not in knowledge_base_source]
        else : 
            knowledge_base_update_files = azure_file_list_names.copy()
        if 'links_for_scrape.xlsx' not in knowledge_base_update_files : 
            knowledge_base_update_files.append('links_for_scrape.xlsx')
        return knowledge_base_update_files
    
    def fetch_html(self, url):
        session = HTMLSession()
        response = session.get(url)
        response.html.arender()
        return response.html.html if response.ok else None
    
    def parse_html(self, html):
        soup = BeautifulSoup(html, 'html.parser')
        return soup.get_text(strip=True)
    
    # downloads the file from azure blob to local directory specified. 
    def reading_file(self , directory_name , filename  ) : 
        directory_path = Path.joinpath(Path().resolve() , directory_name)
        file_path = Path.joinpath(directory_path , filename)
        filetype = filename.split('.')[-1]
        if filetype not in self.supported_types.keys() : 
            print('File type is not supported. Supported file types are ' + ' ,'.join(self.supported_types.keys()) + '. The file type of the docuement is ' + filetype)
        else : 
            if filetype == 'geojson' : 
                loader = JSONLoader(file_path = str(file_path) , jq_schema = '.features[]' , text_content = False)
                data = loader.load()
                for i in range(len(data)) : 
                    self.document_list.append(DocumentStore(id = ''.join(random.choice(string.ascii_uppercase + string.ascii_lowercase + string.digits) for _ in range(16)) , 
                                                        content = data[i].page_content ,
                                                        metadata = data[i].metadata  , 
                                                        type = filetype))
            elif filetype == 'json' : 
                loader = JSONLoader(file_path = str(file_path) , jq_schema = '.' , text_content = False)
                data = loader.load()
                self.document_list.append(DocumentStore(id = ''.join(random.choice(string.ascii_uppercase + string.ascii_lowercase + string.digits) for _ in range(16)) , 
                                content = data[0].page_content ,
                                metadata = data[0].metadata  , 
                                type = filetype))
            else : 
                loader = self.supported_types[filetype](str(file_path))
                data = loader.load()
                self.document_list.append(DocumentStore(id = ''.join(random.choice(string.ascii_uppercase + string.ascii_lowercase + string.digits) for _ in range(16)) , 
                                content = data[0].page_content ,
                                metadata = data[0].metadata  , 
                                type = filetype))
        
        print('File ' + filename + ' has been read.')
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
        docs = text_splitter.split_documents(data)
        document_id_list = self.vector_search.add_documents(documents=docs)
        print('File ' + filename + ' has been updated into knowledge base.')
        os.remove(file_path)
        
        return document_id_list        
    
    def reading_URLS_for_scrape(self , directory_name , filename  ) : 
        directory_path = Path.joinpath(Path().resolve() , directory_name)
        file_path = Path.joinpath(directory_path , filename)
        dataframe = pd.read_excel(file_path , engine = 'openpyxl')
        url_links = dataframe[(dataframe['Scraping Status'] == 0)&(dataframe['Type'] == 'url')].reset_index(drop = True)['Link']
        html_links = dataframe[(dataframe['Scraping Status'] == 0)&(dataframe['Type'] != 'url')].reset_index(drop = True)['Link']
        print('Number of URLs to be scraped is ' + str(len(url_links)) + '. Number of HTML links to be scraped is ' + str(len(html_links)))
        document_id_list = []
        for each in html_links : 
            html_content = self.fetch_html(each)
            if html_content:
                print("HTML content fetched successfully!")
            else:
                print("Failed to fetch HTML content.")
            text_content = self.parse_html(html_content)
            data = Document(
                page_content = text_content,
                metadata={"source": each}
            )
            print('Scraping for HTML link ' + str(each) + ' is done')
            self.document_list.append(DocumentStore(id = ''.join(random.choice(string.ascii_uppercase + string.ascii_lowercase + string.digits) for _ in range(16)) , 
                            content = data.page_content ,
                            metadata = data.metadata  , 
                            type = 'html'))
            text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
            docs = text_splitter.split_documents([data])
            document_id_list.append(self.vector_search.add_documents(documents=docs))
        for each in url_links : 
            html_content = self.fetch_html(each)
            if html_content:
                print("URL content fetched successfully!")
            else:
                print("Failed to fetch URL content.")
            text_content = self.parse_html(html_content)
            data = Document(
                page_content = text_content,
                metadata={"source": each}
            )
            print('Scraping for URL ' + str(each) + ' is done')
            self.document_list.append(DocumentStore(id = ''.join(random.choice(string.ascii_uppercase + string.ascii_lowercase + string.digits) for _ in range(16)) , 
                            content = data.page_content ,
                            metadata = data.metadata  , 
                            type = 'url'))
            text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
            docs = text_splitter.split_documents([data])
            document_id_list.append(self.vector_search.add_documents(documents=docs))
        #Path.unlink(filename , missing_ok=False)   
        print('File ' + filename + ' has been read.')
        print('File ' + filename + ' has been updated into knowledge base.')
        # os.remove(file_path)
        dataframe['Scraping Status'] = 1
        return document_id_list , dataframe 
    
    
