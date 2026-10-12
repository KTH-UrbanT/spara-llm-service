import json
import hashlib
import math
import os
import re
import smtplib
import time
import uuid
from copy import deepcopy
from email.utils import parseaddr
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
import redis
from src.agents.building_response_prompt import select_preferred_identifier

SERVICE_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(SERVICE_ROOT / ".env", override=False)

SMTP_SERVER = os.getenv("EXPERT_EMAIL_SMTP_SERVER")
SMTP_PORT = int(os.getenv("EXPERT_EMAIL_SMTP_PORT", "587"))
EMAIL_ACCOUNT = os.getenv("EXPERT_EMAIL_ACCOUNT")
EMAIL_ADDRESS = os.getenv("EXPERT_EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EXPERT_EMAIL_PASSWORD")
EMAIL_RECIPIENT = os.getenv("EXPERT_EMAIL_RECIPIENT")

# A preview is the unit of consent and deduplication, including across workers.
_handoff_store = redis.StrictRedis(
    host=os.getenv("REDIS_HOST", "127.0.0.1"),
    port=int(os.getenv("REDIS_PORT", "6379")),
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5,
)
HANDOFF_TTL_SECONDS = 7 * 24 * 60 * 60

SUMMARY_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "expert_handoff_summary_prompt.txt"
)

HANDOFF_SHARING_SCOPES = {"summary_only", "full_conversation"}
# These fields can inform the summary without being copied into the transcript.
# Bind fields present at preview time; later transport-derived mirrors are not
# new input to an already approved summary.
_SUMMARY_SOURCE_FIELDS = {
    "retrieved_facts", "building_identity_check", "address_identity_evidence",
    "brf_resolution", "brf_candidate_buildings", "requested_address",
    "epc_record_address", "same_building_multiple_addresses",
}

# Sharing, approval and submission are rendered from application state outside
# the case summary. They must not be inferred from old assistant replies or
# restated by the summarizer, where they can contradict the current preview.
_HANDOFF_CONTROL_TEXT = re.compile(
    r"\b(?:förhandsvis\w*|preview\w*|skicka(?:r|s|t|ts|de|des)?|"
    r"sänd(?:a|er|s|e|es|t|ts)|mottagare\w*|godkän(?:na|ner|nande\w*)|"
    r"samtyck\w*|delning(?:en)?|delnings\w*|"
    r"avbryt\w*|avbrut\w*|makuler\w*|"
    r"send|sends|sending|sent|submitted|submission\w*|recipient\w*|approval\w*|consent\w*|permission\w*|"
    r"cancel\w*|smtp|e-post\w*|email\w*|mejl\w*|cc)\b|"
    r"\b(?:hela\s+(?:chatt\w*|samtal\w*|konversation\w*)|"
    r"(?:full|entire|whole)\s+(?:chat|conversation|transcript)|"
    r"summary[-_ ]only|(?:bara|endast)\s+(?:den\s+)?(?:godkända\s+)?sammanfattning\w*|"
    r"only\s+(?:the\s+)?(?:approved\s+)?summary|"
    r"samtalsbilag\w*|konversationsbilag\w*|conversation\s+attachment|bifogas)\b|"
    r"(?:^|\s)(?:bilaga|attachment)\s*:|"
    r"\b(?:dela|delas|delat|delats|delade|delades|share|shared|sharing)\b"
    r"[^.!?\n]{0,60}\b(?:sammanfattning\w*|samtal\w*|konversation\w*|summary|conversation|transcript)\b|"
    r"\b(?:sammanfattning\w*|samtal\w*|konversation\w*|summary|conversation|transcript)\b"
    r"[^.!?\n]{0,60}\b(?:dela|delas|delat|delats|delade|delades|share|shared|sharing)\b|"
    r"\b(?:användaren|du|ni|jag|user|you|we|request|handoff)\b[^.!?\n]{0,40}"
    r"\b(?:godkänt|godkände|approved|authorized|permitted)\b|"
    r"\b(?:approved\s+(?:summary|preview|handoff|sharing)|"
    r"godkänd(?:a)?\s+(?:sammanfattning\w*|förhandsvisning\w*|överlämning\w*|delning\w*))\b|"
    r"\battachment\b\s+(?:will|would|shall)\b|"
    r"\b(?:attachment|bilaga)\b[^.!?\n]{0,60}"
    r"\b(?:this\s+(?:message|email|handoff)|meddelandet|mejlet|överlämningen)\b|"
    r"^\s*(?:approved|godkänt)\s*[:.!]?$|"
    r"\b(?:rapport\w*|report\w*)\b[^.!?\n]{0,60}\b(?:skap\w*|created|generated)\b",
    re.IGNORECASE,
)


def _case_summary_text(text: Any) -> str:
    """Keep case facts while removing sentences that claim application actions."""
    kept = []
    for line in str(text or "").splitlines():
        # Corrections commonly follow a scope request in the same sentence.
        line = re.sub(
            r"\s+(?:och|and|men|but)\s+(?=(?:rättelse|correction|budget(?:en)?|"
            r"(?:vår|our|the)\s+budget)\b)", ". ", line, flags=re.IGNORECASE,
        )
        fragments = re.split(r"(?<=[.!?])\s+|(?<=;)\s*", line)
        retained = [fragment for fragment in fragments if fragment.strip()
                    and not _HANDOFF_CONTROL_TEXT.search(fragment)]
        if retained:
            kept.append(" ".join(retained))
    return "\n".join(kept).strip()


def _case_summary_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    projected = []
    for message in messages or []:
        if message.get("role") not in {"user", "assistant"}:
            continue
        # These assistant turns describe operations, not independent case
        # evidence. Ordinary technical assistant explanations remain useful.
        if message.get("role") == "assistant":
            containers = [message]
            if isinstance(message.get("metadata"), dict):
                containers.append(message["metadata"])
            if any(container.get("route") == "action_status"
                   or any(container.get(key) == "expert_handoff" for key in ("classification", "agent_answered"))
                   for container in containers):
                continue
        content = _case_summary_text(message.get("content"))
        if content and not re.fullmatch(r"(?:ja|nej|yes|no|ok|okej)[.!\s]*", content, re.IGNORECASE):
            projected.append({"role": message["role"], "content": content})
    return projected


def _has_case_summary_content(summary: str) -> bool:
    headings = {
        "anledning till överlämning", "användarens huvudsakliga behov",
        "relevant information om byggnaden eller ärendet", "vad spara redan har bidragit med",
        "vad rådgivaren behöver hjälpa till med", "reason for handoff", "user's main need",
        "relevant building or case details", "what the chatbot already provided",
        "why human follow-up is needed",
    }
    return any(line.strip(" \t*#:-").casefold() not in headings
               for line in summary.splitlines() if line.strip())


def _handoff_sharing_scope(metadata: Dict[str, Any]) -> Optional[str]:
    scope = (metadata or {}).get("expert_handoff_scope", "full_conversation")
    return scope if isinstance(scope, str) and scope in HANDOFF_SHARING_SCOPES else None


def _sanitize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sanitized_messages: List[Dict[str, Any]] = []
    for message in messages or []:
        # Persistence adds IDs, versions and transport diagnostics between turns.
        # Include stable conversational fields so that consent survives storage.
        sanitized_message = {
            key: deepcopy(message[key])
            for key in ("role", "content", "classification", "agent_answered", "parsed_intent")
            if key in message
        }
        sanitized_messages.append(sanitized_message)
    return sanitized_messages


def _sanitize_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    metadata = metadata or {}
    # Message-service rehydrates generated route/evaluation fields into the
    # session hash. Only stable source context belongs in the disclosed email.
    context_keys = {
        "address", "address_from_user", "address_location_hint", "brf_name",
        "selected_brf_building_id", "selected_brf_addresses", "user_email",
        "building_information", "simulation_results", "aggregated_data", "agent_data",
        "advisory_context", "user_goals", "constraints",
    }
    sanitized = deepcopy({
        key: value for key, value in metadata.items()
        if key in context_keys and value not in (None, "", [], {})
    })
    direct_ids = {key: metadata[key] for key in (
        "byggnadsid", "building_id", "50a_uuid", "uuid", "oden_uuid", "01a_fnr",
    ) if metadata.get(key)}
    building_id = (metadata.get("selected_brf_building_id")
                   or select_preferred_identifier(direct_ids)
                   or select_preferred_identifier(sanitized))
    if building_id:
        sanitized["building_id"] = str(building_id)
    return sanitized


def _normalize_email(value: Any) -> Optional[str]:
    _, parsed_email = parseaddr(str(value or "").strip())
    if not parsed_email or "@" not in parsed_email:
        return None
    return parsed_email.lower()


def _extract_user_email(thread_id: str, metadata: Dict[str, Any]) -> Optional[str]:
    metadata_email = _normalize_email((metadata or {}).get("user_email"))
    if metadata_email:
        return metadata_email

    if ":" in str(thread_id or ""):
        thread_email = str(thread_id).split(":", 1)[0]
        return _normalize_email(thread_email)

    return None


def expert_handoff_configuration(thread_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    recipient = _normalize_email(EMAIL_RECIPIENT)
    explicitly_disabled = str(os.getenv("EXPERT_EMAIL_ENABLED", "true")).lower().strip() in {
        "0", "false", "no", "off",
    }
    return {
        "enabled": not explicitly_disabled and bool(
            SMTP_SERVER and EMAIL_ACCOUNT and EMAIL_PASSWORD
            and _normalize_email(EMAIL_ADDRESS) and recipient
        ),
        "recipient": recipient,
        "cc": _extract_user_email(thread_id, metadata),
    }


def handoff_response_language(messages: List[Dict[str, Any]], metadata: Dict[str, Any]) -> str:
    """Keep short consent replies from changing the conversation language."""
    from src.pipeline.safety_analysis import response_language
    return response_language(messages, metadata=metadata)


def copyable_expert_handoff_summary(messages: List[Dict[str, Any]], metadata: Dict[str, Any]) -> str:
    """A deterministic fallback remains usable when the LLM/SMTP service is down."""
    requests = [
        str(message.get("content") or "").strip()
        for message in _case_summary_messages(messages) if message.get("role") == "user"
    ]
    swedish = handoff_response_language(messages, metadata) == "sv"
    context_metadata = _sanitize_metadata(metadata)
    labels = (
        ("building_id", "Byggnads-ID" if swedish else "Building ID"),
        ("address", "Adress" if swedish else "Address"),
        ("brf_name", "Förening" if swedish else "Association"),
    )
    context = "\n".join(f"{label}: {context_metadata[key]}" for key, label in labels if context_metadata.get(key))
    context = context or ("Ingen byggnadsidentitet är angiven i sammanfattningen." if swedish else "No building identity is provided in this summary.")
    if swedish:
        summary = (
            "Sammanfattning att kopiera till en energi- och klimatrådgivare:\n"
            "Användarens frågor och önskemål (inte oberoende verifierade):\n"
            + "\n".join(f"- {request}" for request in requests if request)
            + f"\n\nTillgänglig information om byggnaden eller ärendet:\n{context}\n\n"
            "Önskat nästa steg: gå igenom frågorna, underlaget, saknade uppgifter "
            "och lämpliga prioriteringar tillsammans med föreningen."
        )
    else:
        summary = (
            "Summary to copy for an energy and climate advisor:\n"
            "User-reported questions and concerns (not independently verified):\n"
            + "\n".join(f"- {request}" for request in requests if request)
            + f"\n\nAvailable building/session context:\n{context}\n\n"
            "Requested next step: review the questions, evidence, missing information, "
            "and appropriate priorities with the association."
        )
    from src.services.source_link_registry import guard_response_links
    return guard_response_links(summary, "sv" if swedish else "en")


def _preview_digest(
    thread_id: str, messages: List[Dict[str, Any]], metadata: Dict[str, Any],
    preview: Dict[str, Any],
) -> str:
    consent_payload = {
        "thread_id": thread_id,
        "source": _build_transcript_payload(messages, metadata),
        "summary_source_context": {
            key: metadata.get(key) for key in preview["source_context_keys"]
        },
        "approved_preview": {
            key: preview.get(key) for key in (
                "enabled", "recipient", "cc", "language", "sharing_scope",
                "request_id", "expires_at", "message_count", "source_context_keys", "summary",
            )
        },
    }
    payload = json.dumps(consent_payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def prepare_expert_handoff(thread_id: str, messages: List[Dict[str, Any]], metadata: Dict[str, Any]) -> Dict[str, Any]:
    configuration = expert_handoff_configuration(thread_id, metadata)
    sharing_scope = _handoff_sharing_scope(metadata)
    if sharing_scope is None:
        raise ValueError("Invalid expert handoff sharing scope")
    old_preview = metadata.get("expert_handoff_preview") or {}
    # A pure sharing-scope change can reuse exactly the previously shown summary.
    # The router must pass the original source slice, not later advisory turns.
    can_reuse = (
        isinstance(old_preview, dict)
        and old_preview.get("message_count") == len(messages or [])
        and _preview_matches_source(thread_id, messages, metadata, require_current_scope=False)
        and not _HANDOFF_CONTROL_TEXT.search(str(old_preview.get("summary") or ""))
    )
    summary = old_preview["summary"] if can_reuse else (
        generate_expert_handoff_summary(thread_id, messages, metadata)
        if configuration["enabled"] else copyable_expert_handoff_summary(messages, metadata)
    )
    source_context_keys = (
        old_preview["source_context_keys"] if can_reuse else
        sorted(key for key in _SUMMARY_SOURCE_FIELDS if key in metadata)
    )
    preview = {
        **configuration,
        "language": handoff_response_language(messages, metadata),
        "sharing_scope": sharing_scope,
        "request_id": str(uuid.uuid4()),
        "expires_at": time.time() + 1800,
        "message_count": len(messages or []),
        "source_context_keys": source_context_keys,
        "summary": summary,
    }
    preview["payload_digest"] = _preview_digest(thread_id, messages, metadata, preview)
    return preview


def expert_handoff_preview_matches(thread_id: str, messages: List[Dict[str, Any]], metadata: Dict[str, Any]) -> bool:
    return _preview_matches_source(thread_id, messages, metadata, require_current_scope=True)


def _preview_matches_source(
    thread_id: str, messages: List[Dict[str, Any]], metadata: Dict[str, Any],
    *, require_current_scope: bool,
) -> bool:
    preview = metadata.get("expert_handoff_preview") or {}
    if not isinstance(preview, dict):
        return False
    configuration = expert_handoff_configuration(thread_id, metadata)
    count = preview.get("message_count")
    sharing_scope = preview.get("sharing_scope")
    summary = preview.get("summary")
    source_context_keys = preview.get("source_context_keys")
    if (type(count) is not int or count < 0 or count > len(messages or [])
            or not isinstance(sharing_scope, str) or sharing_scope not in HANDOFF_SHARING_SCOPES
            or not isinstance(summary, str) or not summary.strip()
            or not isinstance(source_context_keys, list)
            or any(not isinstance(key, str) or key not in _SUMMARY_SOURCE_FIELDS for key in source_context_keys)
            or source_context_keys != sorted(set(source_context_keys))
            or (require_current_scope and sharing_scope != _handoff_sharing_scope(metadata))):
        return False
    expires_at = preview.get("expires_at")
    return bool(
        isinstance(preview.get("request_id"), str) and preview["request_id"]
        and preview.get("enabled") is True and configuration["enabled"]
        and type(expires_at) in (int, float) and math.isfinite(expires_at)
        and expires_at > time.time()
        and preview.get("recipient") == configuration["recipient"]
        and preview.get("cc") == configuration["cc"]
        and preview.get("payload_digest") == _preview_digest(thread_id, (messages or [])[:count], metadata, preview)
    )


def _build_transcript_payload(
    messages: List[Dict[str, Any]],
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"conversation": _sanitize_messages(messages)}
    sanitized_metadata = _sanitize_metadata(metadata)
    if sanitized_metadata:
        payload["metadata"] = sanitized_metadata
    return payload


def build_expert_handoff_transcript(
    thread_id: str,
    messages: List[Dict[str, Any]],
    metadata: Dict[str, Any],
) -> str:
    transcript_payload = _build_transcript_payload(messages, metadata)
    return (
        f"SPARA expert handoff transcript\n"
        f"Session ID: {thread_id}\n\n"
        f"{json.dumps(transcript_payload, indent=2, ensure_ascii=False)}\n"
    )


def _extract_building_context(metadata: Dict[str, Any]) -> str:
    from src.agents.building_response_prompt import build_identity_evidence_context
    sanitized_metadata = _sanitize_metadata(metadata)
    preferred_keys = [
        "building_id",
        "byggnadsid",
        "address",
        "address_from_user",
        "building_information",
        "simulation_results",
    ]
    extracted = {
        key: sanitized_metadata[key]
        for key in preferred_keys
        if key in sanitized_metadata and sanitized_metadata[key]
    }
    selected = sanitized_metadata.get("building_id")
    extracted.update(build_identity_evidence_context(metadata, selected))
    facts = metadata.get("retrieved_facts") or {}
    if isinstance(facts, dict) and selected and str(facts.get("byggnadsid") or facts.get("building_id") or "") == str(selected):
        extracted["recorded_facts"] = facts
    if not extracted:
        return "No building metadata was stored for this handoff."
    return json.dumps(extracted, indent=2, ensure_ascii=False)


def _extract_handoff_reason(messages: List[Dict[str, Any]]) -> str:
    for message in reversed(messages or []):
        if message.get("role") == "user":
            content = str(message.get("content", "")).strip()
            if content:
                return content
    return "The user asked for expert support."


def generate_expert_handoff_summary(
    thread_id: str,
    messages: List[Dict[str, Any]],
    metadata: Dict[str, Any],
) -> str:
    case_messages = _case_summary_messages(messages)
    context_metadata = _sanitize_metadata(metadata)
    context_metadata.pop("user_email", None)
    case_context = json.dumps(
        {"conversation": case_messages, "metadata": context_metadata},
        ensure_ascii=False, indent=2,
    )
    language = handoff_response_language(messages, metadata)
    handoff_reason = _extract_handoff_reason(case_messages)
    building_context = _extract_building_context(metadata)

    user_prompt = (
        "Current application-controlled preview (authoritative; do not restate in the case summary):\n"
        f"{json.dumps({'sharing_scope': _handoff_sharing_scope(metadata), 'phase': 'proposed_preview'}, ensure_ascii=False)}\n\n"
        f"Handoff trigger: {handoff_reason}\n\n"
        f"Relevant building/session metadata:\n{building_context}\n\n"
        "Case-only conversation and metadata (operational previews/status omitted):\n"
        f"{case_context}"
    )

    try:
        from src.agents.openai_agent import OpenAIResponseAgent

        summarizer = OpenAIResponseAgent(prompt_path=str(SUMMARY_PROMPT_PATH))
        summary = summarizer.generate_response(user_prompt, message_list=[], language=language)
        normalized_summary = summary.strip() if isinstance(summary, str) else ""
        if (normalized_summary and not normalized_summary.lower().startswith("error:")
                and normalized_summary.lower().rstrip(".") not in {
                    "no content returned", "no content returned from the model",
                }):
            from src.services.source_link_registry import guard_response_links
            case_summary = _case_summary_text(normalized_summary)
            if case_summary and _has_case_summary_content(case_summary):
                return guard_response_links(case_summary, language)
    except Exception:
        pass

    return copyable_expert_handoff_summary(messages, metadata)


def build_expert_handoff_email(
    thread_id: str,
    messages: List[Dict[str, Any]],
    metadata: Dict[str, Any],
    summary_override: Optional[str] = None,
    sharing_scope: Optional[str] = None,
) -> MIMEMultipart:
    preview = metadata.get("expert_handoff_preview") or {}
    scope = sharing_scope or preview.get("sharing_scope") or _handoff_sharing_scope(metadata)
    if not isinstance(scope, str) or scope not in HANDOFF_SHARING_SCOPES:
        raise ValueError("Invalid expert handoff sharing scope")
    summary_text = summary_override if summary_override is not None else generate_expert_handoff_summary(
        thread_id, messages, metadata
    )
    swedish = (preview.get("language") or handoff_response_language(messages, metadata)) == "sv"
    subject = "SPARA – överlämning till energi- och klimatrådgivare" if swedish else "SPARA expert handoff"
    if scope == "full_conversation":
        subject += f" ({thread_id})" if swedish else f" for session {thread_id}"
    message = MIMEMultipart()
    message["From"] = EMAIL_ADDRESS
    message["To"] = preview.get("recipient") or EMAIL_RECIPIENT
    user_email = preview.get("cc") if preview else _extract_user_email(thread_id, metadata)
    if user_email:
        message["Cc"] = user_email
    message["Subject"] = subject

    body = (
        "Hej,\n\n"
        "Användaren har godkänt att SPARA lämnar över detta ärende till en energi- och klimatrådgivare.\n\n"
        f"{summary_text}\n\n"
    ) if swedish else (
        "Hello,\n\n"
        "SPARA has escalated this conversation to an EKR expert.\n\n"
        f"{summary_text}\n\n"
    )
    if scope == "full_conversation":
        body += (
            "Hela samtalet fram till förhandsvisningen finns i den bifogade textfilen.\n"
            if swedish else "The full conversation transcript is attached as a text file.\n"
        )
    else:
        body += (
            "Endast den godkända sammanfattningen delas; ingen samtalsbilaga skickas.\n"
            if swedish else "Only the approved summary is shared; no conversation attachment is sent.\n"
        )
    message.attach(MIMEText(body, "plain", "utf-8"))
    if scope == "full_conversation":
        transcript_text = build_expert_handoff_transcript(thread_id, messages, metadata)
        attachment = MIMEText(transcript_text, "plain", "utf-8")
        attachment.add_header(
            "Content-Disposition",
            "attachment",
            filename=f"spara_handoff_{thread_id}.txt",
        )
        message.attach(attachment)
    return message


def send_expert_handoff_email(
    thread_id: str,
    messages: List[Dict[str, Any]],
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    if not metadata.get("expert_handoff_confirmed") or not expert_handoff_preview_matches(thread_id, messages, metadata):
        return {"status": "failed", "error": "confirmation_required"}

    preview = metadata["expert_handoff_preview"]
    # Generate the exact previewed content, excluding subsequent confirmation turns.
    message = build_expert_handoff_email(
        thread_id, messages[:preview["message_count"]], metadata,
        summary_override=preview["summary"],
        sharing_scope=preview["sharing_scope"],
    )
    recipients = [preview["recipient"]]
    user_email = preview.get("cc")
    if user_email and user_email not in recipients:
        recipients.append(user_email)

    digest = hashlib.sha256(f"{thread_id}:{preview['request_id']}".encode("utf-8")).hexdigest()
    key = f"expert_handoff_submission:{digest}"
    try:
        claimed = _handoff_store.set(key, json.dumps({"status": "sending"}), nx=True, ex=HANDOFF_TTL_SECONDS)
        if not claimed:
            stored = json.loads(_handoff_store.get(key) or '{}')
            return {**stored, "status": stored.get("status", "unknown"), "duplicate": True}
    except Exception:
        # Sending without deduplication would turn a retried queue job into another email.
        return {"status": "failed", "error": "submission_store_unavailable"}

    smtp_session = None
    submission_started = False
    try:
        smtp_session = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=20)
        smtp_session.ehlo()
        smtp_session.starttls()
        smtp_session.ehlo()
        smtp_session.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        submission_started = True
        refused = smtp_session.sendmail(EMAIL_ADDRESS, recipients, message.as_string()) or {}
        result = {
            "status": "failed" if preview["recipient"] in refused else "submitted",
            "cc_submitted": bool(user_email and user_email not in refused),
        }
    except smtplib.SMTPRecipientsRefused:
        result = {"status": "failed", "error": "recipients_refused", "cc_submitted": False}
    except Exception as exc:
        result = {"status": "unknown" if submission_started else "failed", "error": type(exc).__name__}
    finally:
        if smtp_session is not None:
            try:
                smtp_session.quit()
            except Exception:
                # A disconnect after DATA acceptance does not undo submission.
                try:
                    smtp_session.close()
                except Exception:
                    pass
    try:
        _handoff_store.setex(key, HANDOFF_TTL_SECONDS, json.dumps(result))
    except Exception:
        # The original claim remains; retries must not send again.
        pass
    return result
