import json
from pathlib import Path


def _registry_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "source_links.json"


def _load_registry() -> dict:
    try:
        return json.loads(_registry_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _is_url(value: str) -> bool:
    lowered = (value or "").strip().lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _parse_source_key(source_key) -> str:
    value = str(source_key or "").strip()
    if not value:
        return ""

    if value.lower().startswith("source:"):
        value = value.split(":", 1)[1].strip()

    if value[:1] == value[-1:] and value[:1] in {'"', "'"}:
        value = value[1:-1]

    return value.replace("\\\\", "\\").strip()


def _filename_from_source(source_key) -> str:
    source_key = _parse_source_key(source_key)
    if not source_key or _is_url(source_key):
        return source_key
    return source_key.replace("\\", "/").rstrip("/").split("/")[-1]


def resolve_source_links(source_keys) -> list:
    registry = _load_registry()
    sources = []
    seen = set()

    for source_key in source_keys or []:
        original = _parse_source_key(source_key)
        if not original:
            continue

        filename = _filename_from_source(original)
        source = registry.get(filename) or registry.get(original) or {}
        title = str(source.get("title") or source.get("name") or filename or original).strip()
        link = str(source.get("link") or source.get("url") or "").strip()

        if _is_url(original) and not link:
            link = original

        dedupe_key = link or filename or original
        if dedupe_key in seen:
            continue

        sources.append(
            {
                "name": title,
                "filename": filename or original,
                "link": link,
            }
        )
        seen.add(dedupe_key)

    return sources
