import os
import json
import argparse
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError


class AzureBlobUploader:
    def __init__(self):
        load_dotenv()

        self.account_url = os.getenv("account_url")
        self.container_name = os.getenv("container_name_blob")

        if not self.account_url or not self.container_name:
            raise ValueError("Missing required environment variables: account_url or container_name_blob")

        self.blob_service_client = BlobServiceClient(
            account_url=self.account_url,
            credential=DefaultAzureCredential()
        )

    def create_container(self):
        try:
            container_client = self.blob_service_client.get_container_client(self.container_name)
            container_client.get_container_properties()
            print(f"Container '{self.container_name}' already exists. Uploading to existing container.")
            return True

        except ResourceNotFoundError:
            try:
                self.blob_service_client.create_container(self.container_name)
                print(f"Container '{self.container_name}' created successfully.")
                return True
            except ResourceExistsError:
                print(f"Container '{self.container_name}' already exists during creation attempt. Unexpected.")
                return True
            except Exception as e:
                print(f"Error creating container '{self.container_name}': {e}")
                return False

        except Exception as e:
            print(f"Error checking for container '{self.container_name}': {e}")
            return False

    def upload_files(self, folder_path):
        container_client = self.blob_service_client.get_container_client(self.container_name)

        for file_name in os.listdir(folder_path):
            file_path = os.path.join(folder_path, file_name)

            if os.path.isfile(file_path):
                blob_client = container_client.get_blob_client(file_name)

                try:
                    blob_client.get_blob_properties()
                    print(f"File '{file_name}' already exists in Blob. Skipping upload.")
                except ResourceNotFoundError:
                    try:
                        with open(file_path, "rb") as data:
                            blob_client.upload_blob(data, overwrite=True)
                        print(f"Uploaded: {file_name}")
                    except Exception as upload_error:
                        print(f"Error uploading {file_name}: {upload_error}")
                except Exception as e:
                    print(f"Error checking if {file_name} exists in blob: {e}")

    def run(self, config):
        local_folder_path = config.get("local_data_folder")

        if not local_folder_path:
            raise ValueError("Missing 'local_data_folder' key in config file")

        if not os.path.isdir(local_folder_path):
            raise ValueError(f"Provided folder does not exist: {local_folder_path}")

        if self.create_container():
            self.upload_files(local_folder_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Upload files to Azure Blob Storage using a config file.")
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to the configuration file (JSON format)."
    )

    args = parser.parse_args()

    try:
        with open(args.config, "r") as f:
            config = json.load(f)
    except Exception as e:
        print(f"Error loading configuration file: {e}")
        exit(1)

    uploader = AzureBlobUploader()
    uploader.run(config)
