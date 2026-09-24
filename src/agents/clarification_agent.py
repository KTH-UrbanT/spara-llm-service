"""Generate one bounded clarification question; never select a building or take action."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time

from src.agents.completion_options import completion_options
from src.azure_openai_v1 import azure_v1_base_url
from src.pipeline.telemetry import record_model_call

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "clarification_prompt.txt"
IDENTITY_FOCUS = {
    "missing_address": "building_identity",
    "missing_brf_name": "building_identity",
    "insufficient_data_for_personalized_advice": "building_identity",
    "ambiguous_address": "building_location",
    "ambiguous_brf": "building_selection",
    "brf_not_found": "building_identity",
    "brf_lookup_failed": "building_identity",
    "building_not_found_by_id": "building_identity",
    "building_not_found": "building_identity",
}
OPEN_FOCI = ["objective", "scope", "observation", "timing", "constraint", "missing_evidence"]
FACT_KEYS = (
    "building_type", "heating_system", "ventilation_type", "energy_class",
    "energy_performance", "construction_year", "energy_declaration_year",
)


def _history(messages):
    """Bound prompt size without forwarding metadata, credentials or system messages."""
    result, remaining = [], 16000
    for message in reversed(messages or []):
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        content = content[:min(2000, remaining)]
        if not content:
            break
        result.append({"role": message["role"], "content": content})
        remaining -= len(content)
    return list(reversed(result))


def _context(state, reason, foci):
    md = state.get("metadata") or {}
    facts = md.get("retrieved_facts") or {}
    selected = md.get("selected_brf_building_id") or md.get("byggnadsid")
    fact_id = facts.get("byggnadsid") or facts.get("building_id")
    identity = md.get("building_identity_check") or {}
    verified = bool(selected and fact_id == selected and identity.get("status") == "passed")
    pending = md.get("pending_building_advice") or {}
    return {
        "latest_user_message": str(state.get("last_message") or "")[:4000],
        "history": _history(state.get("messages")),
        "reason": reason,
        "allowed_focus": foci,
        "original_request": str((state.get("context") or {}).get("answer_focus") or pending.get("question") or "")[:2000],
        "identity_resolved": bool(selected and (verified or md.get("selected_brf_building_id"))),
        "address_already_supplied": bool(md.get("address") or md.get("address_from_user")),
        "brf_already_supplied": bool(md.get("brf_name")),
        "recorded_facts": {key: facts[key] for key in FACT_KEYS if verified and isinstance(facts.get(key), (str, int, float, bool))},
    }


def _valid_question(data, foci, context):
    if not isinstance(data, dict) or set(data) != {"focus", "question"}:
        return False
    question = data.get("question")
    if data.get("focus") not in foci or not isinstance(question, str):
        return False
    question = question.strip()
    if not 12 <= len(question) <= 300 or not question.endswith("?") or question.count("?") != 1:
        return False
    # A clarification does not need generated figures, identifiers, links or claims.
    if re.search(r"[\d\n\r.!;:\[\]{}<>@%€$£]", question):
        return False
    if not re.match(r"^(?:what|which|when|where|how|is|are|do|does|did|has|have|can|could|would|vad|vilken|vilket|vilka|när|var|vart|hur|varför|är|har|finns|kan|kunde|skulle|vill|vet|behöver|(?:i|på|för|från|om|med|under)\s+(?:vilken|vilket|vilka))\b", question, re.I):
        return False
    if re.search(r"\b(?:guarantee\w*|payback|definitely|obviously|caused by|you should|you must|you need to|will save|will reduce|i found|i checked|i sent|i created|garantera\w*|garanti\w*|återbetalningstid\w*|definitivt|uppenbarligen|orsakas av|du bör|ni bör|du måste|ni måste|kommer att spara|kommer att minska|jag hittade|jag kontrollerade|jag skickade|jag skapade)\b", question, re.I):
        return False
    # Do not introduce named entities in a question; canonical choices are rendered separately.
    names = re.findall(r"\b[A-ZÅÄÖ][\wÅÄÖåäö-]*\b", question)
    if any(name not in {"BRF", "ODEN", "OVK", "EPC"} for name in names[1:]):
        return False
    focus = data["focus"]
    if focus == "building_identity" and not re.search(r"\b(?:address|building|brf|adress\w*|byggnad\w*|brf-namn\w*|bostadsrättsförening\w*)\b", question, re.I):
        return False
    if focus == "building_location" and not re.search(r"\b(?:city|municipality|postcode|postal|location|building id|ort\w*|stad\w*|kommun\w*|postnummer\w*|postort\w*|byggnadsid)\b", question, re.I):
        return False
    if focus == "building_selection" and not re.search(r"\b(?:which|select|choose|vilken|vilket|vilka|välj\w*)\b", question, re.I):
        return False
    if focus in OPEN_FOCI and re.search(r"\b(?:street address|building id|brf name|email|phone number|your name|gatuadress\w*|adress\w*|byggnadsid|brf-namn\w*|föreningens namn|mejladress\w*|e-post\w*|telefonnummer\w*|ditt namn|ert namn)\b", question, re.I):
        return False
    normalized = re.sub(r"\W+", "", question).casefold()
    for message in context["history"]:
        if message["role"] == "assistant" and normalized in re.sub(r"\W+", "", message["content"]).casefold():
            return False
    return True


class ClarificationAgent:
    def __init__(self, *, client=None, deployment=None):
        self.client = client
        self.deployment = deployment if deployment is not None else os.getenv("CLARIFICATION_MODEL_DEPLOYMENT_NAME", "").strip()

    def generate(self, *, state, reason, fallback):
        if reason == "incomplete_question" and fallback == "Could you clarify your request a bit more so I can answer safely?":
            fallback = "What would you like help understanding or deciding?"
        result = {"question": fallback, "status": "fallback"}
        if not self.deployment:
            return {**result, "status": "disabled"}
        if reason in IDENTITY_FOCUS:
            foci = [IDENTITY_FOCUS[reason]]
        elif reason in {"incomplete_question", "missing_building_data"}:
            foci = OPEN_FOCI
        else:
            return result
        context = _context(state, reason, foci)
        # The original lookup outcome / choice list is protected, not rewritten by the model.
        protected = fallback if reason in {
            "ambiguous_brf", "brf_not_found", "brf_lookup_failed",
            "building_not_found_by_id", "building_not_found",
        } else ""
        context["protected_message"] = protected
        started, response, output, error = time.perf_counter(), None, None, None
        try:
            if self.client is None:
                from openai import OpenAI
                self.client = OpenAI(
                    api_key=os.environ["OPENAI_API_KEY"],
                    base_url=azure_v1_base_url(os.environ["AZURE_ENDPOINT"]),
                    timeout=15.0, max_retries=0,
                )
            messages = [
                {"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ]
            response = self.client.chat.completions.create(
                model=self.deployment, messages=messages,
                response_format={"type": "json_schema", "json_schema": {
                    "name": "clarification_question", "strict": True,
                    "schema": {"type": "object", "additionalProperties": False,
                        "properties": {"focus": {"type": "string", "enum": foci}, "question": {"type": "string"}},
                        "required": ["focus", "question"]},
                }},
                **completion_options(self.deployment, reasoning_tokens=2048),
            )
            choice = response.choices[0]
            if choice.finish_reason != "stop" or getattr(choice.message, "refusal", None):
                raise ValueError("incomplete_or_refused")
            output = choice.message.content
            data = json.loads(output)
            if not _valid_question(data, foci, context):
                raise ValueError("invalid_question")
            question = data["question"].strip()
            # Choice menus and lookup results must remain exact. A generated question
            # can help the user respond, but cannot replace those facts or options.
            if protected:
                lines = protected.splitlines()
                # Existing builders put the lookup outcome before the request
                # on the first line, and canonical choice rows on later lines.
                prefix = lines[0].rsplit(". ", 1)[0] + ". " if ". " in lines[0] else ""
                question = prefix + question
                if len(lines) > 1:
                    question += "\n" + "\n".join(lines[1:])
            return {"question": question, "status": "generated", "focus": data["focus"]}
        except Exception as exc:
            error = type(exc).__name__
            return result
        finally:
            record_model_call(component="clarification_agent", model=self.deployment,
                              response=response, output_text=output,
                              latency_seconds=time.perf_counter() - started,
                              success=error is None, error=error)


_agent = None


def generate_clarification(*, state, reason, fallback):
    global _agent
    if _agent is None:
        _agent = ClarificationAgent()
    return _agent.generate(state=state, reason=reason, fallback=fallback)
