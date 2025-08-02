from langchain_community.document_loaders.pdf import PyPDFLoader
from langchain_community.document_loaders.word_document import UnstructuredWordDocumentLoader
from langchain_community.document_loaders.excel import UnstructuredExcelLoader
from langchain_community.document_loaders.text import TextLoader
from langchain_community.document_loaders.json_loader import JSONLoader
from langchain_community.document_loaders.powerpoint import UnstructuredPowerPointLoader
from langchain_community.document_loaders.html import UnstructuredHTMLLoader
from langchain_community.document_loaders.csv_loader import CSVLoader
from langchain_core.documents import Document

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


            
            
class DocumentStore:
    """
    A class to store document information including its content, metadata, and type.
    This class serves as a storage container for documents that can be added to the knowledge base.
    """
    def __init__(self, id: str, content: Optional[str] = None, metadata: Optional[dict] = None, type: Optional[str] = None):
        """
        Initializes a DocumentStore object.

        Parameters:
        - id (str): Unique identifier for the document.
        - content (str): The textual content of the document.
        - metadata (dict): Metadata associated with the document (e.g., source, type).
        - type (str): Type of the document (e.g., PDF, JSON).
        """
        self.id = id
        self.content = content
        self.metadata = metadata
        self.type = type
        ## update times for documents

    def get_content(self) -> str:
        """
        Returns the content of the document.

        Returns:
        - str: The content of the document.
        """
        return self.content
    

    
class Directory(DocumentStore) : 
    """
    A class for managing directories containing documents and performing document loading, parsing,
    and processing operations. This class handles both local file reading and URL-based content scraping.
    """
    def __init__(self , vector_search ):
        """
        Initializes the Directory class, which manages document loading and processing.

        Parameters:
        - vector_search: Object responsible for adding processed documents into a vector database or search index.
        """
        self.vector_search = vector_search
        self.document_list = []
        self.supported_types = {
            'pdf': PyPDFLoader,
            'docx': UnstructuredWordDocumentLoader,
            'txt': TextLoader,
            'xlsx' :  UnstructuredExcelLoader ,
            'pptx' : UnstructuredPowerPointLoader , 
            'html' : UnstructuredHTMLLoader, 
            'csv' : CSVLoader , 
            'json': JSONLoader , 
            'geojson': JSONLoader
        }
    
    # identifies which files needs to be updated in the 
    def identify_documents_not_present(self, knowledge_base_data ,azure_file_list_names) : 
        """
        Identifies which files are not yet present in the knowledge base and need to be updated.

        Parameters:
        - knowledge_base_data (list): List of documents currently stored in the knowledge base.
        - azure_file_list_names (list): List of file names available in the Azure Blob Storage.

        Returns:
        - list: List of file names that need to be updated in the knowledge base.
        """
        if len(knowledge_base_data) != 0 : 
            knowledge_base_source = [i['source'].split('\\')[-1] for i in knowledge_base_data]
            knowledge_base_update_files = [i for i in azure_file_list_names if i not in knowledge_base_source]
        else : 
            knowledge_base_update_files = azure_file_list_names.copy()
        if 'links_for_scrape.xlsx' not in knowledge_base_update_files and 'links_for_scrape.xlsx' in  azure_file_list_names: 
            knowledge_base_update_files.append('links_for_scrape.xlsx')
        return knowledge_base_update_files
    
    def fetch_html(self, url):
        """
        Fetches the HTML content of a given URL using the requests_html library.

        Parameters:
        - url (str): The URL to scrape.

        Returns:
        - str: The HTML content of the page if successfully fetched, else None.
        """
        session = HTMLSession()
        response = session.get(url)
        response.html.arender()
        return response.html.html if response.ok else None
    
    def parse_html(self, html):
        """
        Extracts and parses the text content from HTML.

        Parameters:
        - html (str): The raw HTML content.

        Returns:
        - str: Cleaned text extracted from the HTML.
        """
        soup = BeautifulSoup(html, 'html.parser')
        return soup.get_text(strip=True)
    
    # downloads the file from azure blob to local directory specified. 
    def reading_file(self , directory_name , filename  ) : 
        """
        Reads a document file from the local directory and processes it based on its file type.
        The file is loaded and its content added to the knowledge base after processing.

        Parameters:
        - directory_name (str): The name of the directory where the file is located.
        - filename (str): The name of the file to be processed.

        Returns:
        - list: A list of document IDs added to the knowledge base.
        """
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
        """
        Reads a file that contains URLs to be scraped. It fetches the content from each URL,
        processes it, and adds it to the knowledge base.

        Parameters:
        - directory_name (str): The name of the directory where the file is located.
        - filename (str): The name of the file to be processed.

        Returns:
        - tuple: A tuple containing the document IDs and the updated dataframe.
        """
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
    
    
