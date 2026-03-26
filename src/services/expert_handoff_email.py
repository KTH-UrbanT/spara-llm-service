import json
import os
import smtplib
from copy import deepcopy
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional


SMTP_SERVER = os.getenv("EXPERT_EMAIL_SMTP_SERVER", "smtp.kth.se")
SMTP_PORT = int(os.getenv("EXPERT_EMAIL_SMTP_PORT", "587"))
EMAIL_ACCOUNT = os.getenv("EXPERT_EMAIL_ACCOUNT", "abe-spara-bot")
EMAIL_ADDRESS = os.getenv("EXPERT_EMAIL_ADDRESS", "spara-bot@kth.se")
EMAIL_PASSWORD = os.getenv("EXPERT_EMAIL_PASSWORD", "jgEAjC8#ts2sCax1ujZCn")
EMAIL_RECIPIENT = os.getenv("EXPERT_EMAIL_RECIPIENT", "shadaab@kth.se")

SUMMARY_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "expert_handoff_summary_prompt.txt"
)


def _sanitize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sanitized_messages: List[Dict[str, Any]] = []
    for message in messages or []:
        sanitized_message = deepcopy(message)
        sanitized_message.pop("added_to_database", None)
        sanitized_message.pop("timestamp", None)
        sanitized_messages.append(sanitized_message)
    return sanitized_messages


def _sanitize_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    return deepcopy(metadata or {})


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
        if isinstance(summary, str) and summary.strip() and not summary.startswith("Error:"):
            return summary.strip()
    except Exception:
        pass

    return (
        "Reason for handoff:\n"
        f"- The user explicitly requested expert support or email escalation to EKR.\n\n"
        "What the expert should know:\n"
        f"- Latest user handoff request: {handoff_reason}\n"
        f"- Session ID: {thread_id}\n\n"
        "Relevant metadata:\n"
        f"{building_context}\n"
    )


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
) -> bool:
    message = build_expert_handoff_email(thread_id, messages, metadata)

    smtp_session = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
    try:
        smtp_session.ehlo()
        smtp_session.starttls()
        smtp_session.ehlo()
        smtp_session.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        smtp_session.sendmail(EMAIL_ADDRESS, [EMAIL_RECIPIENT], message.as_string())
        return True
    finally:
        smtp_session.quit()
