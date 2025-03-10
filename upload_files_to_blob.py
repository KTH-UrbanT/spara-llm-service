import os
import json
import argparse
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError

# Load environment variables from .env file
load_dotenv()

# Get values from environment variables
ACCOUNT_URL = os.getenv("account_url")
CONTAINER_NAME = os.getenv("container_name_blob")

if not ACCOUNT_URL or not CONTAINER_NAME:
    raise ValueError("Missing required environment variables: account_url or container_name_blob")

# Use DefaultAzureCredential for authentication
default_credential = DefaultAzureCredential()
blob_service_client = BlobServiceClient(account_url=ACCOUNT_URL, credential=default_credential)

def create_container(blob_service_client, container_name):
    """Checks if a container exists, creates it if it doesn't, or uploads to it if it does."""

    try:
        # Attempt to get the container client. This will raise ResourceNotFoundError if it doesn't exist.
        container_client = blob_service_client.get_container_client(container_name)
        container_client.get_container_properties() #verify that the container exists.

        print(f"Container '{container_name}' already exists. Uploading to existing container.")
        return True # container exists

    except ResourceNotFoundError:
        try:
            # Container doesn't exist, create it.
            blob_service_client.create_container(container_name)
            print(f"Container '{container_name}' created successfully.")
            return True #container created

        except ResourceExistsError: #This should not happen if the first except works correctly.
            print(f"Container '{container_name}' already exists during creation attempt. This is unexpected.")
            return True #container exists

        except Exception as e:
            print(f"Error creating container '{container_name}': {e}")
            return False #container not created

    except Exception as e:
        print(f"Error checking for container '{container_name}': {e}")
        return False #container check failed.

def upload_files(blob_service_client, container_name, folder_path):
    """Uploads files only if they exist locally and not in the Blob container."""
    container_client = blob_service_client.get_container_client(container_name)

    for file_name in os.listdir(folder_path):
        file_path = os.path.join(folder_path, file_name)

        if os.path.isfile(file_path):  # Ensure it's a file, not a subdirectory
            blob_client = container_client.get_blob_client(file_name)

            try:
                # Attempt to get blob properties. If it exists, it'll succeed.
                blob_client.get_blob_properties()
                print(f"File '{file_name}' already exists in Blob. Skipping upload.")

            except ResourceNotFoundError:
                # File doesn't exist in Blob, upload it.
                try:
                    with open(file_path, "rb") as data:
                        blob_client.upload_blob(data, overwrite=True)
                    print(f"Uploaded: {file_name}")
                except Exception as upload_error:
                    print(f"Error uploading {file_name}: {upload_error}")
            except Exception as e:
                print(f"Error checking if {file_name} exists in blob: {e}")

def main(config):
    """Reads 'local_data_folder' from config and uploads files."""
    local_folder_path = config.get("local_data_folder")

    if not local_folder_path:
        raise ValueError("Missing 'local_data_folder' key in config file")

    if not os.path.isdir(local_folder_path):
        raise ValueError(f"Provided folder does not exist: {local_folder_path}")

    # Create container if it doesn't exist
    create_container(blob_service_client, CONTAINER_NAME)

    # Upload files from the local folder
    upload_files(blob_service_client, CONTAINER_NAME, local_folder_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Upload files to Azure Blob Storage using a config file.")
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to the configuration file (JSON format)."
    )

    args = parser.parse_args()

    # Load the configuration file
    try:
        with open(args.config, "r") as f:
            main_config = json.load(f)
    except Exception as e:
        print(f"Error loading configuration file: {e}")
        exit(1)

    # Run main with the parsed config
    main(main_config)
