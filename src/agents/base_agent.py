class BaseAgent:
    def run(self, user_input, thread_context):
        raise NotImplementedError("Each agent must implement the run() method.")
