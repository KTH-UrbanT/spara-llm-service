import pandas as pd 
import numpy as np 
from openai import OpenAI
import json
import argparse
from utility import *
import os
from dotenv import load_dotenv
from pathlib import Path


class OpenAIChatBot : 
    def __init__(self , config , prompt):
        self.config = config
        self.prompt = prompt 
#        load_dotenv(".env", override=True)
        load_dotenv(Path('.env'))
        self.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
#        self.OPENAI_API_KEY = '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'
        self.read_config()
        
        
    def read_config(self) :
        #self.config = parsing_config(self.path)
        print('Open AI Model being used is ' + self.config['model_name'] )
        
        
        
    def openai_setup (self , content , type ) :
        client = OpenAI(api_key = self.OPENAI_API_KEY)
        self.prompt.append({'role':'user' , 'content' : [{'type':type , 'text' : content}]})
        response = client.chat.completions.create(
        model = self.config['model_name'],
        messages =  self.prompt,
        temperature = self.config['temperature'],
        max_tokens = self.config['max_tokens'],
        top_p = self.config['top_p'],
        frequency_penalty = self.config['frequency_penalty'],
        presence_penalty = self.config['presence_penalty'],
        response_format={
            "type": "text"
        }
        )
        self.prompt.append({'role':'assistant' , 'content' : [{'type':type , 'text' : response.choices[0].message.content}]})
        return response
        
        
if __name__ == 'main':
    print('reached here')
    parser = argparse.ArgumentParser(description = "Setting up of language model of OpenAI.")
    parser.add_argument('--path' , help = 'paste the path to config file which contains information regarding models to be used for openai and other respective arguments')
    args = parser.parse_args()
    config = parsing_config(args.path)
    print(config)
    