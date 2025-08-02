import json
import pandas as pd
import redis
import time
import os
from dotenv import load_dotenv

load_dotenv()

try:
    from src.redis.rag_redis_pub_sub import RedisQueueManager as SingleLLMRAGManager
    from src.redis.redis_manager import RedisQueueManager as AgenticRAGManager
except ImportError as e:
    print(f"Import error: {e}. Check your project structure.")
    exit()

class ABAgenticTester:
    PROBLEM_TYPES = {
        1: "Incorrect Response",
        2: "Imprecise Response",
        3: "Ambiguous Response",
        4: "Too Shallow",
        5: "Irrelevant Response",
        6: "Non-response",
        7: "Overconfident Wrong Answer",
        8: "Broken Flow / Dialogue Misunderstanding",
        9: "Other"
    }

    FEEDBACK_SOURCES = {
        "user_rating": "User Rating",
        "follow_up": "Follow-up Feedback Question",
        "manual_feedback": "User-initiated Feedback",
        "expert_review": "Expert Evaluation",
        "heuristic_flag": "Automated Heuristic Flagging",
        "user_escalation": "User Complaint Escalation"
    }

    CORRECTIVE_ACTIONS = {
        "Prompt Engineering",
        "Knowledge Base Update",
        "Data-driven Service Update",
        "Fine-tuning / Model Re-training",
        "Fallback Improvement",
        "Dialogue Management Update",
        "Clarification Prompting",
        "Confidence Calibration"
    }

    def __init__(self, input_file="test_data.json", redis_host=None, redis_port=None):
        self.data = self._load_data(input_file)
        self.redis_host = redis_host or os.getenv("REDIS_HOST", "127.0.0.1")
        self.redis_port = redis_port or int(os.getenv("REDIS_PORT", 6379))
        self.results = []

        self.redis_client = None
        for _ in range(5):
            try:
                self.redis_client = redis.StrictRedis(
                    host=self.redis_host, port=self.redis_port, decode_responses=True)
                self.redis_client.ping()
                print(f"✅ Connected to Redis at {self.redis_host}:{self.redis_port}")
                break
            except redis.exceptions.ConnectionError:
                print("⚠️ Redis connection failed, retrying...")
                time.sleep(2)
        if not self.redis_client:
            raise Exception("❌ Could not connect to Redis")

    def _load_data(self, filepath):
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Failed to load input data file: {e}")
            exit()

    def _send_and_receive(self, model_type, thread_id, message, role="user"):
        thread_key_single = thread_id
        thread_key_agentic = f"thread:{thread_id}"

        if model_type == "single_llm_rag":
            manager = SingleLLMRAGManager(redis_host=self.redis_host, redis_port=self.redis_port)
            manager.add_message_to_thread(thread_key_single, message)
            check_key = thread_key_single
            get_messages_func = manager.read_all_messages
            target_role = "assistant"
        elif model_type == "agentic_rag":
            manager = AgenticRAGManager(redis_host=self.redis_host, redis_port=self.redis_port)
            manager.add_message_to_thread(thread_id, role, message)
            check_key = thread_key_agentic
            get_messages_func = manager.get_thread_messages
            target_role = "assistant"
        else:
            return "ERROR: Invalid model type.", None, None, None

        start = time.time()
        timeout = 120
        while time.time() - start < timeout:
            try:
                messages = get_messages_func(check_key)
                if messages:
                    last = messages[-1]
                    if last.get("role") == target_role:
                        classification = last.get("classification") if model_type=="agentic_rag" else None
                        agent_answered = last.get("agent_answered") if model_type=="agentic_rag" else None
                        return last.get("content"), classification, agent_answered, time.time() - start
                time.sleep(1)
            except redis.exceptions.ConnectionError:
                time.sleep(3)
            except Exception:
                break
        return "ERROR: Timeout/no assistant response.", None, None, None

    def run_test(self, model_configs):
        print("\n--- Starting Tests ---")

        for item in self.data:
            item_id = item.get("id", "unknown")
            is_dialogue = "turns" in item and isinstance(item["turns"], list)

            for model_name, model_type in model_configs.items():
                thread_id = f"abtest_{model_name}_{item_id}_{int(time.time())}"

                # Clean Redis keys
                self.redis_client.delete(f"thread:{thread_id}")
                self.redis_client.delete(thread_id)

                if is_dialogue:
                    turns = item["turns"]
                    assistant_replies = []
                    classifications = []
                    agent_answered_flags = []
                    latencies = []

                    for i in range(0, len(turns), 2):
                        user_text = turns[i].get("user")
                        if not user_text:
                            print(f"Missing user message in turn {i} in dialogue {item_id}")
                            continue

                        resp, classification, agent_answered, latency = self._send_and_receive(
                            model_type, thread_id, user_text, role="user")
                        print(f"[{model_name}] Dialogue {item_id} Turn {i//2+1} Q: {user_text}")
                        print(f"Assistant: {resp[:200]}{'...' if len(resp)>200 else ''}")

                        assistant_replies.append(resp)
                        classifications.append(classification)
                        agent_answered_flags.append(agent_answered)
                        latencies.append(latency)

                    print(f"\nDialogue {item_id} completed for model {model_name}.")
                    self._human_evaluation_dialogue(
                        item_id, model_name, model_type,
                        item.get("expected_classification"),
                        turns, assistant_replies, classifications,
                        agent_answered_flags, latencies)

                else:
                    question = item.get("question")
                    if not question:
                        print(f"Missing question in item {item_id}, skipping...")
                        continue

                    resp, classification, agent_answered, latency = self._send_and_receive(
                        model_type, thread_id, question, role="user")

                    print(f"[{model_name}] Single Q&A {item_id} Q: {question}")
                    print(f"Assistant: {resp[:200]}{'...' if len(resp)>200 else ''}")

                    self._human_evaluation_single_qa(
                        item_id, model_name, model_type,
                        item.get("expected_classification"),
                        question, resp, classification,
                        agent_answered, latency)

        print("\n--- All Tests Finished ---")
        self.export_results_to_excel("ab_test_results_combined.xlsx")
        self.generate_visualizations()

    def _human_evaluation_single_qa(self, item_id, model_name, model_type,
                                   expected_classification,
                                   question, assistant_reply,
                                   classification, agent_answered, latency):

        print(f"\nEvaluation for single Q&A {item_id} (Model: {model_name}):")
        print(f"Q: {question}")
        print(f"A: {assistant_reply}")

        is_satisfactory = input("Is the answer satisfactory? (Y/N): ").strip().upper()

        relevance = completeness = accuracy = coherence = helpfulness = None
        problem_type = None
        evaluator_comment = ""

        if is_satisfactory == 'N':
            relevance = self._get_valid_score("  Relevance (1-5): ")
            completeness = self._get_valid_score("  Completeness (1-5): ")
            accuracy = self._get_valid_score("  Accuracy (1-5): ")
            coherence = self._get_valid_score("  Coherence/Readability (1-5): ")
            helpfulness = self._get_valid_score("  Helpfulness (1-5): ")
            problem_type = self._get_feedback_and_problem_type()
            evaluator_comment = input("Additional comments? (Optional): ").strip()
        elif is_satisfactory == 'Y':
            relevance = completeness = accuracy = coherence = helpfulness = 5
            print("Marked satisfactory with perfect scores.")
        else:
            print("Invalid input; skipping detailed feedback.")

        corrective_actions = self.suggest_corrective_actions(problem_type) if problem_type else []

        self.results.append({
            "item_id": item_id,
            "model_name": model_name,
            "model_type": model_type,
            "question": question,
            "assistant_reply": assistant_reply,
            "relevance": relevance,
            "completeness": completeness,
            "accuracy": accuracy,
            "coherence": coherence,
            "helpfulness": helpfulness,
            "latency_seconds": latency,
            "actual_classification": classification if classification is not None else "",
            "expected_classification": expected_classification if expected_classification is not None else "",
            "classification_match": (classification == expected_classification) if classification is not None and expected_classification is not None else "",
            "agent_answered": agent_answered if agent_answered is not None else "",
            "problem_type": problem_type if problem_type else "",
            "corrective_actions": ", ".join(corrective_actions),
            "evaluator_comment": evaluator_comment
        })

    def _human_evaluation_dialogue(self, item_id, model_name, model_type,
                                   expected_classification,
                                   turns, assistant_replies, classifications,
                                   agent_answered_flags, latencies):

        print(f"\nEvaluation for dialogue {item_id} (Model: {model_name}):")
        for i, (turn, reply) in enumerate(zip(turns, assistant_replies)):
            print(f"Turn {i+1} User: {turn.get('user', '')}")
            print(f"Assistant: {reply[:200]}{'...' if len(reply) > 200 else ''}")

        is_satisfactory = input("Is the overall dialogue satisfactory? (Y/N): ").strip().upper()

        relevance = completeness = accuracy = coherence = helpfulness = None
        problem_type = None
        evaluator_comment = ""

        if is_satisfactory == 'N':
            relevance = self._get_valid_score("  Relevance (1-5): ")
            completeness = self._get_valid_score("  Completeness (1-5): ")
            accuracy = self._get_valid_score("  Accuracy (1-5): ")
            coherence = self._get_valid_score("  Coherence/Readability (1-5): ")
            helpfulness = self._get_valid_score("  Helpfulness (1-5): ")
            problem_type = self._get_feedback_and_problem_type()
            evaluator_comment = input("Additional comments? (Optional): ").strip()
        elif is_satisfactory == 'Y':
            relevance = completeness = accuracy = coherence = helpfulness = 5
            print("Marked satisfactory with perfect scores.")
        else:
            print("Invalid input; skipping detailed feedback.")

        corrective_actions = self.suggest_corrective_actions(problem_type) if problem_type else []

        self.results.append({
            "item_id": item_id,
            "model_name": model_name,
            "model_type": model_type,
            "questions": "\n---\n".join([t.get("user", "") for t in turns if "user" in t]),
            "assistant_replies": assistant_replies,
            "relevance": relevance,
            "completeness": completeness,
            "accuracy": accuracy,
            "coherence": coherence,
            "helpfulness": helpfulness,
            "latency_seconds": sum([l or 0 for l in latencies]),
            "actual_classifications": classifications if classifications else [],
            "expected_classification": expected_classification if expected_classification else "",
            "classification_match": " / ".join(
                ["✓" if c == expected_classification else "✗" for c in classifications]) if classifications else "",
            "agent_answered_flags": agent_answered_flags if agent_answered_flags else [],
            "problem_type": problem_type if problem_type else "",
            "corrective_actions": ", ".join(corrective_actions),
            "evaluator_comment": evaluator_comment
        })

    def _get_valid_score(self, prompt_text):
        while True:
            try:
                val = int(input(prompt_text).strip())
                if 1 <= val <= 5:
                    return val
            except ValueError:
                pass
            print("Please enter a valid integer between 1 and 5.")

    def _get_feedback_and_problem_type(self):
        print("Select the type of problem encountered:")
        for k, v in self.PROBLEM_TYPES.items():
            print(f"{k}: {v}")
        while True:
            try:
                choice = int(input("Enter the number corresponding to the problem type: ").strip())
                if choice in self.PROBLEM_TYPES:
                    return self.PROBLEM_TYPES[choice]
            except ValueError:
                pass
            print("Invalid selection. Try again.")

    def suggest_corrective_actions(self, problem_type):
        # Simple mapping example
        if problem_type in ["Incorrect Response", "Overconfident Wrong Answer"]:
            return ["Fine-tuning / Model Re-training", "Knowledge Base Update"]
        elif problem_type == "Imprecise Response":
            return ["Prompt Engineering", "Clarification Prompting"]
        elif problem_type == "Broken Flow / Dialogue Misunderstanding":
            return ["Dialogue Management Update", "Clarification Prompting"]
        elif problem_type == "Non-response":
            return ["Fallback Improvement"]
        else:
            return ["Prompt Engineering"]

    def export_results_to_excel(self, filename):
        if not self.results:
            print("No results to export.")
            return
        df = pd.DataFrame(self.results)
        df.to_excel(filename, index=False)
        print(f"Results exported to {filename}")

    def generate_visualizations(self):
        # Placeholder for visualization code if needed
        print("Visualization generation is not implemented yet.")
