from src.agents.base_agent import BaseAgent

class AggregatorAgent(BaseAgent):
    def run(self, responses, thread_context):
        # Choose the most complete/least conflicting answer
        return max(responses, key=len)
