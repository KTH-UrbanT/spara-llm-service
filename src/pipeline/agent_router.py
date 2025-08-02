from src.agents.router_agent import RouterAgent
from src.agents.building_agent import BuildingAgent
from src.agents.cluster_agent import ClusterAgent
from src.agents.generic_agent import GenericAgent
from src.agents.aggregator_agent import AggregatorAgent
from src.agents.conversationalist_agent import ConversationalAgent

class AgentRouter:
    def __init__(self):
        self.router = RouterAgent()
        self.building = BuildingAgent()
        self.cluster = ClusterAgent()
        self.generic = GenericAgent()
        self.aggregator = AggregatorAgent()
        self.conversationallist = ConversationalAgent()
        

    def route_message(self, messages, last_message , metadata , thread_id):
        if len(messages)!=1 : 
            previous_classification = messages[-2]['classification']
        else : 
            previous_classification = None
        classified = self.router.classify_question(last_message , previous_classification)
        print(classified)
        if classified == 'generic':
            response = self.generic.handle_generic_input(last_message , messages)
            return {'content' : response , 'classification' : classified , 'agent_answered' : 'generic' }
        elif classified == 'building_specific':
            return self.building.handle_building_query(last_message, messages,metadata , thread_id)

        elif classified == 'cluster':
            return self.cluster.handle_cluster_query(last_message, messages ,metadata , thread_id)

        elif classified == 'conversational':
            response =  self.conversationallist.handle_conversational_input(last_message,messages)
            return {'content' : response , 'classification' : classified , 'agent_answered' : 'conversationalist' }
            
        else:
            # Fallback handling, optional
            return {"error": f"Unknown classification: {classified}"}
        

