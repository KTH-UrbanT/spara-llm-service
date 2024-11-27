import json 
from knowledge_base.main_KB import *


def main(main_config : dict) : 
    azure_blob_obj = Azure_Blob(account_url = main_config['account_url'] , container_name_blob = main_config['container_name_blob'])
    azure_container_client = azure_blob_obj.setup_blob_connection()
    azure_files_list = azure_blob_obj.get_document_list(azure_container_client)
    vector_database_obj = VectorDataBase(indexing_policy = main_config['indexing_policy'] , 
                                     vector_embedding_policy = main_config['vector_embedding_policy'] , 
                                     database_name = main_config['database_name'] , 
                                     container_name = main_config['container_name'])
    vector_database_obj.setup_connection()
    documents_present = vector_database_obj.get_document_source()
    directory_obj = Directory(vector_search = vector_database_obj.vector_search)
    knowledge_base_update_files = directory_obj.identify_documents_not_present(knowledge_base_data = documents_present , azure_file_list_names = azure_files_list)
    for i in knowledge_base_update_files : 
        azure_blob_obj.download_file_local(azure_container_client , main_config['temp_folder_download'] , i)
        if i == 'links_for_scrape.xlsx' : 
            id_list , excel_file = directory_obj.reading_URLS_for_scrape(main_config['temp_folder_download'] , 'links_for_scrape.xlsx')
            
            directory_path = Path.joinpath(Path().resolve() , main_config['temp_folder_download'])
            file_path = Path.joinpath(directory_path , 'links_for_scrape.xlsx')
            excel_file.to_excel(file_path , index=False)
            azure_blob_obj.upload_file_local(azure_container_client , 'links_for_scrape.xlsx' , main_config['temp_folder_download'])
            os.remove(file_path)
        else: 
            azure_blob_obj.download_file_local(azure_container_client , main_config['temp_folder_download']  , i)
            id_list = directory_obj.reading_file(main_config['temp_folder_download']  , i)
            
        print('Knowledge Base updated for ' + i)
        
    print('Any new data in knowledge base bas been updated')

if __name__ == "__main__":
    main_config = json.load(open('main_config.json'))
    main(main_config)