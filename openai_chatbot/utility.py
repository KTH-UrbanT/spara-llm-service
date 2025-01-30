import os
import json

def parsing_config(args_path) : 
    try :         
        json_object = json.load(open(args_path , 'r'))
        return json_object
    except : 
        print(Exception)
        print('Exception: pass path')