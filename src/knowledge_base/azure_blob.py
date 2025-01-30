from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Azure_Blob : 
    """
    A class for managing Azure Blob Storage operations such as setting up connections,
    listing files, downloading files, and uploading files.
    """
    def __init__(self , account_url , container_name_blob):
        """
        Initializes the Azure_Blob class with the storage account URL and container name.
        
        Parameters:
        - account_url (str): The URL of the Azure Storage Account.
        - container_name_blob (str): The name of the blob container to interact with.
        """
        self.account_url = account_url
        self.container_name_blob = container_name_blob
        
    def setup_blob_connection(self) : 
        """
        Establishes a connection to the Azure Blob Storage container using `DefaultAzureCredential`.
        
        Returns:
        - container_client (ContainerClient): The client object for the specified container,
          or None if the container does not exist or connection fails.
        """
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
        """
        Lists all files in the Azure Blob Storage container.
        
        Parameters:
        - container_client (ContainerClient): The client object for the specified container.

        Returns:
        - azure_file_list_names (list): A list of file names in the container, or None if the container is empty.
        """
        azure_file_list = container_client.list_blobs()
        azure_file_list_names = [i.name for i in azure_file_list]
        if len(azure_file_list_names) == 0 : 
            print('No files exist. Please upload files')
            return None
        print('The number of files present are ' + str(len(azure_file_list_names)))
        return azure_file_list_names
            
    def download_file_local(self, container_client , local_directory , file_name) : 
        """
        Downloads a file from Azure Blob Storage to a local directory.
        
        Parameters:
        - container_client (ContainerClient): The client object for the specified container.
        - local_directory (str): The local directory to save the downloaded file.
        - file_name (str): The name of the file to download.
        """
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
        """
        Uploads a file from a local directory to Azure Blob Storage.
        
        Parameters:
        - container_client (ContainerClient): The client object for the specified container.
        - filename (str): The name of the file to upload.
        - directory_name (str): The local directory where the file is located.
        """
        try : 
            directory_path = Path.joinpath(Path().resolve() , directory_name)
            with open(file=Path.joinpath(directory_path, filename), mode="rb") as data:
                blob_client = container_client.upload_blob(name=filename, data=data, overwrite=True)  
        except Exception as e : 
            print('File not uploaded') 
            print(e)       