import json
import hashlib
import os
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


def copyable_expert_handoff_summary(messages: List[Dict[str, Any]], metadata: Dict[str, Any]) -> str:
    """A deterministic fallback remains usable when the LLM/SMTP service is down."""
    requests = [
        str(message.get("content") or "").strip()
        for message in messages or [] if message.get("role") == "user"
    ]
    context = _extract_building_context(metadata)
    return (
        "Summary to copy for an energy and climate advisor:\n"
        "User-reported questions and concerns (not independently verified):\n"
        + "\n".join(f"- {request}" for request in requests if request)
        + f"\n\nAvailable building/session context:\n{context}\n\n"
        "Requested next step: review the questions, evidence, missing information, "
        "and appropriate priorities with the association."
    )


def _preview_digest(messages: List[Dict[str, Any]], metadata: Dict[str, Any]) -> str:
    payload = json.dumps(_build_transcript_payload(messages, metadata), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def prepare_expert_handoff(thread_id: str, messages: List[Dict[str, Any]], metadata: Dict[str, Any]) -> Dict[str, Any]:
    configuration = expert_handoff_configuration(thread_id, metadata)
    return {
        **configuration,
        "request_id": str(uuid.uuid4()),
        "expires_at": time.time() + 1800,
        "message_count": len(messages or []),
        "payload_digest": _preview_digest(messages, metadata),
        "summary": generate_expert_handoff_summary(thread_id, messages, metadata)
        if configuration["enabled"] else copyable_expert_handoff_summary(messages, metadata),
    }


def expert_handoff_preview_matches(thread_id: str, messages: List[Dict[str, Any]], metadata: Dict[str, Any]) -> bool:
    preview = metadata.get("expert_handoff_preview") or {}
    configuration = expert_handoff_configuration(thread_id, metadata)
    count = preview.get("message_count")
    if not isinstance(count, int) or count < 0 or count > len(messages or []):
        return False
    return bool(
        preview.get("request_id") and configuration["enabled"]
        and isinstance(preview.get("expires_at"), (int, float))
        and preview["expires_at"] > time.time()
        and preview.get("recipient") == configuration["recipient"]
        and preview.get("cc") == configuration["cc"]
        and preview.get("payload_digest") == _preview_digest((messages or [])[:count], metadata)
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
    transcript = build_expert_handoff_transcript(thread_id, messages, metadata)
    handoff_reason = _extract_handoff_reason(messages)
    building_context = _extract_building_context(metadata)

    user_prompt = (
        f"Session ID: {thread_id}\n"
        f"Handoff trigger: {handoff_reason}\n\n"
        f"Relevant building/session metadata:\n{building_context}\n\n"
        f"Full sanitized transcript:\n{transcript}"
    )

    try:
        from src.agents.openai_agent import OpenAIResponseAgent

        summarizer = OpenAIResponseAgent(prompt_path=str(SUMMARY_PROMPT_PATH))
        summary = summarizer.generate_response(user_prompt, message_list=[])
        normalized_summary = summary.strip() if isinstance(summary, str) else ""
        if (normalized_summary and not normalized_summary.lower().startswith("error:")
                and normalized_summary.lower().rstrip(".") not in {
                    "no content returned", "no content returned from the model",
                }):
            return normalized_summary
    except Exception:
        pass

    return copyable_expert_handoff_summary(messages, metadata)


def build_expert_handoff_email(
    thread_id: str,
    messages: List[Dict[str, Any]],
    metadata: Dict[str, Any],
    summary_override: Optional[str] = None,
) -> MIMEMultipart:
    summary_text = summary_override or generate_expert_handoff_summary(
        thread_id, messages, metadata
    )
    transcript_text = build_expert_handoff_transcript(thread_id, messages, metadata)

    subject = f"SPARA expert handoff for session {thread_id}"
    message = MIMEMultipart()
    message["From"] = EMAIL_ADDRESS
    message["To"] = EMAIL_RECIPIENT
    user_email = _extract_user_email(thread_id, metadata)
    if user_email:
        message["Cc"] = user_email
    message["Subject"] = subject

    body = (
        "Hello,\n\n"
        "SPARA has escalated this conversation to an EKR expert.\n\n"
        f"{summary_text}\n\n"
        "The full conversation transcript is attached as a text file.\n"
    )
    message.attach(MIMEText(body, "plain", "utf-8"))

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
