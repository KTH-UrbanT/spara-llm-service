import os
from langchain_community.document_loaders.directory import DirectoryLoader
from langchain_community.document_loaders.json_loader import JSONLoader
from langchain.text_splitter import CharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain.vectorstores import FAISS
from typing import List
import os
from dotenv import load_dotenv
from pathlib import Path


# Class representing a Document with ID, content, and metadata
class Document:
    def __init__(self, doc_id: str, content: str, metadata: dict = None):
        self.id = doc_id
        self.content = content
        self.metadata = metadata or {}

    def get_content(self) -> str:
        return self.content


# Class to manage the Directory and documents within it
class Directory:
    def __init__(self, path: str):
        self.path = path
        self.documents = []

    def upload_document(self, document: Document):
        # Add document to internal list (in practice, you may save it in a directory)
        self.documents.append(document)

    def get_document_list(self) -> List[Document]:
        return self.documents

    def read_all_documents(self) -> List[Document]:
        # Load all documents in the directory using LangChain's DirectoryLoader
        loader = DirectoryLoader(self.path , show_progress=True)
        raw_documents = loader.load()

        for raw_doc in raw_documents:
            doc = Document(doc_id=raw_doc.metadata['source'], content=raw_doc.page_content)
            self.documents.append(doc)




# Class representing a document vector (embedding)
class DocumentVector:
    def __init__(self, doc_id: str, vector_data: List[float]):
        self.id = doc_id
        self.vector_data = vector_data


# Class for managing the vector database (FAISS, or any other vector store)
class VectorDatabase:
    def __init__(self, embedding_model):
        self.vector_store = None
        self.embedding_model = embedding_model
        self.documents = []  # Store the vectors

    def vector_store_initiation(self , path : str  = None) : 
        if path is not None : 
            self.vector_store = FAISS.load_local(path, self.embedding_model)
        else : 
            self.vector_store = None
    
    def is_present(self) -> bool:
        return self.vector_store is not None

    def create(self, documents: List[Document], chunksize: int):
        # Split the documents and create the vector database
        text_splitter = CharacterTextSplitter(chunk_size=chunksize, chunk_overlap=0)
        all_texts = []
        all_metadatas = []
        print('where are you bro ?')

        for doc in documents:
            chunks = text_splitter.split_text(doc.get_content())
            all_texts.extend(chunks)
            all_metadatas.extend([{"source": doc.id}] * len(chunks))

        # Create embeddings for the chunks
        text_embeddings = self.embedding_model.embed_documents(all_texts)
        text_embedding_pairs = zip(all_texts, text_embeddings)
        self.vector_store  = FAISS.from_embeddings(text_embedding_pairs, self.embedding_model)
        self.document_vectors = [DocumentVector(doc.id , embedding) for doc, embedding in zip(documents, text_embeddings)]
        self.documents = documents

    def check_document_exists(self, document: Document) -> bool:
        # Check if the document vector already exists in the vector store
        for doc_vec in self.documents:
            if doc_vec.id == document.id:
                return True
        return False

    def update_with_new_document(self, document: Document):
        # Add the new document to the vector database
        if not self.check_document_exists(document):
            embedding = self.embedding_model.embed_documents([document.get_content()])
            self.vector_store.add_texts([document.get_content()], [{"source": document.id}])
            self.documents.append(DocumentVector(document.id, embedding))


# Example usage of the classes
def main():
    # Set the path to the directory with documents
    document_directory_path = './documents'

    # Initialize the embedding model and vector database
    load_dotenv(Path('.env'))
    embedding_model = OpenAIEmbeddings(openai_api_key = os.getenv("OPENAI_API_KEY"))
    vector_db = VectorDatabase(embedding_model)

    # Create a Directory object and read all documents
    doc_directory = Directory(path=document_directory_path)
    doc_directory.read_all_documents()
    documents = doc_directory.get_document_list()

    # Check if the vector database is present
    if not vector_db.is_present():
        # If no vector database, create it based on the documents in the directory
        vector_db.create(documents, chunksize=1000)

    # Process new documents and update the vector database if necessary
    for doc in documents:
        if not vector_db.check_document_exists(doc):
            vector_db.update_with_new_document(doc)

    # Done, now the vector database contains all the document vectors.

if __name__ == "__main__":
    main()
