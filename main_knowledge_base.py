import json 
from knowledge_base.knowledge_base import *
from knowledge_base.azure_blob import Azure_Blob
from knowledge_base.vector_database import VectorDataBase

import argparse
import os 

from dotenv import load_dotenv

load_dotenv()

def main(main_config : dict) : 
    """
    The main function that orchestrates the process of updating the knowledge base by 
    downloading files from Azure Blob, processing them, and updating the vector database.
    
    Parameters:
    - main_config: A dictionary containing configuration details (e.g., Azure Blob settings, 
                    vector database settings, etc.).
    """
    required_keys = ["indexing_policy", "vector_embedding_policy", "temp_folder_download"]

    for key in required_keys:
        if key not in main_config:
            # Update the exception message for consistency with the test
            raise KeyError(f"Missing required key in main_config: {key}")
        
    # Initialize Azure Blob storage connection object with configuration from main_config        
    azure_blob_obj = Azure_Blob(account_url = os.environ.get('account_url') , container_name_blob = os.environ.get('container_name_blob'))
    
    # Setup the connection to Azure Blob Storage
    azure_container_client = azure_blob_obj.setup_blob_connection()
    
    # Get the list of files available in the Azure Blob container
    azure_files_list = azure_blob_obj.get_document_list(azure_container_client)
    
    # Initialize vector database object with configuration from main_config
    vector_database_obj = VectorDataBase(indexing_policy = main_config['indexing_policy'] , 
                                     vector_embedding_policy = main_config['vector_embedding_policy'] , 
                                     database_name = os.environ.get('database_name') , 
                                     container_name = os.environ.get('container_name'))
    
    # Setup the connection to the vector database
    vector_database_obj.setup_connection()
    
    # Retrieve the list of documents currently present in the vector database
    documents_present = vector_database_obj.get_document_source()
    
    # Initialize directory object to manage document processing
    directory_obj = Directory(vector_search = vector_database_obj.vector_search)
    
    # Identify which files from the Azure Blob container are not present in the knowledge base
    knowledge_base_update_files = directory_obj.identify_documents_not_present(knowledge_base_data = documents_present , azure_file_list_names = azure_files_list)
    
    # Process each file that needs to be added to the knowledge base
    for i in knowledge_base_update_files : 
        # Download the file from Azure Blob Storage to the local machine
        azure_blob_obj.download_file_local(azure_container_client , main_config['temp_folder_download'] , i)
        
        # Check if the file is 'links_for_scrape.xlsx' for special processing
        if i == 'links_for_scrape.xlsx' : 
            # If the file is 'links_for_scrape.xlsx', process it for URLs
            id_list , excel_file = directory_obj.reading_URLS_for_scrape(main_config['temp_folder_download'] , 'links_for_scrape.xlsx')
            
            # Define the local file path for saving the modified file
            directory_path = Path.joinpath(Path().resolve() , main_config['temp_folder_download'])
            file_path = Path.joinpath(directory_path , 'links_for_scrape.xlsx')
            
            # Save the modified Excel file locally
            excel_file.to_excel(file_path , index=False)
            
            # Upload the modified file back to Azure Blob Storage
            azure_blob_obj.upload_file_local(azure_container_client , 'links_for_scrape.xlsx' , main_config['temp_folder_download'])
            
            # Remove the local file after uploading it back to Azure Blob Storage
            os.remove(file_path)
        else: 
            # For other files, process them and update the knowledge base
            azure_blob_obj.download_file_local(azure_container_client , main_config['temp_folder_download']  , i)
            id_list = directory_obj.reading_file(main_config['temp_folder_download']  , i)
            
        print('Knowledge Base updated for ' + i)
        
    print('Any new data in knowledge base bas been updated')

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the main script with a configuration file.")
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to the configuration file (JSON format)."
    )
    args = parser.parse_args()
    # Load the configuration file
    try:
        main_config = json.load(open(args.config))
    except Exception as e:
        print(f"Error loading configuration file: {e}")
        exit(1)
    # main_config = json.load(open('main_config.json'))
    main(main_config)