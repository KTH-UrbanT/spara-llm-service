import logging

def configure_logging():
    # Set root logger level (optional, usually INFO or WARNING)
    logging.basicConfig(level=logging.INFO)

    # Silence verbose Azure SDK and HTTP client loggers globally
    noisy_loggers = [
        "azure.core.pipeline",
        "azure.core.pipeline.policies.http_logging_policy",
        "azure.ai.inference",
        "httpx",
        "urllib3",
    ]

    for logger_name in noisy_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)