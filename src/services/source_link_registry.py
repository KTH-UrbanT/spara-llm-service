import json
import html
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit


def _registry_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "source_links.json"


def _load_registry() -> dict:
    try:
        payload = json.loads(_registry_path().read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _is_url(value: str) -> bool:
    lowered = (value or "").strip().lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _url_key(value: str) -> str:
    """Match exact destinations, never an entire domain or arbitrary subpath."""
    value = html.unescape(str(value or "").strip())
    if re.search(r"[\s\x00-\x1f\\]", value):
        return ""
    try:
        parts = urlsplit(value)
        if (parts.scheme.lower() not in {"http", "https"} or not parts.hostname
                or parts.username or parts.password):
            return ""
        # Decode only unreserved characters: encoded slashes/question marks can
        # identify a different resource and must not match a checked destination.
        path = quote(unicodedata.normalize("NFC", parts.path or "/"), safe="/%:@-._~!$&'()*+,;=")
        def normalize_escape(match):
            character = chr(int(match.group(1), 16))
            return character if re.fullmatch(r"[A-Za-z0-9._~-]", character) else match.group(0).upper()
        path = re.sub(r"%([0-9a-fA-F]{2})", normalize_escape, path)
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, parts.fragment))
    except ValueError:
        return ""


def _verified(source: dict) -> bool:
    return bool(isinstance(source, dict) and source.get("link_status") == "verified" and _url_key(source.get("link")))


def _verified_link_index(registry: dict) -> dict:
    index = {}
    for source in registry.values():
        if not isinstance(source, dict) or not _verified(source):
            continue
        for url in [source["link"], *(source.get("link_aliases") or [])]:
            key = _url_key(url)
            if key:
                index[key] = source
    return index


def verified_link_context() -> list:
    """A checked catalog for response/report writers; it is not retrieval evidence."""
    seen, context = set(), []
    for source in _load_registry().values():
        if _verified(source) and source["link"] not in seen:
            seen.add(source["link"])
            context.append({"title": source.get("link_title") or source.get("title"),
                            "link": source["link"], "link_type": source.get("link_type"),
                            "note": source.get("link_note", "")})
    return context


def _parse_source_key(source_key) -> str:
    if isinstance(source_key, dict):
        source_key = (source_key.get("filename") or source_key.get("source")
                      or source_key.get("link") or source_key.get("name") or "")
    value = str(source_key or "").strip()
    if not value:
        return ""

    if value.lower().startswith("source:"):
        value = value.split(":", 1)[1].strip()

    if value[:1] == value[-1:] and value[:1] in {'"', "'"}:
        value = value[1:-1]

    return unicodedata.normalize("NFC", value.replace("\\\\", "\\").strip())


def _filename_from_source(source_key) -> str:
    source_key = _parse_source_key(source_key)
    if not source_key or _is_url(source_key):
        return source_key
    return source_key.replace("\\", "/").rstrip("/").split("/")[-1]


def resolve_source_links(source_keys) -> list:
    registry = _load_registry()
    names = {unicodedata.normalize("NFC", key).casefold(): value for key, value in registry.items() if isinstance(value, dict)}
    urls = _verified_link_index(registry)
    sources = []
    seen = set()

    for source_key in source_keys or []:
        original = _parse_source_key(source_key)
        if not original:
            continue

        filename = _filename_from_source(original)
        source = names.get(filename.casefold()) or names.get(original.casefold()) or {}
        if _is_url(original) and not source:
            verified = urls.get(_url_key(original))
            if verified:
                source = {**verified, "title": verified.get("link_title") or verified.get("title"),
                          "link_type": "web_page" if verified.get("link_type") == "related_page" else verified.get("link_type")}
        fallback = source_key.get("name") if isinstance(source_key, dict) else None
        title = str(source.get("title") or fallback or ("Extern källa (länk ej verifierad)" if _is_url(original) else filename)).strip()
        link = source.get("link", "") if _verified(source) else ""

        # A resource page may be shared by several different documents.
        dedupe_key = (filename or original, link)
        if dedupe_key in seen:
            continue

        sources.append(
            {
                "name": title,
                "filename": filename or original,
                "link": link,
                **{key: source[key] for key in ("link_type", "link_status", "source_type", "link_title", "link_note", "link_verified_at") if key in source},
                **({"link_status": "unregistered"} if not source else {}),
            }
        )
        seen.add(dedupe_key)

    return sources


def guard_response_links(text: str, language: str = "sv") -> str:
    """Publish only checked catalog links, preserving plain text and code evidence.

    This checks destination identity against the audited catalog, not whether
    the surrounding claim is supported. External availability can change later.
    """
    if not isinstance(text, str) or not text:
        return text
    if not re.search(r"https?://|www\.|\]\(|\]\s*:\s*\S|<a\b", text, re.I):
        return text
    approved = _verified_link_index(_load_registry())
    held, removed = [], 0
    prefix = "\x00SPARA_LINK_"
    while prefix in text:
        prefix += "_"

    def hold(value):
        token = f"{prefix}{len(held)}\x00"
        held.append(value)
        return token

    # Code/JSON evidence is not a clickable citation and must stay byte-for-byte intact.
    text = re.sub(r"(?ms)^([ \t]*)(`{3,}|~{3,})[^\n]*\n.*?^\1\2[ \t]*$|(?<!`)(`+)[^\n]*?\3(?!`)",
                  lambda match: hold(match.group(0)), text)

    def link(label, url, image=False):
        nonlocal removed
        source = approved.get(_url_key(url))
        if not source or image:
            removed += 1
            return label
        title = str(source.get("link_title") or source.get("title") or label)
        if source.get("link_type") == "related_page":
            title += " (relaterad information)" if language == "sv" else " (related information)"
        title = title.replace("[", "\\[").replace("]", "\\]")
        return hold(f"[{title}]({source['link']})")

    # Resolve Markdown references before removing their definitions.
    references = {}
    def reference(match):
        references[match.group(1).strip().casefold()] = match.group(2) or match.group(3)
        return ""
    text = re.sub(r'(?m)^ {0,3}\[(?!\^)([^\]\n]+)\]:\s*(?:<([^>\n]+)>|(\S+))(?:[^\n]*)$', reference, text)
    def referenced(match):
        key = (match.group(2) or match.group(1)).strip().casefold()
        if key not in references:
            return match.group(0)
        return link(match.group(1), references[key])
    text = re.sub(r"\[([^\]\n]+)\]\[([^\]\n]*)\]", referenced, text)
    text = re.sub(r"(?<!!)\[([^\]\n]+)\](?![\[(])",
                  lambda match: link(match.group(1), references[match.group(1).strip().casefold()])
                  if match.group(1).strip().casefold() in references else match.group(0), text)

    inline = r'''(!?)\[([^\]\n]*)\]\(\s*(?:<([^>\n]+)>|((?:[^()\s]|\([^()\n]*\))+))(?:\s+["'][^\n]*?["'])?\s*\)'''
    text = re.sub(inline, lambda match: link(match.group(2), match.group(3) or match.group(4), bool(match.group(1))), text)
    text = re.sub(r'''(?is)<a\b[^>]*\bhref\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a\s*>''',
                  lambda match: link(re.sub(r"<[^>]*>", "", match.group(2)), match.group(1)), text)
    text = re.sub(r"(?i)<(https?://[^>\s]+)>", lambda match: link(match.group(1), match.group(1)), text)

    placeholder = "[länk ej verifierad]" if language == "sv" else "[link not verified]"
    def bare(match):
        url = match.group(0).rstrip(".,;!?)")
        suffix = match.group(0)[len(url):]
        if _url_key(url) in approved:
            return link(url, url) + suffix
        nonlocal removed
        removed += 1
        return placeholder + suffix
    text = re.sub(r'''(?i)(?:https?://|\bwww\.)[^\s<>"'\x00]+''', bare, text)
    for index in range(len(held) - 1, -1, -1):
        text = text.replace(f"{prefix}{index}\x00", held[index])
    if removed:
        note = ("Jag har utelämnat länkar som inte kunde verifieras. Källnamnen kan fortfarande användas för att söka efter underlaget."
                if language == "sv" else "I omitted links that could not be verified. You can still use the source names to look for the material.")
        if note not in text:
            text = text.rstrip() + "\n\n" + note
    return text
