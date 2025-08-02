# def check_address_match(): 
#     return True
import re
import logging
from src.database.sql_client import SQLClient
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)
sql_client = SQLClient()

def search_for_address(prompt, buildings=None):
    pattern = re.compile(r"\b(?:bor\ på|address\ är|live\ at|live\ in|address\ is)\b\W+(\w+(?:\W+\w+){0,1})", re.IGNORECASE)
    address = pattern.findall(prompt)

    if address:
        extracted = address[0]
        # Optional: validate via buildings DB
        try:
            # req = f"http://localhost:8001/buildings/single_filter?filter_name=epc_idadr&filter_value={extracted}"
            # response = requests.get(req)
            response = sql_client.building_by_address(extracted)
            logger.debug(f"Address search result: {response.text}")
            return extracted , response.text
        except Exception as e:
            logger.warning(f"Failed address lookup: {e}")
            return extracted , None # fallback: still use the extracted term
    return None

def check_address_match(user_input, thread_id):
    extracted_address , building_information = search_for_address(user_input)
    address_from_user = sql_client.get_address_by_username(thread_id)
    if extracted_address == address_from_user : 
        return 'Address given by user matches address in database' , extracted_address , building_information

    if extracted_address == None : 
        response = sql_client.building_by_address(extracted_address)
        return 'Address is extracted from user_id' , address_from_user , response.text
    
    elif extracted_address != address_from_user : 
        return 'Address is a mismatch' , extracted_address , building_information
                  

                  
                  
                  
                  
                  
                  
                  
                  
                  
                  
                  
                  
                  
                  
                  
                  
                  
                  
                  
                  
                  
                  
                  
                  
                  
                

    
