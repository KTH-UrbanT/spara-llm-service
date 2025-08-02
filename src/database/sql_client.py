import requests

import random

class SQLClient :
    def __init__(self):
        pass
    def fetch_building_data(self , building_id):
        # return requests.get(f"http://your-sql-api/buildings/{building_id}").json()
        res = requests.get(f"https://oden.abe.kth.se/api/v1/buildings/492db706-b583-4211-8f51-34dc7a3f6d7d/").json()
        return res
        
    def building_by_address(self, extracted) : 
        req = f"http://localhost:8001/buildings/single_filter?filter_name=epc_idadr&filter_value={extracted}"
        response = requests.get(req)    
        return response   
        
    def get_address_by_username(self , thread_id) : 
        user_name = thread_id.split(':')[0]
        return random.choice(['Teknigringen 10B', 'Malvinas Väg 10', 'Drottning Kristinas väg 43B', 'Professorsslingan 51'])
        
