"""Azure OpenAI v1 endpoint handling shared by chat and embedding clients."""
from urllib.parse import urlsplit, urlunsplit


def azure_v1_base_url(endpoint: str | None) -> str:
    """Accept a resource root or v1 URL; reject project/deployment URLs."""
    if not endpoint or not endpoint.strip():
        raise ValueError("AZURE_ENDPOINT is required for Azure OpenAI v1.")
    parts = urlsplit(endpoint.strip())
    if (
        parts.scheme != "https" or not parts.hostname
        or parts.username is not None or parts.password is not None
        or parts.query or parts.fragment
        or parts.path.rstrip("/") not in ("", "/openai/v1")
    ):
        raise ValueError(
            "AZURE_ENDPOINT must be an HTTPS resource root or /openai/v1/ URL "
            "without credentials, query parameters, or a project/deployment path."
        )
    return urlunsplit((parts.scheme, parts.netloc, "/openai/v1/", "", ""))
