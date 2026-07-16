"""Normalize and deduplicate structured bibliography manifests."""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from pathlib import Path

from store import atomic_write_csv


DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
MANIFEST_FIELDS = ["id", "title", "doi", "url"]


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_title(text: str) -> str:
    return re.sub(r"[\W_]+", " ", text.casefold(), flags=re.UNICODE).strip()


def normalize_doi(value) -> str | None:
    text = _text(value)
    if not text:
        return None
    text = re.sub(r"^doi\s*:\s*", "", text, flags=re.I)
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text, flags=re.I)
    match = DOI_RE.search(text)
    if not match:
        return None
    return match.group(0).rstrip(".,").lower()


def extract_doi(value) -> str | None:
    text = _text(value)
    if not text:
        return None
    match = DOI_RE.search(text)
    return match.group(0).rstrip(".,").lower() if match else None


def normalize_url(value) -> str | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = urllib.parse.urlsplit(text)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        return None
    netloc = host
    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]"
    if port is not None:
        netloc += f":{port}"
    path = parsed.path or "/"
    return urllib.parse.urlunsplit((scheme, netloc, path, parsed.query, ""))


def normalize_items(items: list[dict]) -> list[dict]:
    records = []
    used_ids: dict[str, int] = {}
    seen_doi: dict[str, str] = {}
    seen_url: dict[str, str] = {}
    seen_title: dict[str, str] = {}

    for index, raw in enumerate(items, 1):
        base_id = _text(raw.get("id")) or f"row{index}"
        count = used_ids.get(base_id, 0) + 1
        used_ids[base_id] = count
        input_id = base_id if count == 1 else f"{base_id}-{count}"
        title = _text(raw.get("title"))
        raw_doi = _text(raw.get("doi"))
        raw_url = _text(raw.get("url"))
        doi = normalize_doi(raw_doi)
        url = normalize_url(raw_url)
        record = {
            "id": input_id,
            "title": title,
            "doi": doi,
            "url": url,
            "input_index": index - 1,
            "canonical_id": None,
            "manifest_status": "ready",
            "validation_error": None,
            "duplicate_of": None,
            "possible_title_duplicate_of": None,
        }

        if raw_doi and not doi:
            record.update(manifest_status="invalid", validation_error="invalid_doi")
            records.append(record)
            continue
        if raw_url and not url:
            record.update(manifest_status="invalid", validation_error="invalid_url")
            records.append(record)
            continue
        if not (title or doi or url):
            record.update(manifest_status="invalid", validation_error="missing_identifier")
            records.append(record)
            continue

        duplicate_of = None
        if doi and doi in seen_doi:
            duplicate_of = seen_doi[doi]
        elif url and url in seen_url:
            duplicate_of = seen_url[url]

        if duplicate_of:
            record.update(
                canonical_id=duplicate_of,
                manifest_status="duplicate",
                duplicate_of=duplicate_of,
            )
            records.append(record)
            continue

        if doi:
            canonical_id = f"doi:{doi}"
        elif url:
            canonical_id = f"url:{url}"
        else:
            normalized_title = normalize_title(title or "")
            suffix = hashlib.sha256(input_id.encode("utf-8")).hexdigest()[:8]
            canonical_id = f"title:{normalized_title}:{suffix}"
        record["canonical_id"] = canonical_id
        if doi:
            seen_doi[doi] = canonical_id
        if url:
            seen_url[url] = canonical_id

        normalized_title = normalize_title(title or "")
        if normalized_title:
            if normalized_title in seen_title:
                record["possible_title_duplicate_of"] = seen_title[normalized_title]
            else:
                seen_title[normalized_title] = canonical_id
        records.append(record)
    return records


def fetchable(records: list[dict]) -> list[dict]:
    return [record for record in records if record.get("manifest_status") == "ready"]


def manifest_sha256(records: list[dict]) -> str:
    rows = [
        {key: record.get(key) for key in MANIFEST_FIELDS}
        for record in fetchable(records)
    ]
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_manifest_csv(records: list[dict], path: Path) -> Path:
    rows = [
        {key: record.get(key) or "" for key in MANIFEST_FIELDS}
        for record in fetchable(records)
    ]
    atomic_write_csv(Path(path), MANIFEST_FIELDS, rows)
    return Path(path)
