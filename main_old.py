from openai_chatbot.main import OpenAIChatBot
from utility import *
import argparse
import json

class SparaMain() : 
    def __init__(self , config_main) -> None:
        self.config_main = config_main
        self.chatbot_config = json.load(open(self.config_main['language_model_path'] + '/py_config.json', 'r'))
        
    def chatbot_setup(self) : 
        with open(self.config_main['prompt_path'] )  as f : 
            prompt = f.read()
        self.prompt_list = [ {'role' : 'system', 'content' : [{ 'type' : 'text' , 'text' : prompt}]} ]
        chatbot_obj = OpenAIChatBot(self.chatbot_config , self.prompt_list )
        return chatbot_obj
    
    def chat_with_language_model(self , content , type , chatbot_obj) : 
        #print(chatbot_obj.prompt)
        response = chatbot_obj.openai_setup(content , type)
        return chatbot_obj, response


#if __name__ == 'main':
def test_function () : 
    print('reached here')
    parser = argparse.ArgumentParser(description = "This is the main function to ")
    parser.add_argument('--path' , help = 'paste the path to config file which contains information regarding models to be used for openai and other respective arguments')
    args = parser.parse_args()
    config_main = parsing_config(args.path)
    print(config_main)