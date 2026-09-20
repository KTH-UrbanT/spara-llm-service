"""Chat Completions options for the deployed models and legacy fallbacks."""


def completion_options(deployment, *, max_tokens=800, temperature=0.2,
                       top_p=0.23, reasoning_tokens=4096):
    name = (deployment or "").lower()
    if name.startswith(("gpt-5.6", "gpt-6-astra")):
        # The limit includes both hidden reasoning and visible output tokens.
        # These deployments do not accept the legacy sampling parameters.
        return {"max_completion_tokens": reasoning_tokens, "reasoning_effort": "low"}
    if name.startswith(("o3", "o4")):
        return {"max_completion_tokens": 4000}
    return {
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "frequency_penalty": 0,
        "presence_penalty": 0,
    }
