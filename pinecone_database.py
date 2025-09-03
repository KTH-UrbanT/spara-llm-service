# ingest_pipeline.py
import os
import sys
import time
import math
import hashlib
import random
import string
from pathlib import Path
from typing import List, Optional, Dict, Any, Iterable

# --- Environment ---
from dotenv import load_dotenv
load_dotenv()

# --- LangChain loaders & core types ---
from langchain_community.document_loaders.pdf import PyPDFLoader
from langchain_community.document_loaders.word_document import UnstructuredWordDocumentLoader
from langchain_community.document_loaders.excel import UnstructuredExcelLoader
from langchain_community.document_loaders.text import TextLoader
from langchain_community.document_loaders.json_loader import JSONLoader
from langchain_community.document_loaders.powerpoint import UnstructuredPowerPointLoader
from langchain_community.document_loaders.html import UnstructuredHTMLLoader
from langchain_community.document_loaders.csv_loader import CSVLoader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

# --- Azure OpenAI embeddings ---
from langchain_openai import AzureOpenAIEmbeddings

# --- Pinecone v3 client ---
from pinecone import Pinecone, ServerlessSpec

# --- Optional scraping deps ---
from bs4 import BeautifulSoup
from requests_html import HTMLSession

import pandas as pd


# ===========================
# Config (env-first)
# ===========================
INPUT_DIR = os.getenv("INGEST_INPUT_DIR", "./temp_folder_download")
DELETE_AFTER_INGEST = os.getenv("DELETE_AFTER_INGEST", "false").lower() == "true"

# Embedding / Azure OpenAI
AZURE_DEPLOYMENT = os.environ.get("OPENAI_EMBEDDINGS_MODEL_DEPLOYMENT")
AZURE_API_VERSION = os.environ.get("OPENAI_API_VERSION")
AZURE_ENDPOINT = os.environ.get("AZURE_ENDPOINT")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# Pinecone
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY") or os.environ.get("pinecone_api")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "doc-embeddings")
PINECONE_NAMESPACE = os.getenv("PINECONE_NAMESPACE", "docs")
PINECONE_CLOUD = os.getenv("PINECONE_CLOUD", "aws")          # aws | gcp
PINECONE_REGION = os.getenv("PINECONE_REGION", "us-east-1")  # e.g. us-east-1

# Chunks & batching
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "150"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "64"))
UPSERT_BATCH_SIZE = int(os.getenv("UPSERT_BATCH_SIZE", str(BATCH_SIZE)))

# ===========================
# Utility
# ===========================
def rand_id(n=16) -> str:
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(n))

def sha_id(*parts: str) -> str:
    h = hashlib.md5("::".join(parts).encode("utf-8")).hexdigest()
    return h

def batched(it: Iterable[Any], n: int):
    batch = []
    for x in it:
        batch.append(x)
        if len(batch) >= n:
            yield batch
            batch = []
    if batch:
        yield batch

# ===========================
# Vector store adapter (Pinecone + Azure embeddings)
# ===========================
class PineconeVectorSearch:
    """
    Minimal adapter for Directory.vector_search.add_documents(documents=docs)
    - Ensures Pinecone index exists with the correct dimension
    - Embeds chunks with AzureOpenAIEmbeddings
    - Upserts to Pinecone (values + metadata)
    """
    def __init__(
        self,
        index_name: str,
        namespace: str,
        embeddings: AzureOpenAIEmbeddings,
        api_key: str,
        cloud: str = PINECONE_CLOUD,
        region: str = PINECONE_REGION,
        metric: str = "cosine",
    ):
        if not api_key:
            raise RuntimeError("Missing Pinecone API key. Set PINECONE_API_KEY or pinecone_api.")

        self.pc = Pinecone(api_key=api_key)
        self.index_name = index_name
        self.namespace = namespace
        self.metric = metric
        self.cloud = cloud
        self.region = region
        self.embeddings = embeddings

        # Detect embedding dimension
        probe = self.embeddings.embed_query("dimension probe")
        self.dimension = len(probe)

        # Ensure index exists with correct dimension
        self._ensure_index()

        # Use index
        self.index = self.pc.Index(self.index_name)

    def _ensure_index(self):
        # Pinecone client v3/v4 supports list_indexes(); we check by name
        existing = {ix["name"] for ix in self.pc.list_indexes().get("indexes", [])}
        if self.index_name not in existing:
            print(f"[INFO] Creating Pinecone index '{self.index_name}' ({self.cloud}/{self.region}) dim={self.dimension}")
            self.pc.create_index(
                name=self.index_name,
                dimension=self.dimension,
                metric=self.metric,
                spec=ServerlessSpec(cloud=self.cloud, region=self.region),
            )
            # Wait for ready
            for _ in range(60):
                desc = self.pc.describe_index(self.index_name)
                if desc.get("status", {}).get("ready"):
                    break
                time.sleep(1)
        else:
            # Optionally you can verify the dimension matches; here we assume it does or you manage it out-of-band.
            pass

    def add_documents(self, documents: List[Document]) -> List[str]:
        if not documents:
            return []

        texts = [d.page_content or "" for d in documents]
        metas = [d.metadata or {} for d in documents]

        # Generate stable IDs using source + idx + length (falls back to hash of text)
        ids: List[str] = []
        for i, (t, m) in enumerate(zip(texts, metas)):
            src = m.get("source", "unknown")
            base = f"{src}::{i}::{len(t)}"
            ids.append(sha_id(base))

        # Embed in batches
        vectors_all: List[List[float]] = []
        for batch in batched(texts, BATCH_SIZE):
            for attempt in range(5):
                try:
                    vecs = self.embeddings.embed_documents(batch)
                    vectors_all.extend(vecs)
                    break
                except Exception as e:
                    wait = 2 ** attempt
                    print(f"[WARN] Embedding batch failed: {e} -> retrying in {wait}s")
                    time.sleep(wait)
            else:
                raise RuntimeError("Failed to embed a batch after multiple retries")

        # Upsert in batches
        upserted = 0
        for i, up_batch_idx in enumerate(range(0, len(ids), UPSERT_BATCH_SIZE)):
            j = up_batch_idx + UPSERT_BATCH_SIZE
            batch_ids = ids[up_batch_idx:j]
            batch_vecs = vectors_all[up_batch_idx:j]
            batch_meta = metas[up_batch_idx:j]

            payload = [
                {"id": batch_ids[k], "values": batch_vecs[k], "metadata": batch_meta[k]}
                for k in range(len(batch_ids))
            ]

            for attempt in range(5):
                try:
                    self.index.upsert(vectors=payload, namespace=self.namespace)
                    upserted += len(payload)
                    print(f"[INFO] Upserted {upserted}/{len(ids)} vectors")
                    break
                except Exception as e:
                    wait = 2 ** attempt
                    print(f"[WARN] Upsert failed: {e} -> retrying in {wait}s")
                    time.sleep(wait)
            else:
                raise RuntimeError("Failed to upsert a batch after multiple retries")

        return ids


# ===========================
# Document storage wrappers
# ===========================
class DocumentStore:
    """
    Storage container for documents you’ve read (optional, for bookkeeping).
    """
    def __init__(self, id: str, content: Optional[str] = None, metadata: Optional[dict] = None, type: Optional[str] = None):
        self.id = id
        self.content = content
        self.metadata = metadata
        self.type = type

    def get_content(self) -> str:
        return self.content


class Directory(DocumentStore):
    """
    Manages directories containing documents:
    - Reads local files via loader map
    - Splits text into chunks
    - Sends to vector store (Pinecone) via .add_documents()
    - Can scrape URLs from an Excel file and ingest them too
    """
    def __init__(self, vector_search: PineconeVectorSearch):
        self.vector_search = vector_search
        self.document_list: List[DocumentStore] = []
        self.supported_types = {
            'pdf': PyPDFLoader,
            'docx': UnstructuredWordDocumentLoader,
            'txt': TextLoader,
            'xlsx': UnstructuredExcelLoader,
            'pptx': UnstructuredPowerPointLoader,
            'html': UnstructuredHTMLLoader,
            'csv': CSVLoader,
            'json': JSONLoader,
            'geojson': JSONLoader
        }

    def identify_documents_not_present(self, knowledge_base_data, azure_file_list_names):
        if len(knowledge_base_data) != 0:
            knowledge_base_source = [i['source'].split('\\')[-1] for i in knowledge_base_data]
            knowledge_base_update_files = [i for i in azure_file_list_names if i not in knowledge_base_source]
        else:
            knowledge_base_update_files = azure_file_list_names.copy()
        if 'links_for_scrape.xlsx' not in knowledge_base_update_files:
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

    def _split_and_upsert(self, loaded_docs: List[Document]) -> List[str]:
        splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
        docs = splitter.split_documents(loaded_docs)
        return self.vector_search.add_documents(documents=docs)

    def reading_file(self, directory_name: str, filename: str, delete_after: bool = DELETE_AFTER_INGEST) -> List[str]:
        directory_path = Path.joinpath(Path().resolve(), directory_name)
        file_path = Path.joinpath(directory_path, filename)
        filetype = filename.split('.')[-1].lower()

        if filetype not in self.supported_types:
            print('File type is not supported. Supported: ' + ', '.join(self.supported_types.keys()) + f'. Got: {filetype}')
            return []

        try:
            if filetype == 'geojson':
                loader = JSONLoader(file_path=str(file_path), jq_schema='.features[]', text_content=False)
                data = loader.load()
                for i in range(len(data)):
                    self.document_list.append(
                        DocumentStore(id=rand_id(), content=data[i].page_content, metadata=data[i].metadata, type=filetype)
                    )
                ids = self._split_and_upsert(data)

            elif filetype == 'json':
                loader = JSONLoader(file_path=str(file_path), jq_schema='.', text_content=False)
                data = loader.load()
                self.document_list.append(
                    DocumentStore(id=rand_id(), content=data[0].page_content, metadata=data[0].metadata, type=filetype)
                )
                ids = self._split_and_upsert(data)

            else:
                LoaderCls = self.supported_types[filetype]
                loader = LoaderCls(str(file_path))
                data = loader.load()
                # many loaders return a list; we track only the first for DocumentStore
                self.document_list.append(
                    DocumentStore(id=rand_id(), content=data[0].page_content, metadata=data[0].metadata, type=filetype)
                )
                ids = self._split_and_upsert(data)

            print(f'File {filename} has been read and upserted.')
            if delete_after:
                try:
                    os.remove(file_path)
                    print(f'File {filename} deleted from local directory.')
                except Exception as e:
                    print(f'[WARN] Could not delete {filename}: {e}')
            return ids

        except Exception as e:
            print(f'[ERROR] reading_file failed for {filename}: {e}')
            return []

    def reading_URLS_for_scrape(self, directory_name: str, filename: str):
        directory_path = Path.joinpath(Path().resolve(), directory_name)
        file_path = Path.joinpath(directory_path, filename)

        dataframe = pd.read_excel(file_path, engine='openpyxl')
        url_links = dataframe[(dataframe['Scraping Status'] == 0) & (dataframe['Type'] == 'url')].reset_index(drop=True)['Link']
        html_links = dataframe[(dataframe['Scraping Status'] == 0) & (dataframe['Type'] != 'url')].reset_index(drop=True)['Link']

        print('Number of URLs to be scraped:', len(url_links), '| Number of HTML links:', len(html_links))

        document_id_list = []

        # HTML links (pre-rendered/static HTML)
        for each in html_links:
            html_content = self.fetch_html(each)
            if html_content:
                text_content = self.parse_html(html_content)
                data = Document(page_content=text_content, metadata={"source": each})
                print('Scraping for HTML link', each, 'is done')
                self.document_list.append(DocumentStore(id=rand_id(), content=data.page_content, metadata=data.metadata, type='html'))
                ids = self._split_and_upsert([data])
                document_id_list.extend(ids)
            else:
                print("Failed to fetch HTML content for:", each)

        # URL links (treated the same way here)
        for each in url_links:
            html_content = self.fetch_html(each)
            if html_content:
                text_content = self.parse_html(html_content)
                data = Document(page_content=text_content, metadata={"source": each})
                print('Scraping for URL', each, 'is done')
                self.document_list.append(DocumentStore(id=rand_id(), content=data.page_content, metadata=data.metadata, type='url'))
                ids = self._split_and_upsert([data])
                document_id_list.extend(ids)
            else:
                print("Failed to fetch URL content for:", each)

        print('File', filename, 'has been processed and ingested.')
        dataframe['Scraping Status'] = 1
        return document_id_list, dataframe

    # Convenience: read & ingest every supported file in a folder (recursively)
    def read_all(self, directory_name: str) -> Dict[str, List[str]]:
        directory_path = Path.joinpath(Path().resolve(), directory_name)
        if not Path.exists(directory_path):
            raise FileNotFoundError(f"Directory not found: {directory_name}")

        results: Dict[str, List[str]] = {}
        for p in Path(directory_path).rglob("*"):
            if not p.is_file():
                continue
            fname = p.name
            ext = fname.split(".")[-1].lower()
            if ext in self.supported_types:
                ids = self.reading_file(directory_name, str(Path(p).relative_to(directory_path)))
                results[fname] = ids
        return results


# ===========================
# Bootstrap & run
# ===========================
def build_embeddings() -> AzureOpenAIEmbeddings:
    if not (AZURE_DEPLOYMENT and AZURE_API_VERSION and AZURE_ENDPOINT and OPENAI_API_KEY):
        raise RuntimeError(
            "Missing Azure OpenAI env. Set OPENAI_EMBEDDINGS_MODEL_DEPLOYMENT, OPENAI_API_VERSION, "
            "AZURE_ENDPOINT, OPENAI_API_KEY."
        )
    return AzureOpenAIEmbeddings(
        azure_deployment=AZURE_DEPLOYMENT,
        api_version=AZURE_API_VERSION,
        azure_endpoint=AZURE_ENDPOINT,
        openai_api_key=OPENAI_API_KEY,
    )

def main():
    # Build embeddings
    embeddings = build_embeddings()

    # Build Pinecone vector search adapter
    vs = PineconeVectorSearch(
        index_name=PINECONE_INDEX_NAME,
        namespace=PINECONE_NAMESPACE,
        embeddings=embeddings,
        api_key=PINECONE_API_KEY,
        cloud=PINECONE_CLOUD,
        region=PINECONE_REGION,
        metric="cosine",
    )

    # Directory manager
    directory = Directory(vector_search=vs)

    # Ingest everything under INPUT_DIR
    print(f"[INFO] Ingesting from: {INPUT_DIR}")
    results = directory.read_all(INPUT_DIR)
    total_chunks = sum(len(v) for v in results.values())
    print(f"[DONE] Ingested {len(results)} files, {total_chunks} chunks total into index '{PINECONE_INDEX_NAME}' / ns '{PINECONE_NAMESPACE}'.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[FATAL]", e)
        sys.exit(1)
