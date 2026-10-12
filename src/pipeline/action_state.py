"""Server-owned action records, independent of generated conversation text."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re


HANDOFF_STATUSES = {"pending_confirmation", "changes_requested", "disabled", "cancelled", "superseded", "simulated", "submitted", "failed", "unknown"}
ARTIFACT_FIELDS = {"report_id", "file_name", "mime_type", "download_url", "expires_at", "created_at", "building_id"}


def _clean_receipts(value):
    result = {"reports": [], "handoffs": []}
    if not isinstance(value, dict):
        return result
    for record in value.get("reports", []) if isinstance(value.get("reports"), list) else []:
        if not isinstance(record, dict) or record.get("status") not in {"generated", "fallback"}:
            continue
        artifact = record.get("artifact")
        if not isinstance(artifact, dict) or not isinstance(artifact.get("report_id"), str) or not artifact["report_id"]:
            continue
        result["reports"].append({
            "report_id": artifact["report_id"], "status": record["status"],
            "building_id": record.get("building_id"),
            "artifact": {key: deepcopy(artifact[key]) for key in ARTIFACT_FIELDS if key in artifact},
        })
    for record in value.get("handoffs", []) if isinstance(value.get("handoffs"), list) else []:
        if not isinstance(record, dict) or record.get("status") not in HANDOFF_STATUSES:
            continue
        if not isinstance(record.get("request_id"), str) or not record["request_id"]:
            continue
        result["handoffs"].append({key: deepcopy(record.get(key)) for key in (
            "request_id", "status", "recipient", "cc", "cc_submitted", "sharing_scope", "summary", "building_id",
            "legacy_status_only", "source_message_id", "provenance",
        )})
    return result


def _merge_receipts(target, incoming):
    for collection, identity in (("reports", "report_id"), ("handoffs", "request_id")):
        for record in _clean_receipts(incoming)[collection]:
            for index, existing in enumerate(target[collection]):
                if existing[identity] == record[identity]:
                    # A new pending/cancelled preview cannot erase a completed
                    # SMTP attempt for the same request identifier.
                    if collection == "handoffs":
                        if existing["status"] == "submitted" and record["status"] != "submitted":
                            break
                        if existing["status"] in {"simulated", "unknown", "failed"} and record["status"] in {"pending_confirmation", "cancelled", "changes_requested", "superseded", "disabled"}:
                            break
                    for key in ("source_message_id", "provenance"):
                        if record.get(key) is None and existing.get(key) is not None:
                            record[key] = existing[key]
                    target[collection][index] = record
                    break
            else:
                target[collection].append(record)
        if collection == "handoffs":
            # Preview churn must not erase actual submission history/counts.
            target[collection] = [record for index, record in enumerate(target[collection]) if
                                  record["status"] in {"submitted", "simulated", "unknown", "failed"}
                                  or index >= len(target[collection]) - 32]
        else:
            target[collection] = target[collection][-32:]
    # Only the latest request can still await approval. Canonical snapshots from
    # older messages may otherwise resurrect a replaced preview as pending.
    for record in target["handoffs"][:-1]:
        if record["status"] in {"pending_confirmation", "changes_requested"}:
            record["status"] = "superseded"


def _building_id(metadata):
    for key in ("building_id_from_user", "selected_address_building_id", "selected_brf_building_id", "byggnadsid", "building_id"):
        value = metadata.get(key)
        if value not in (None, "", [], {}):
            return value[0] if isinstance(value, list) else value
    identity = metadata.get("building_identity_check") or {}
    return identity.get("matched_building_id") if identity.get("status") == "passed" else None


def _handoff_execution_provenance(response, metadata):
    """False for known status/carry-forward responses; None for old unknowns."""
    # Historical RouterAgent status replies inherited route=expert_handoff and
    # sent=True from the preceding submission. The answering agent is stronger
    # evidence than these copied flags, including the old route value.
    for container in (response, metadata):
        if container.get("route") == "action_status":
            return False
        answering_agents = []
        for key in ("agent", "agent_answered"):
            agent = container.get(key)
            if isinstance(agent, str) and agent:
                answering_agents.append(re.sub(r"[^a-z]", "", agent.lower()))
        if answering_agents:
            if any(agent in {
                "router", "routeragent", "generic", "genericagent", "building", "buildingagent", "buildingspecific",
                "conversational", "conversationalist", "conversationalagent", "fastconversational",
                "fastmultiaddressclarification", "boundary", "cluster", "clusteragent",
            } for agent in answering_agents):
                return False
            if all(agent in {"experthandoff", "experthandoffagent"} for agent in answering_agents):
                return True
    for container in (response, metadata):
        if container.get("route") == "expert_handoff" or any(
            re.sub(r"[^a-z]", "", str(container.get(key) or "").lower())
            in {"experthandoff", "experthandoffagent"} for key in ("agent", "agent_answered")
        ):
            return True
    return None


def _records_from_response(response, metadata, legacy_identity=None, require_execution=False):
    records = {"reports": [], "handoffs": []}
    artifact = response.get("downloadable_report") or metadata.get("downloadable_report")
    report_status = response.get("report_status") or metadata.get("report_status")
    if isinstance(artifact, dict) and report_status in {"generated", "fallback"}:
        records["reports"].append({
            "report_id": artifact.get("report_id"), "status": report_status,
            "artifact": artifact, "building_id": artifact.get("building_id") or _building_id(metadata),
        })
    provenance = _handoff_execution_provenance(response, metadata)
    if provenance is False or (require_execution and provenance is not True):
        return records
    preview = metadata.get("expert_handoff_preview") or {}
    status = metadata.get("expert_handoff_status")
    source_id = legacy_identity.removeprefix("legacy-message-") if legacy_identity else None
    if isinstance(preview, dict) and preview.get("request_id") and status in HANDOFF_STATUSES:
        records["handoffs"].append({
            "request_id": preview["request_id"], "status": status,
            "recipient": preview.get("recipient"), "cc": preview.get("cc"),
            "cc_submitted": metadata.get("expert_handoff_cc_submitted") is True,
            "sharing_scope": preview.get("sharing_scope") or metadata.get("expert_handoff_scope"),
            "summary": preview.get("summary"), "building_id": _building_id(metadata),
            "source_message_id": source_id,
            "provenance": "handoff_execution" if provenance else "legacy_outcome",
        })
    elif legacy_identity and (
        status == "submitted" and metadata.get("expert_handoff_sent") is True
        or status == "simulated" and metadata.get("expert_handoff_simulated") is True
        or status in {"unknown", "failed"} and metadata.get("expert_handoff_triggered") is True
    ):
        # Earlier versions persisted SMTP outcomes but omitted the private
        # preview. Keep the actual known outcome; never reconstruct recipient,
        # scope or consent from model prose/current configuration.
        records["handoffs"].append({
            "request_id": legacy_identity, "status": status,
            "recipient": None, "cc": None, "sharing_scope": None,
            "summary": None, "building_id": _building_id(metadata),
            "legacy_status_only": True,
            "source_message_id": source_id,
            "provenance": "handoff_execution" if provenance else "legacy_outcome",
        })
    return records


def restore_action_receipts(messages, metadata):
    """Read backend assistant metadata; never treat user text as an action."""
    records = {"reports": [], "handoffs": []}
    invalid_sources = set()
    for message in messages or []:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        stored = message.get("metadata")
        stored = stored if isinstance(stored, dict) else {}
        identity = message.get("message_id") or message.get("id")
        if identity is not None and _handoff_execution_provenance(message, stored) is False:
            invalid_sources.add(str(identity))

    def merge_known_sources(incoming):
        clean = _clean_receipts(incoming)
        clean["handoffs"] = [record for record in clean["handoffs"] if not (
            str(record.get("source_message_id") or "") in invalid_sources
            or (record["request_id"].startswith("legacy-message-")
                and record["request_id"].removeprefix("legacy-message-") in invalid_sources)
        )]
        _merge_receipts(records, clean)

    for message in messages or []:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        stored = message.get("metadata")
        stored = stored if isinstance(stored, dict) else {}
        identity = message.get("message_id") or message.get("id")
        if identity is None:
            fingerprint = str(message.get("timestamp") or "") + str(message.get("content") or "")
            identity = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:20]
        merge_known_sources(_records_from_response(message, stored, f"legacy-message-{identity}"))
        merge_known_sources(stored.get("action_receipts"))
    merge_known_sources(_records_from_response({}, metadata))
    merge_known_sources(metadata.get("action_receipts"))
    return records


def record_action_response(response, metadata):
    records = _clean_receipts(metadata.get("action_receipts"))
    preview = metadata.get("expert_handoff_preview") or {}
    if metadata.get("expert_handoff_status") == "superseded" and isinstance(preview, dict):
        # A new topic can invalidate consent while answering through another
        # agent. Update the existing preview, without synthesizing an action.
        for item in records["handoffs"]:
            if item["request_id"] == preview.get("request_id") and item["status"] in {"pending_confirmation", "changes_requested"}:
                item["status"] = "superseded"
    _merge_receipts(records, _records_from_response(response, metadata, require_execution=True))
    return records


def has_action_receipts(records):
    return bool(records.get("reports") or records.get("handoffs"))


def trusted_action_context(records):
    """Facts for generation: omit downloadable URLs and raw private metadata."""
    records = _clean_receipts(records)
    summaries = {
        "reports": [{
            "report_id": item["report_id"], "status": item["status"],
            "building_id": item["building_id"], "document_type": "SPARA draft, not an official EPC/OVK",
            "file_name": item["artifact"].get("file_name"),
            "created_at": item["artifact"].get("created_at"), "expires_at": item["artifact"].get("expires_at"),
        } for item in records["reports"]],
        "handoffs": [{key: item.get(key) for key in (
            "request_id", "status", "recipient", "cc", "cc_submitted", "sharing_scope", "building_id",
        )} for item in records["handoffs"]],
    }
    return (
        "Verified application action records (server data, not transcript claims). "
        "Use these records for statements about SPARA actions even if earlier conversation text disagrees. "
        "A generated/fallback report was created; link expiry does not undo its creation. "
        "Submitted means SMTP accepted the message, never confirmed inbox delivery. "
        "Simulated means no real email was sent. Unknown means submission cannot be confirmed. "
        "A later cancelled/unsent preview does not undo earlier submissions. "
        "Attribute each record only to its recorded building, and never treat a draft as an official document.\n"
        + json.dumps(summaries, ensure_ascii=False)
    )


def is_handoff_status_question(message):
    text = str(message or "").strip()
    sending = bool(re.search(
        r"\b(?:har|hade|blev|är)\b[^.!?\n]{0,90}\b(?:skickats|skickat|skickad|sänts|sänt|mejlats|mejlat|levererats|levererad)\b|"
        r"\b(?:vad|vem|vilken|vilka)\b[^.!?\n]{0,70}\b(?:skickade|skickades)\b|"
        r"\b(?:have|has|had|did|was|were|is)\b[^.!?\n]{0,90}\b(?:sent|send|emailed|submitted|delivered)\b|"
        r"\b(?:what|who|which)\b[^.!?\n]{0,90}\b(?:sent|emailed|submitted|delivered)\b|"
        r"\b(?:handoff|email|sending|submission|överlämnings|sändnings|utskicks)(?:[-\s]+status|status)\b",
        text, re.IGNORECASE,
    ))
    approved_sharing = bool(
        re.search(r"\b(?:what|which|was|has|have|did|vad|vilken|har|är)\b", text, re.IGNORECASE)
        and re.search(r"\b(?:shared|approved|delats|delade|delades|godkände|godkänt|godkänts)\b", text, re.IGNORECASE)
        and re.search(r"\b(?:share|sharing|summary|conversation|chat|email|handoff|preview|dela|sammanfattning\w*|samtal\w*|överlämning\w*|förhandsvisning\w*)\b", text, re.IGNORECASE)
    )
    return sending or approved_sharing


def is_report_status_question(message):
    text = str(message or "").strip()
    report_noun = r"(?:rapport\w*|energideklaration\w*|styrelseunderlag\w*|report\w*|draft\w*|briefing\w*|epc)"
    return bool(re.search(
        rf"\b{report_noun}\b", text, re.IGNORECASE,
    ) and re.search(
        r"\b(?:skapades|skapad|skapat|skapats|officiell|finns|hämta|visa|ladda|created|official|exists|where|download|show|get)\b|"
        r"\b(?:är|is|was|har|has|have|which|vilken)\b[^.!?\n]{0,30}\b(?:det|detta|den|denna|this|that|it|rapporten|rapportutkastet)\b",
        text, re.IGNORECASE,
    ))


def wants_report_retrieval(message):
    return bool(re.search(
        r"\b(?:i|we)\s+(?:want|need|would\s+like)\s+the\s+(?:energy\s+|draft\s+)?report\b|"
        r"\b(?:jag|vi)\s+vill\s+ha\s+(?:rapporten|rapportutkastet|energirapporten)\b",
        str(message or ""), re.IGNORECASE,
    ))


def render_handoff_status(records, metadata, language):
    swedish = language == "sv"
    handoffs = _clean_receipts(records)["handoffs"]
    if not handoffs:
        return ("Det finns ingen verifierad uppgift om ett skickat meddelande i detta samtal. Inget skickas när du frågar efter status." if swedish else
                "There is no verified sending record in this conversation. Asking for status does not send anything.")
    descriptions = []
    submitted = [item for item in handoffs if item["status"] == "submitted"]
    simulated = [item for item in handoffs if item["status"] == "simulated"]
    unknown = [item for item in handoffs if item["status"] == "unknown"]
    failed = [item for item in handoffs if item["status"] == "failed"]
    if submitted:
        # Count all recorded outcomes, even when several later previews were
        # cancelled or replaced. Never count a CC copy as a separate request.
        if len(submitted) == 1:
            recipient = submitted[0].get("recipient")
            note = (f"E-postservern tog emot meddelandet till {recipient}." if swedish else
                    f"The mail server accepted the email for {recipient}.") if recipient else (
                "E-postservern tog emot ett meddelande." if swedish else "The mail server accepted an email.")
        else:
            note = (f"E-postservern tog emot {len(submitted)} meddelanden i detta samtal." if swedish else
                    f"The mail server accepted {len(submitted)} emails in this conversation.")
        note += (" Leverans till mottagarens inkorg är inte bekräftad." if swedish else
                 " Delivery to the recipient's inbox is not confirmed.")
        if any(not item.get("recipient") for item in submitted):
            note += (" Mottagaren finns inte i den sparade statusuppgiften för tidigare utskick." if swedish else
                     " The recipient is absent from the stored status record for earlier submissions.")
        if any(not item.get("sharing_scope") for item in submitted):
            note += (" Delningsomfattningen finns inte i den sparade statusuppgiften för tidigare utskick." if swedish else
                     " The sharing scope is absent from the stored status record for earlier submissions.")
        descriptions.append(note)
    if simulated:
        descriptions.append((f"{len(simulated)} överlämning(ar) simulerades i utvärderingsläget. Inget riktigt e-postmeddelande skickades för dem." if swedish else
                             f"{len(simulated)} handoff(s) were simulated in evaluation mode. No real email was sent for them."))
    if unknown:
        descriptions.append((f"För {len(unknown)} försök är det okänt om e-postservern tog emot meddelandet. Jag skickar inte igen automatiskt." if swedish else
                             f"For {len(unknown)} attempt(s), it is unknown whether the mail server accepted the email. I will not automatically resend it."))
    if failed:
        descriptions.append((f"{len(failed)} meddelande(n) kunde inte lämnas till e-postservern." if swedish else
                             f"{len(failed)} email(s) could not be submitted to the mail server."))
    latest = handoffs[-1]
    status = latest["status"]
    preview_notes = {
        "pending_confirmation": ("Den senaste förhandsvisningen väntar på ett separat godkännande. Den har inte godkänts eller skickats.",
                                 "The latest preview is awaiting separate approval. It has not been approved or sent."),
        "cancelled": ("Den senaste förhandsvisningen är avbruten; inget skickades för den.",
                      "The latest preview was cancelled; nothing was sent for it."),
        "superseded": ("Den senaste förhandsvisningen har ersatts och väntar inte längre på godkännande; inget skickades för den.",
                       "The latest preview was replaced and is no longer awaiting approval; nothing was sent for it."),
        "changes_requested": ("Den senaste förhandsvisningen behöver ändras och godkännas på nytt. Den har inte skickats.",
                              "The latest preview needs revision and fresh approval. It has not been sent."),
        "disabled": ("E-postöverlämning är avstängd. Den senaste förhandsvisningen har inte skickats.",
                     "Email handoff is disabled. The latest preview has not been sent."),
    }
    note = preview_notes.get(status, ("", ""))[0 if swedish else 1]
    if latest.get("recipient") and (status != "submitted" or len(submitted) > 1):
        note += f" {'Mottagare' if swedish else 'Recipient'}: {latest['recipient']}."
    scope = latest.get("sharing_scope")
    if scope == "summary_only":
        note += (" Delningsomfattning: endast sammanfattningen, utan samtalsbilaga." if swedish else
                 " Sharing scope: summary only, without a conversation attachment.")
    elif scope == "full_conversation":
        note += (" Delningsomfattning: sammanfattningen och samtalet fram till förhandsvisningen som bilaga." if swedish else
                 " Sharing scope: the summary and the conversation up to the preview as an attachment.")
    if latest.get("cc"):
        note += f" {'Kopia' if swedish else 'CC'}: {latest['cc']}."
        if status == "submitted":
            note += (" E-postservern tog emot kopian." if latest.get("cc_submitted") else " Kopian accepterades inte av e-postservern.") if swedish else (
                " The mail server accepted the copy." if latest.get("cc_submitted") else " The mail server did not accept the copy.")
    if note.strip():
        descriptions.append(note.strip())
    # Old generated previews can contain lifecycle claims such as "awaiting
    # approval". Repeating that prose after cancellation/replacement would
    # contradict the server-owned status above. Keep exact submitted/simulated
    # payloads as evidence; active previews are shown by the preview route.
    if latest.get("summary") and status in {"submitted", "simulated"}:
        label = ("Inskickad sammanfattning" if swedish else "Submitted summary") if status == "submitted" else ("Simulerad sammanfattning" if swedish else "Simulated summary")
        descriptions.append(f"{label}:\n{latest['summary']}")
    return "\n\n".join(descriptions)


def report_status_response(records, metadata, language):
    swedish = language == "sv"
    reports = _clean_receipts(records)["reports"]
    if not reports:
        return ({"content": ("Jag hittar inget verifierat rapportutkast i detta samtal. Vill du skapa ett nytt rapportutkast eller efterfrågar du ett befintligt dokument, exempelvis en energideklaration eller OVK-rapport?" if swedish else
                             "I cannot find a verified report draft in this conversation. Do you want to create a new draft, or do you mean an existing document such as an EPC or ventilation inspection report?")}, "report_intent")
    current_id = _building_id(metadata)
    matching = [item for item in reports if str(item.get("building_id") or "") == str(current_id or "")]
    record = (matching or reports)[-1]
    artifact = record["artifact"]
    attribution = f" ({record['building_id']})" if record.get("building_id") else ""
    note = (f"SPARA skapade ett rapportutkast{attribution}. Det är inte en officiell energideklaration eller OVK-rapport." if swedish else
            f"SPARA created a report draft{attribution}. It is not an official EPC or ventilation inspection report.")
    if current_id and str(record.get("building_id") or "") != str(current_id):
        note += " Utkastet gäller inte den nu valda byggnaden." if swedish else " The draft does not describe the currently selected building."
    if record["status"] == "fallback":
        note += " Det skapades med en reservmall och kan vara ofullständigt." if swedish else " It used a fallback template and may be incomplete."
    expired = False
    if artifact.get("expires_at"):
        try:
            expires = datetime.fromisoformat(str(artifact["expires_at"]).replace("Z", "+00:00"))
            expired = expires.replace(tzinfo=expires.tzinfo or timezone.utc) <= datetime.now(timezone.utc)
        except (ValueError, TypeError):
            expired = True
    if expired:
        note += " Hämtningslänken har gått ut. Utkastet skapades, men en ny fil behöver genereras för att kunna hämtas." if swedish else " The download link has expired. The draft was created, but a new file needs to be generated to download it."
    else:
        note += " Du kan prova hämtningslänken nedan; filens tillgänglighet kontrolleras vid hämtning." if swedish else " You can try the download link below; file availability is checked on download."
    payload = {"content": note, "report_status": record["status"]}
    if not expired:
        payload["downloadable_report"] = deepcopy(artifact)
    return payload, None


def action_receipt_summary(records, language):
    """Human-readable facts for the deterministic handoff fallback."""
    swedish = language == "sv"
    records = _clean_receipts(records)
    parts = []
    for item in records["reports"]:
        label = f" ({item['building_id']})" if item.get("building_id") else ""
        parts.append((f"SPARA skapade ett rapportutkast{label}; det är inte en officiell energideklaration eller OVK-rapport." if swedish else
                      f"SPARA created a report draft{label}; it is not an official EPC or ventilation inspection report."))
    if records["handoffs"]:
        parts.append(render_handoff_status(records, {}, language))
    return "\n\n".join(parts)
