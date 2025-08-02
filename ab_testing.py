import argparse
import json
import os
from src.ab_testing_framework.ab_testing_with_qa import ABAgenticTester

def parse_args():
    parser = argparse.ArgumentParser(
        description="Run A/B testing on LLM models using a configuration file."
    )
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to the A/B testing config JSON file (e.g., src/config/ab_testing_config.json)"
    )
    return parser.parse_args()

def main():
    args = parse_args()

    config_path = args.config
    if not os.path.isfile(config_path):
        print(f"❌ Config file not found: {config_path}")
        exit(1)

    with open(config_path, "r") as f:
        config = json.load(f)

    questions_file = config.get("questions_file", "src/config/questions.json")
    model_configurations = config.get("models", {})

    if not model_configurations:
        print("❌ No model configurations found in the config.")
        exit(1)

    tester = ABAgenticTester(questions_file=questions_file)
    tester.run_test(model_configurations)

if __name__ == "__main__":
    main()
