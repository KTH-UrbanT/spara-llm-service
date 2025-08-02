import json
import argparse
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

from src.knowledge_base.knowledge_base import Directory
from src.knowledge_base.azure_blob import Azure_Blob
from src.knowledge_base.vector_database import VectorDataBase

load_dotenv()

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

class KnowledgeBaseUpdater:
    def __init__(self, config: dict):
        self.config = config
        self._validate_config()

        self.temp_folder = config["temp_folder_download"]
        self.local_data_folder = config.get("local_data_folder")

        self.azure_blob = Azure_Blob(
            account_url=os.environ.get("account_url"),
            container_name_blob=os.environ.get("container_name_blob")
        )

        self.vector_db = VectorDataBase(
            indexing_policy=config["indexing_policy"],
            vector_embedding_policy=config["vector_embedding_policy"],
            database_name=os.environ.get("database_name"),
            container_name=os.environ.get("container_name")
        )

        try:
            self.vector_db.setup_connection()
            logger.info("Vector database connection established successfully.")
        except Exception as e:
            logger.error(f"Failed to set up Vector DB connection: {e}")
            raise RuntimeError(f"Failed to set up Vector DB connection: {e}")

        if not hasattr(self.vector_db, "vector_search"):
            logger.error("VectorDataBase instance has no attribute 'vector_search' after setup.")
            raise AttributeError("VectorDataBase instance has no attribute 'vector_search' after setup.")

        self.directory = Directory(vector_search=self.vector_db.vector_search)

    def _validate_config(self):
        required = ["indexing_policy", "vector_embedding_policy", "temp_folder_download"]
        for key in required:
            if key not in self.config:
                logger.error(f"Missing required key in main_config: {key}")
                raise KeyError(f"Missing required key in main_config: {key}")

    def update_knowledge_base(self):
        blob_client = self.azure_blob.setup_blob_connection()
        azure_files_list = self.azure_blob.get_document_list(blob_client)

        if self.local_data_folder:
            if not os.path.isdir(self.local_data_folder):
                logger.error(f"'local_data_folder' does not exist: {self.local_data_folder}")
                raise ValueError(f"'local_data_folder' does not exist: {self.local_data_folder}")

            logger.info(f"Uploading files from {self.local_data_folder} to Azure Blob...")

            files_original = [
                f for f in os.listdir(self.local_data_folder)
                if os.path.isfile(os.path.join(self.local_data_folder, f))
            ]
            files_to_upload = [f for f in files_original if f not in azure_files_list]

            if not files_to_upload:
                logger.info(f"No new files found in local folder '{self.local_data_folder}'. Nothing to upload.")
            else:
                logger.info(f"Uploading {len(files_to_upload)} files to Azure Blob Storage...")

            for file_name in files_to_upload:
                try:
                    self.azure_blob.upload_file_local(blob_client, file_name, self.local_data_folder)
                    logger.info(f"Uploaded: {file_name}")
                except Exception as e:
                    logger.error(f"Failed to upload {file_name}: {e}")

            # Update azure_files_list with newly uploaded files
            azure_files_list.extend(files_to_upload)

        try:
            documents_present = self.vector_db.get_document_source()
        except Exception as e:
            logger.error(f"Error fetching document sources from vector DB: {e}")
            documents_present = []

        files_to_update = self.directory.identify_documents_not_present(
            knowledge_base_data=documents_present,
            azure_file_list_names=azure_files_list
        )

        if not files_to_update:
            logger.info("No files need updating in the knowledge base.")
        else:
            logger.info(f"Files to update in knowledge base: {files_to_update}")

        for file_name in files_to_update:
            try:
                self.azure_blob.download_file_local(blob_client, self.temp_folder, file_name)
                logger.info(f"Downloaded {file_name} to {self.temp_folder}")
            except Exception as e:
                logger.error(f"Failed to download {file_name}: {e}")
                continue

            try:
                if file_name == 'links_for_scrape.xlsx':
                    id_list, excel_file = self.directory.reading_URLS_for_scrape(self.temp_folder, file_name)

                    file_path = Path(self.temp_folder) / file_name
                    excel_file.to_excel(file_path, index=False)

                    self.azure_blob.upload_file_local(blob_client, file_name, self.temp_folder)
                    logger.info(f"Re-uploaded processed file {file_name}")
                    os.remove(file_path)
                    logger.info(f"Removed temporary file {file_path}")
                else:
                    # No double download, just read once after download
                    id_list = self.directory.reading_file(self.temp_folder, file_name)

                logger.info(f"Knowledge Base updated for {file_name}")
            except Exception as e:
                logger.error(f"Error processing file {file_name}: {e}")

        logger.info("Any new data in knowledge base has been updated.")


def load_config(path: str) -> dict:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading configuration file: {e}")
        raise RuntimeError(f"Error loading configuration file: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run knowledge base updater.")
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to the configuration JSON file."
    )

    args = parser.parse_args()
    config = load_config(args.config)

    updater = KnowledgeBaseUpdater(config)
    updater.update_knowledge_base()
