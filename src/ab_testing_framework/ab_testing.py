import json
import pandas as pd
import redis
import time
import os
import matplotlib.pyplot as plt
import seaborn as sns
from dotenv import load_dotenv

# Load environment variables (e.g., REDIS_HOST, REDIS_PORT)
load_dotenv()

# Import your Redis managers
try:
    from rag_redis_pub_sub import RedisQueueManager as SingleLLMRAGManager
    from redis_manager import RedisQueueManager as AgenticRAGManager
except ImportError as e:
    print(f"Error importing Redis managers: {e}")
    print("Please ensure 'rag_redis_pub_sub.py' and 'redis_manager.py' are in the same directory or accessible via PYTHONPATH.")
    print("Also ensure that 'main_retrieval_generation.py' (for rag_redis_pub_sub) and 'src.pipeline.agent_router' (for redis_manager) dependencies are met.")
    exit()

class ABAgenticTester:
    # Define problem types as per your quality assurance check
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

    def __init__(self, questions_file="questions.json", redis_host=None, redis_port=None):
        self.questions = self._load_questions(questions_file)
        self.redis_host = redis_host or os.getenv("REDIS_HOST", "127.0.0.1")
        self.redis_port = redis_port or int(os.getenv("REDIS_PORT", 6379))
        self.results = []
        self.pubsub_channel = 'thread_events'

        # Initialize a direct Redis client for thread cleanup and basic operations
        self.redis_client = None
        retries = 5
        while retries > 0:
            try:
                self.redis_client = redis.StrictRedis(
                    host=self.redis_host,
                    port=self.redis_port,
                    decode_responses=True
                )
                self.redis_client.ping()
                print(f"✅ Connected to Redis at {self.redis_host}:{self.redis_port}")
                break
            except redis.exceptions.ConnectionError as e:
                retries -= 1
                print(f"⚠️ Redis connection failed ({e}), retries left: {retries}")
                time.sleep(2)
        if not self.redis_client:
            raise Exception("❌ Failed to connect to Redis after several retries.")

    def _load_questions(self, file_path):
        """Loads test questions from a JSON file."""
        try:
            with open(file_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"Error: questions file '{file_path}' not found.")
            exit()
        except json.JSONDecodeError:
            print(f"Error: Could not decode JSON from '{file_path}'. Check file format.")
            exit()

    def _get_response_from_model(self, model_type, thread_id, question_text):
        """
        Sends a message to the specified model via Redis and waits for its response.
        This uses polling for simplicity.
        """
        thread_key_single_llm = thread_id # For rag_redis_pub_sub, thread_name is directly the key
        thread_key_agentic_rag = f"thread:{thread_id}" # For redis_manager, keys are prefixed

        print(f"  Sending message to {model_type} for thread {thread_id}...")

        if model_type == "single_llm_rag":
            manager = SingleLLMRAGManager(redis_host=self.redis_host, redis_port=self.redis_port)
            manager.add_message_to_thread(thread_key_single_llm, question_text)
            check_key = thread_key_single_llm
            get_messages_func = manager.read_all_messages
            target_role = "assistant"

        elif model_type == "agentic_rag":
            manager = AgenticRAGManager(redis_host=self.redis_host, redis_port=self.redis_port)
            manager.add_message_to_thread(thread_id, "user", question_text)
            check_key = thread_key_agentic_rag
            get_messages_func = manager.get_thread_messages
            target_role = "assistant"
        else:
            return "ERROR: Invalid model type.", None, None, None, None

        start_time = time.time()
        timeout_seconds = 120 # Increased timeout for potentially slower LLM/Agentic RAG responses

        while time.time() - start_time < timeout_seconds:
            try:
                messages = get_messages_func(check_key)
                if messages:
                    last_message = messages[-1]
                    if last_message.get("role") == target_role:
                        classification = last_message.get("classification") if model_type == "agentic_rag" else None
                        agent_answered = last_message.get("agent_answered") if model_type == "agentic_rag" else None
                        return last_message.get("content"), classification, agent_answered, (time.time() - start_time)
                time.sleep(1) # Wait 1 second before checking again
            except redis.exceptions.ConnectionError as e:
                print(f"Redis connection error during polling: {e}. Retrying...")
                time.sleep(5)
            except Exception as e:
                print(f"An unexpected error occurred during message retrieval: {e}")
                break

        print(f"Timeout reached for thread {thread_id} ({model_type}). No assistant response received.")
        return "ERROR: Timeout or no assistant response received.", None, None, None

    def run_test(self, model_configs):
        """
        Runs the A/B test by iterating through questions and model configurations,
        collecting responses and human evaluations.
        """
        print("\n--- Starting A/B Test ---")
        for question in self.questions:
            question_id = question['id']
            question_text = question['text']
            expected_classification = question.get('expected_classification')

            print(f"\n--- Testing Question: {question_id} - '{question_text}' ---")

            for model_name, model_type in model_configs.items():
                thread_id = f"ab_test_thread_{model_name}_{question_id}_{int(time.time())}"
                print(f"  Testing Model: {model_name} (Type: {model_type}) with temporary thread: {thread_id}")

                # Clean up previous thread data for this specific test case (important for consistency)
                self.redis_client.delete(f"thread:{thread_id}") # For redis_manager HASH keys
                self.redis_client.delete(thread_id) # For rag_redis_pub_sub LIST keys

                response_content, classification, agent_answered, latency = \
                    self._get_response_from_model(model_type, thread_id, question_text)

                print(f"    Response from {model_name}: {response_content[:500]}{'...' if len(response_content) > 500 else ''}")
                if classification:
                    print(f"    Classification: {classification} (Expected: {expected_classification})")
                if agent_answered:
                    print(f"    Agent Answered: {agent_answered}")
                if latency is not None:
                    print(f"    Latency: {latency:.2f} seconds")

                # --- Human Evaluation Step (Expert Evaluation Flow) ---
                print(f"\n--- Human Evaluation for {model_name} on Question {question_id} ---")
                print(f"Question: {question_text}")
                print(f"Response (first 500 chars): {response_content[:500]}{'...' if len(response_content) > 500 else ''}")

                is_satisfactory = input("  Is the response satisfactory? (Y/N): ").strip().upper()

                relevance = completeness = accuracy = coherence = helpfulness = None
                problem_type = None
                evaluator_comment = ""

                if is_satisfactory == 'N':
                    print("\n  Please provide detailed feedback:")
                    relevance = self._get_valid_score("  Relevance (1-5): ")
                    completeness = self._get_valid_score("  Completeness (1-5): ")
                    accuracy = self._get_valid_score("  Accuracy (1-5): ")
                    coherence = self._get_valid_score("  Coherence/Readability (1-5): ")
                    helpfulness = self._get_valid_score("  Helpfulness (1-5): ")

                    problem_type = self._get_feedback_and_problem_type()
                    evaluator_comment = input("  Any additional comments? (Optional): ").strip()
                elif is_satisfactory == 'Y':
                    # If satisfactory, assume perfect scores for a simplified model
                    relevance = completeness = accuracy = coherence = helpfulness = 5
                    print("  Response marked as satisfactory. Assigning perfect scores.")
                else:
                    print("  Invalid input for satisfaction. Skipping detailed feedback for this response.")


                self.results.append({
                    "question_id": question_id,
                    "question_text": question_text,
                    "model_name": model_name,
                    "model_type": model_type,
                    "response_content": response_content,
                    "relevance": relevance,
                    "completeness": completeness,
                    "accuracy": accuracy,
                    "coherence": coherence,
                    "helpfulness": helpfulness,
                    "latency_seconds": latency,
                    "actual_classification": classification,
                    "expected_classification": expected_classification,
                    "classification_match": (classification == expected_classification) if expected_classification else None,
                    "agent_answered": agent_answered,
                    "is_satisfactory": is_satisfactory == 'Y', # New field
                    "problem_type": problem_type, # New field
                    "evaluator_comment": evaluator_comment # New field
                })

        print("\n--- A/B Testing Complete ---")
        self.export_results_to_excel("ab_test_results.xlsx")
        self.generate_visualizations()

    def _get_valid_score(self, prompt):
        """Helper function to get validated integer input for scores (1-5)."""
        while True:
            try:
                score = int(input(prompt))
                if 1 <= score <= 5:
                    return score
                else:
                    print("Please enter a score between 1 and 5.")
            except ValueError:
                print("Invalid input. Please enter a number.")

    def _get_feedback_and_problem_type(self):
        """Prompts the user to classify the problem type."""
        print("\n  Please select the primary problem type:")
        for key, desc in self.PROBLEM_TYPES.items():
            print(f"    {key}. {desc}")

        while True:
            try:
                choice = int(input("  Enter number for problem type: "))
                if choice in self.PROBLEM_TYPES:
                    return self.PROBLEM_TYPES[choice]
                else:
                    print("  Invalid choice. Please select a number from the list.")
            except ValueError:
                print("  Invalid input. Please enter a number.")

    def export_results_to_excel(self, filename):
        """Exports the test results to an Excel file."""
        if not self.results:
            print("No results to export.")
            return

        df = pd.DataFrame(self.results)
        df.to_excel(filename, index=False)
        print(f"Results exported to {filename}")

    def generate_visualizations(self):
        """Generates various plots to visualize the A/B test results."""
        if not self.results:
            print("No results to visualize.")
            return

        df = pd.DataFrame(self.results)

        # Filter out rows where scores were not provided (e.g., if user skipped feedback)
        # Only include rows where relevance (or any other score) is not None for average calculations
        df_scored = df.dropna(subset=['relevance', 'completeness', 'accuracy', 'coherence', 'helpfulness'], how='all')

        if not df_scored.empty:
            # Calculate average scores for each model
            avg_scores = df_scored.groupby('model_name')[['relevance', 'completeness', 'accuracy', 'coherence', 'helpfulness']].mean(numeric_only=True)
            print("\nAverage Scores per Model:\n", avg_scores)

            # 1. Bar chart for average scores per metric per model
            plt.figure(figsize=(14, 8))
            avg_scores.plot(kind='bar', figsize=(14, 8), ax=plt.gca())
            plt.title('Average Performance Scores per Model Across Metrics')
            plt.ylabel('Average Score (1-5)')
            plt.xlabel('Model Name')
            plt.xticks(rotation=45, ha='right')
            plt.legend(title='Metric', bbox_to_anchor=(1.05, 1), loc='upper left')
            plt.tight_layout()
            plt.savefig("average_scores_bar_chart.png")
            plt.show()

            # 2. Pie chart for overall "win" rates (example: highest combined score)
            df_scored['total_score'] = df_scored[['relevance', 'completeness', 'accuracy', 'coherence', 'helpfulness']].sum(axis=1)
            # Find the winning model for each question_id based on total_score
            idx = df_scored.groupby('question_id')['total_score'].idxmax()
            winning_models = df_scored.loc[idx, 'model_name'].value_counts()

            if not winning_models.empty:
                plt.figure(figsize=(10, 10))
                plt.pie(winning_models, labels=winning_models.index, autopct='%1.1f%%', startangle=90, colors=sns.color_palette("viridis", len(winning_models)))
                plt.title('Percentage of Questions "Won" by Each Model (Based on Total Score)')
                plt.axis('equal')
                plt.savefig("model_win_rate_pie_chart.png")
                plt.show()
            else:
                print("No winning models to display for the pie chart.")

            # 3. Box plots for distribution of individual metric scores per model
            metrics_to_plot = ['relevance', 'completeness', 'accuracy', 'coherence', 'helpfulness']
            for metric in metrics_to_plot:
                plt.figure(figsize=(10, 6))
                sns.boxplot(x='model_name', y=metric, data=df_scored, palette='pastel')
                plt.title(f'{metric} Score Distribution per Model')
                plt.ylabel(f'{metric} Score (1-5)')
                plt.xlabel('Model Name')
                plt.xticks(rotation=45, ha='right')
                plt.tight_layout()
                plt.savefig(f"{metric}_boxplot.png")
                plt.show()
        else:
            print("No valid scores available for average score and box plot visualizations.")


        # 4. Latency Bar Chart (if latency data is available)
        if 'latency_seconds' in df.columns and df['latency_seconds'].notna().any():
            avg_latency = df.groupby('model_name')['latency_seconds'].mean().sort_values()
            plt.figure(figsize=(10, 6))
            sns.barplot(x=avg_latency.index, y=avg_latency.values, palette='coolwarm')
            plt.title('Average Response Latency per Model')
            plt.ylabel('Average Latency (Seconds)')
            plt.xlabel('Model Name')
            plt.xticks(rotation=45, ha='right')
            plt.tight_layout()
            plt.savefig("average_latency_bar_chart.png")
            plt.show()

        # 5. Classification Accuracy (for Agentic RAG models if applicable)
        classification_df = df[df['classification_match'].notna()]
        if not classification_df.empty:
            accuracy_by_model = classification_df.groupby('model_name')['classification_match'].mean() * 100
            plt.figure(figsize=(10, 6))
            sns.barplot(x=accuracy_by_model.index, y=accuracy_by_model.values, palette='Set2')
            plt.title('Classification Accuracy per Model')
            plt.ylabel('Accuracy (%)')
            plt.xlabel('Model Name')
            plt.xticks(rotation=45, ha='right')
            plt.ylim(0, 100)
            plt.tight_layout()
            plt.savefig("classification_accuracy_bar_chart.png")
            plt.show()

        # 6. Distribution of Problem Types (New Visualization for QA Check)
        # Filter for rows where problem_type was identified (i.e., not None)
        df_problems = df[df['problem_type'].notna()]
        if not df_problems.empty:
            plt.figure(figsize=(14, 8))
            sns.countplot(data=df_problems, x='problem_type', hue='model_name', palette='viridis', order=self.PROBLEM_TYPES.values())
            plt.title('Distribution of Identified Problem Types per Model')
            plt.xlabel('Problem Type')
            plt.ylabel('Count')
            plt.xticks(rotation=45, ha='right')
            plt.legend(title='Model', bbox_to_anchor=(1.05, 1), loc='upper left')
            plt.tight_layout()
            plt.savefig("problem_type_distribution_bar_chart.png")
            plt.show()
        else:
            print("No problem types were identified to visualize.")

if __name__ == "__main__":
    model_configurations = {
        "Simple_RAG_GPT3.5": "single_llm_rag", # Uses rag_redis_pub_sub.py
        "Agentic_RAG_V1": "agentic_rag",     # Uses redis_manager.py
        # Add more models here as needed for your A/B test
    }

    tester = ABAgenticTester(questions_file="questions.json")
    tester.run_test(model_configurations)