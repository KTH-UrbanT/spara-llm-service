import pytest
from unittest.mock import patch, MagicMock
import os
from pathlib import Path
from main_knowledge_base import main  # Assuming main function is in main_knowledge_base.py

@pytest.fixture
def mock_main_config():
    # Define the mock configuration dictionary
    return {
        "indexing_policy": "default_policy",
        "vector_embedding_policy": "embedding_policy",
        "temp_folder_download": "temp_folder_download"
    }

@patch("main_knowledge_base.Azure_Blob")
@patch("main_knowledge_base.VectorDataBase")
@patch("main_knowledge_base.Directory")
def test_main_function(mock_directory, mock_vector_database, mock_azure_blob, mock_main_config):
    """
    Test the main function using mocks for external dependencies.
    """
    # Mock Azure_Blob behavior
    mock_blob_instance = mock_azure_blob.return_value
    mock_blob_instance.setup_blob_connection.return_value = "mock_container_client"
    mock_blob_instance.get_document_list.return_value = ["file1.txt", "file2.txt", "links_for_scrape.xlsx"]
    mock_blob_instance.download_file_local = MagicMock()
    mock_blob_instance.upload_file_local = MagicMock()

    # Mock VectorDataBase behavior
    mock_vector_db_instance = mock_vector_database.return_value
    mock_vector_db_instance.get_document_source.return_value = [{"id": "file1"}]
    mock_vector_db_instance.setup_connection = MagicMock()

    # Mock Directory behavior
    mock_directory_instance = mock_directory.return_value
    mock_directory_instance.identify_documents_not_present.return_value = ["file2.txt"]
    mock_directory_instance.reading_file.return_value = ["id1", "id2"]
    mock_directory_instance.reading_URLS_for_scrape.return_value = (
        ["id1", "id2"],
        MagicMock(),
    )

    # Mock environment variables
    with patch.dict(os.environ, {"account_url": "mock_account_url", "container_name_blob": "mock_blob_container", "database_name": "mock_db", "container_name": "mock_container"}):
        # Call the main function
        main(mock_main_config)

        # Assertions for Azure_Blob interactions
        mock_blob_instance.setup_blob_connection.assert_called_once()
        mock_blob_instance.get_document_list.assert_called_once_with("mock_container_client")
        mock_blob_instance.download_file_local.assert_called()

        # Skip upload_file_local for links_for_scrape.xlsx
        mock_blob_instance.upload_file_local.assert_not_called()


def test_main_with_invalid_config():
    """
    Test the main function with invalid configuration file handling.
    """
    with pytest.raises(KeyError, match="Missing required key in main_config:"):
        main({})
