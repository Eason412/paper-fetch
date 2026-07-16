#!/usr/bin/env python3
"""Legal open-access paper PDF fetcher.

Sources: direct open PDF URLs, arXiv, Crossref title->DOI metadata,
OpenAlex OA locations, Unpaywall OA locations, and Semantic Scholar
openAccessPdf. This script intentionally does not use Sci-Hub or paywall
bypass mechanisms.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import ipaddress
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import config as paper_config
import manifest as manifest_tools
import store


VERSION = "0.4.0"
MAX_PDF_BYTES = 80 * 1024 * 1024
DEFAULT_TIMEOUT = 30
DEFAULT_PROFILE_DIR = paper_config.DEFAULT_PROFILE_DIR
UA = f"oa-paper-fetch/{VERSION} (+legal-open-access)"
PDF_UA = UA
CROSSREF_TITLE_MIN_SCORE = 0.62

DOI_RE = manifest_tools.DOI_RE
ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", re.I)


def request_json(url: str, timeout: int) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except Exception:
        return None


def safe_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    if port not in {None, 80, 443}:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return False
    if host in {"localhost", "metadata.google.internal", "metadata.aws.internal", "metadata"}:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # urllib/socket accept several legacy numeric loopback spellings that
        # ipaddress intentionally rejects (for example 2130706433 or 0177.0.0.1).
        if re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+)){0,3}",
                        host, re.I):
            return False
        # Do not pre-resolve and then reconnect by name: that would still be
        # vulnerable to DNS rebinding, while VPN/proxy resolvers commonly map
        # public hosts into synthetic address ranges. Reject local namespaces
        # here and reapply this URL policy to every redirect hop instead.
        if host.endswith((".localhost", ".local", ".internal", ".home.arpa")):
            return False
        return True
    return ip.is_global


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Apply the same public-URL policy to every HTTP redirect hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        if not safe_url(target):
            raise urllib.error.HTTPError(
                target, code, "unsafe redirect target", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, target)


def normalize_title(text: str) -> str:
    return manifest_tools.normalize_title(text)


def title_score(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def extract_doi(text: str | None) -> str | None:
    return manifest_tools.extract_doi(text)


def extract_arxiv_pdf(text: str | None) -> str | None:
    if not text:
        return None
    match = ARXIV_RE.search(text)
    if not match:
        return None
    arxiv_id = match.group(1)
    return f"https://arxiv.org/pdf/{arxiv_id}.pdf"


def clean_filename(text: str, max_len: int = 150) -> str:
    text = re.sub(r"[\\/:*?\"<>|]+", "_", text)
    text = re.sub(r"\s+", "_", text.strip())
    text = re.sub(r"_+", "_", text).strip("._")
    return (text or "paper")[:max_len]


def metadata_filename(
    meta: dict, fallback: str, canonical_id: str | None = None
) -> str:
    if canonical_id:
        return store.build_filename(meta, fallback, canonical_id)
    year = str(meta.get("year") or "unknown")
    title = meta.get("title") or fallback or "paper"
    first_author = meta.get("first_author") or "unknown"
    return clean_filename(f"{year}_{first_author}_{title}") + ".pdf"


def crossref_title_to_doi(title: str, timeout: int) -> dict | None:
    query = urllib.parse.urlencode({"query.title": title, "rows": "5"})
    data = request_json(f"https://api.crossref.org/works?{query}", timeout)
    items = (((data or {}).get("message") or {}).get("items") or [])
    best = None
    for item in items:
        candidate_title = " ".join(item.get("title") or [])
        doi = item.get("DOI")
        if not candidate_title or not doi:
            continue
        score = title_score(title, candidate_title)
        candidate = {
            "doi": doi,
            "title": candidate_title,
            "score": score,
            "year": (((item.get("published-print") or item.get("published-online") or {}).get("date-parts") or [[None]])[0][0]),
            "first_author": ((item.get("author") or [{}])[0].get("family")),
            "url": item.get("URL"),
        }
        if best is None or score > best["score"]:
            best = candidate
    return best if best and best["score"] >= CROSSREF_TITLE_MIN_SCORE else None


def arxiv_title_lookup(title: str | None, timeout: int) -> dict:
    if not title:
        return {}
    query = urllib.parse.urlencode({
        "search_query": f'all:"{title}"',
        "start": "0",
        "max_results": "5",
    })
    url = f"http://export.arxiv.org/api/query?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            xml = response.read()
    except Exception:
        return {}
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return {}
    ns = {"a": "http://www.w3.org/2005/Atom"}
    best = None
    for entry in root.findall("a:entry", ns):
        found_title = re.sub(r"\s+", " ", (entry.findtext("a:title", default="", namespaces=ns) or "")).strip()
        found_id = entry.findtext("a:id", default="", namespaces=ns) or ""
        score = title_score(title, found_title)
        if score < 0.55:
            continue
        arxiv_id = found_id.rstrip("/").split("/")[-1]
        authors = entry.findall("a:author", ns)
        first_author = None
        if authors:
            first_author = (authors[0].findtext("a:name", default="", namespaces=ns) or "").split()[-1] or None
        year = None
        published = entry.findtext("a:published", default="", namespaces=ns)
        if published[:4].isdigit():
            year = int(published[:4])
        candidate = {
            "title": found_title,
            "doi": f"10.48550/arXiv.{arxiv_id}",
            "year": year,
            "first_author": first_author,
            "urls": [f"https://arxiv.org/pdf/{arxiv_id}.pdf"],
            "score": score,
        }
        if best is None or score > best["score"]:
            best = candidate
    return best or {}


def openalex_lookup(doi: str | None, title: str | None, timeout: int) -> dict:
    if doi:
        encoded = urllib.parse.quote(f"https://doi.org/{doi}", safe="")
        url = f"https://api.openalex.org/works/{encoded}"
    elif title:
        query = urllib.parse.urlencode({"search": title, "per-page": "3"})
        url = f"https://api.openalex.org/works?{query}"
    else:
        return {}
    data = request_json(url, timeout)
    if not data:
        return {}
    if "results" in data:
        best = None
        for item in data.get("results") or []:
            score = title_score(title or "", item.get("title") or "")
            if best is None or score > best[0]:
                best = (score, item)
        if not best or best[0] < 0.55:
            return {}
        data = best[1]
    authorships = data.get("authorships") or []
    first_author = None
    if authorships:
        first_author = ((authorships[0].get("author") or {}).get("display_name") or "").split()[-1] or None
    urls = []
    for key in ("best_oa_location", "primary_location"):
        loc = data.get(key) or {}
        for url_key in ("pdf_url", "landing_page_url"):
            u = loc.get(url_key)
            if u and (u not in urls):
                urls.append(u)
    oa_url = ((data.get("open_access") or {}).get("oa_url"))
    if oa_url and oa_url not in urls:
        urls.append(oa_url)
    return {
        "doi": (data.get("doi") or "").replace("https://doi.org/", "") or doi,
        "title": data.get("title") or title,
        "year": data.get("publication_year"),
        "first_author": first_author,
        "urls": urls,
    }


def unpaywall_lookup(doi: str, timeout: int) -> dict:
    email = os.environ.get("UNPAYWALL_EMAIL", "").strip()
    if not email:
        return {"skipped": "UNPAYWALL_EMAIL not set"}
    encoded = urllib.parse.quote(doi, safe="")
    data = request_json(f"https://api.unpaywall.org/v2/{encoded}?email={urllib.parse.quote(email)}", timeout)
    if not data:
        return {}
    urls = []
    for loc_key in ("best_oa_location",):
        loc = data.get(loc_key) or {}
        for url_key in ("url_for_pdf", "url"):
            u = loc.get(url_key)
            if u and u not in urls:
                urls.append(u)
    for loc in data.get("oa_locations") or []:
        for url_key in ("url_for_pdf", "url"):
            u = loc.get(url_key)
            if u and u not in urls:
                urls.append(u)
    z_authors = data.get("z_authors") or []
    first_author = None
    if z_authors:
        first_author = z_authors[0].get("family") or z_authors[0].get("given")
    return {
        "title": data.get("title"),
        "year": data.get("year"),
        "first_author": first_author,
        "urls": urls,
    }


def semantic_scholar_lookup(doi: str, timeout: int) -> dict:
    encoded = urllib.parse.quote(f"DOI:{doi}", safe=":")
    fields = "title,year,authors,openAccessPdf,externalIds,url"
    data = request_json(f"https://api.semanticscholar.org/graph/v1/paper/{encoded}?fields={fields}", timeout)
    if not data:
        return {}
    urls = []
    oa = data.get("openAccessPdf") or {}
    if oa.get("url"):
        urls.append(oa["url"])
    arxiv = (data.get("externalIds") or {}).get("ArXiv")
    if arxiv:
        urls.append(f"https://arxiv.org/pdf/{arxiv}.pdf")
    authors = data.get("authors") or []
    first_author = None
    if authors:
        first_author = (authors[0].get("name") or "").split()[-1] or None
    return {
        "title": data.get("title"),
        "year": data.get("year"),
        "first_author": first_author,
        "urls": urls,
    }


def download_pdf(url: str, dest: Path, timeout: int, overwrite: bool) -> tuple[bool, str]:
    if not safe_url(url):
        return False, "unsafe_url"
    if store.verify_pdf(dest) and not overwrite:
        return True, "exists"
    req = urllib.request.Request(url, headers={"User-Agent": PDF_UA, "Accept": "application/pdf,*/*"})
    try:
        opener = urllib.request.build_opener(SafeRedirectHandler())
        with opener.open(req, timeout=timeout) as response:
            data = response.read(MAX_PDF_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return False, f"http_{exc.code}"
    except Exception as exc:
        return False, f"network_{type(exc).__name__}"
    if len(data) > MAX_PDF_BYTES:
        return False, "too_large"
    if not data.startswith(b"%PDF"):
        return False, "not_pdf"
    store.atomic_write_bytes(dest, data)
    return True, "downloaded"


def direct_candidates(url: str | None) -> list[str]:
    if not url:
        return []
    arxiv_pdf = extract_arxiv_pdf(url)
    if arxiv_pdf:
        return [arxiv_pdf]
    parsed = urllib.parse.urlparse(url)
    if parsed.path.lower().endswith(".pdf"):
        return [url]
    return []


def resolve_item(item: dict, out_dir: Path, timeout: int, overwrite: bool, dry_run: bool) -> dict:
    original_title = item.get("title") or ""
    original_url = item.get("url") or ""
    doi = item.get("doi") or extract_doi(original_url) or extract_doi(original_title)
    title = original_title
    sources = []
    meta: dict = {"title": title, "doi": doi, "source_id": item.get("id"), "url": original_url}
    candidates: list[tuple[str, str, dict]] = []

    for u in direct_candidates(original_url):
        candidates.append(("direct", u, {}))
    for u in direct_candidates(title):
        candidates.append(("direct", u, {}))
    arxiv_pdf = extract_arxiv_pdf(original_url) or extract_arxiv_pdf(title)
    if arxiv_pdf:
        candidates.append(("arxiv", arxiv_pdf, {}))

    if not doi and title:
        ax = arxiv_title_lookup(title, timeout)
        sources.append({"source": "arxiv_title", "result": bool(ax), "score": ax.get("score") if ax else None})
        if ax:
            for key in ("title", "year", "first_author", "doi"):
                if ax.get(key) and not meta.get(key):
                    meta[key] = ax[key]
            for u in ax.get("urls") or []:
                candidates.append(("arxiv_title", u, ax))

        cr = crossref_title_to_doi(title, timeout)
        sources.append({"source": "crossref_title", "result": bool(cr), "score": (cr or {}).get("score")})
        if cr:
            doi = cr.get("doi")
            meta.update({k: v for k, v in cr.items() if k in {"title", "year", "first_author", "doi", "url"} and v})

    for source_name, lookup in (
        ("openalex", lambda: openalex_lookup(doi, title, timeout)),
        ("unpaywall", lambda: unpaywall_lookup(doi, timeout) if doi else {}),
        ("semantic_scholar", lambda: semantic_scholar_lookup(doi, timeout) if doi else {}),
    ):
        found = lookup()
        sources.append({"source": source_name, "result": bool(found), "skipped": found.get("skipped") if isinstance(found, dict) else None})
        if not found:
            continue
        for key in ("title", "year", "first_author", "doi"):
            if found.get(key) and not meta.get(key):
                meta[key] = found[key]
        for u in found.get("urls") or []:
            candidates.append((source_name, u, found))

    seen = set()
    candidates = [(s, u, m) for s, u, m in candidates if not (u in seen or seen.add(u))]
    canonical_id = item.get("canonical_id") or (
        f"doi:{doi}" if doi else f"legacy:{title or original_url or item.get('id') or 'paper'}"
    )
    state_filename = item.get("_state_filename")
    if state_filename and Path(state_filename).name == state_filename:
        filename = state_filename
    else:
        filename = metadata_filename(
            meta,
            title or doi or item.get("id") or "paper",
            canonical_id,
        )
    dest = out_dir / filename
    identity = {
        "canonical_id": canonical_id,
        "input_id": item.get("id"),
        "possible_title_duplicate_of": item.get("possible_title_duplicate_of"),
        "target_file": str(dest),
    }

    if dry_run:
        return {
            "success": bool(candidates),
            "status": "candidate" if candidates else "failed",
            "dry_run": True,
            "file": str(dest) if candidates else None,
            "pdf_url": candidates[0][1] if candidates else None,
            "source": candidates[0][0] if candidates else None,
            "meta": meta,
            "sources": sources,
            "candidates": [{"source": s, "url": u} for s, u, _ in candidates],
            **identity,
        }

    attempts = []
    for source, url, _ in candidates:
        ok, reason = download_pdf(url, dest, timeout, overwrite)
        attempts.append({"source": source, "url": url, "result": reason})
        if ok:
            return {
                "success": True,
                "status": "exists" if reason == "exists" else "downloaded",
                "source": source,
                "pdf_url": url,
                "file": str(dest),
                "meta": meta,
                "sources": sources,
                "attempts": attempts,
                **identity,
            }
        time.sleep(0.5)

    return {
        "success": False,
        "status": "failed",
        "source": None,
        "pdf_url": None,
        "file": None,
        "meta": meta,
        "sources": sources,
        "attempts": attempts,
        "error": "no_open_access_pdf_downloaded",
        **identity,
    }


def strip_cell(cell: str) -> str:
    return cell.strip().strip("`").strip()


def parse_markdown_table(path: Path) -> list[dict]:
    rows = []
    headers = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if "|" not in line:
            continue
        parts = [strip_cell(p) for p in line.strip().strip("|").split("|")]
        if not parts or all(re.fullmatch(r"-+", p.replace(":", "").strip()) for p in parts):
            continue
        if headers is None:
            headers = [p.lower() for p in parts]
            continue
        if len(parts) != len(headers):
            continue
        rec = dict(zip(headers, parts))
        title = rec.get("题名") or rec.get("title") or rec.get("paper") or rec.get("name")
        url = rec.get("链接") or rec.get("url") or rec.get("link")
        doi = rec.get("doi") or extract_doi(url or "") or extract_doi(title or "")
        ident = rec.get("标记") or rec.get("id") or rec.get("key")
        if title or url or doi:
            rows.append({"id": ident, "title": title, "url": url, "doi": doi})
    return rows


def parse_batch(path: Path) -> list[dict]:
    if path.suffix.lower() == ".md":
        return parse_markdown_table(path)
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as f:
            rows = []
            for rec in csv.DictReader(f):
                if not any(str(value or "").strip() for value in rec.values()):
                    continue
                lower = {k.lower(): v for k, v in rec.items() if k}
                title = lower.get("title") or rec.get("题名")
                url = lower.get("url") or lower.get("link") or rec.get("链接")
                doi = lower.get("doi") or extract_doi(url or "") or extract_doi(title or "")
                ident = lower.get("id") or lower.get("key") or rec.get("标记")
                rows.append({"id": ident, "title": title, "url": url, "doi": doi})
            return rows
    rows = []
    for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append({"id": str(idx), "title": None if extract_doi(line) or line.startswith("http") else line, "url": line if line.startswith("http") else None, "doi": extract_doi(line)})
    return rows


def write_reports(results: list[dict], out_dir: Path) -> None:
    store.atomic_write_json(out_dir / "oa_fetch_results.json", results)
    fields = ["success", "source", "file", "pdf_url", "doi", "title",
              "source_id", "error", "institutional_error", "status",
              "canonical_id", "input_id", "duplicate_of", "pending_reason"]
    rows = []
    for result in results:
        meta = result.get("meta") or {}
        rows.append({
                "success": result.get("success"),
                "source": result.get("source"),
                "file": result.get("file"),
                "pdf_url": result.get("pdf_url"),
                "doi": meta.get("doi"),
                "title": meta.get("title"),
                "source_id": meta.get("source_id"),
                "error": result.get("error"),
                "institutional_error": (result.get("institutional") or {}).get("error"),
                "status": result.get("status"),
                "canonical_id": result.get("canonical_id"),
                "input_id": result.get("input_id") or meta.get("source_id"),
                "duplicate_of": result.get("duplicate_of"),
                "pending_reason": result.get("pending_reason"),
        })
    store.atomic_write_csv(out_dir / "oa_fetch_results.csv", fields, rows)


def _config_cli_values(args) -> dict:
    return {
        "output_dir": args.out,
        "oa_delay": args.oa_delay,
        "timeout": args.timeout,
        "institutional": args.institutional,
        "browser_profile": args.browser_profile,
        "inst_delay": args.inst_delay,
        "inst_jitter": args.inst_jitter,
        "max_institutional": args.max_institutional,
        "headless": args.headless,
    }


def _item_meta(item: dict) -> dict:
    return {
        "title": item.get("title"),
        "doi": item.get("doi"),
        "source_id": item.get("id"),
        "url": item.get("url"),
    }


def _decorate_result(result: dict, item: dict, out_dir: Path) -> dict:
    result = dict(result)
    result.setdefault("meta", _item_meta(item))
    result.setdefault("canonical_id", item.get("canonical_id"))
    result.setdefault("input_id", item.get("id"))
    result.setdefault(
        "possible_title_duplicate_of", item.get("possible_title_duplicate_of")
    )
    if result.get("success"):
        result.setdefault("status", "downloaded")
    else:
        result.setdefault("status", "failed")
    if not result.get("target_file"):
        meta = result.get("meta") or {}
        filename = metadata_filename(
            meta,
            item.get("title") or item.get("doi") or item.get("id") or "paper",
            item.get("canonical_id"),
        )
        result["target_file"] = str(out_dir / filename)
    return result


def _persist_result(state: dict, out_dir: Path, item: dict, result: dict) -> None:
    store.record_result(state, item, result)
    store.save_state(out_dir, state)


def _summary(results: list[dict], unique_total: int) -> dict:
    statuses = ("candidate", "downloaded", "exists", "duplicate", "failed", "pending")
    summary = {
        "total": len(results),
        "unique": unique_total,
        "succeeded": sum(1 for result in results if result.get("success")),
    }
    for status in statuses:
        summary[status] = sum(1 for result in results if result.get("status") == status)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Download legal open-access academic PDFs.")
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--doi")
    group.add_argument("--title")
    group.add_argument("--url")
    group.add_argument("--batch", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument("--oa-delay", type=float, default=None,
                        help="seconds between OA paper items (0 to 60; default: 1)")
    parser.add_argument("--config", type=Path, default=paper_config.DEFAULT_CONFIG_PATH,
                        help=f"preferences file (default: {paper_config.DEFAULT_CONFIG_PATH})")
    parser.add_argument("--save-config", action="store_true",
                        help="save explicitly provided non-sensitive preferences")
    parser.add_argument("--manifest-out", type=Path,
                        help="normalize and deduplicate --batch to CSV without network access")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--format", choices=["json", "text"], default="json")
    parser.add_argument("--version", action="store_true")

    inst = parser.add_argument_group("institutional (SSO) fetch")
    inst_mode = inst.add_mutually_exclusive_group()
    inst_mode.add_argument("--institutional", dest="institutional", action="store_true",
                           help="after OA fails, retry via a logged-in browser session (IEEE/Wiley/Elsevier)")
    inst_mode.add_argument("--oa-only", dest="institutional", action="store_false",
                           help="disable a configured institutional fallback for this run")
    parser.set_defaults(institutional=None)
    inst.add_argument("--institutional-login", action="store_true",
                      help="open a browser to sign in via institutional SSO once, then exit")
    inst.add_argument("--browser-profile", type=Path, default=None,
                      help=f"persistent browser profile dir (default: {DEFAULT_PROFILE_DIR})")
    inst.add_argument("--inst-delay", type=float, default=None,
                      help="base seconds between institutional requests (minimum: 4)")
    inst.add_argument("--inst-jitter", type=float, default=None,
                      help="added random delay in seconds (0 to 10)")
    inst.add_argument("--max-institutional", type=int, default=None,
                      help="institutional attempts per run (1 to 30)")
    inst.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="reuse an established institutional profile without a visible window",
    )
    args = parser.parse_args()

    if args.version:
        print(VERSION)
        return 0

    config_path = args.config.expanduser()
    cli_values = _config_cli_values(args)
    try:
        file_values = paper_config.load_config(config_path)
        settings = paper_config.resolve_config(file_values, cli_values)
    except paper_config.ConfigError as exc:
        parser.error(str(exc))

    from institutional_fetch import validate_institutional_options
    try:
        validate_institutional_options(
            settings["inst_delay"],
            settings["inst_jitter"],
            settings["max_institutional"],
        )
    except ValueError as exc:
        parser.error(str(exc))

    provided_config = {
        key: value for key, value in cli_values.items() if value is not None
    }
    if args.save_config:
        if not provided_config:
            parser.error("--save-config requires at least one preference option")
        try:
            saved = paper_config.save_config(config_path, provided_config)
        except (paper_config.ConfigError, OSError) as exc:
            print(f"Could not save config {config_path}: {exc}", file=sys.stderr)
            return 4
        has_input = bool(args.doi or args.title or args.url or args.batch)
        if not has_input and not args.institutional_login:
            print(json.dumps({"ok": True, "config": str(config_path), "saved": saved},
                             ensure_ascii=False, indent=2))
            return 0
        print(f"[config] saved non-sensitive preferences to {config_path}", file=sys.stderr)

    if args.institutional_login:
        if args.headless is True:
            parser.error("--institutional-login cannot be combined with --headless")
        from institutional_fetch import login
        return login(str(settings["browser_profile"]))

    if not (args.doi or args.title or args.url or args.batch):
        parser.error(
            "one of --doi/--title/--url/--batch is required "
            "(or use --institutional-login/--save-config)"
        )
    if args.manifest_out and not args.batch:
        parser.error("--manifest-out requires --batch")

    if args.batch:
        batch_path = args.batch.expanduser()
        if not batch_path.exists():
            print(f"Batch file not found: {batch_path}", file=sys.stderr)
            return 3
        try:
            items = parse_batch(batch_path)
        except (OSError, UnicodeError, csv.Error) as exc:
            print(f"Could not read batch file {batch_path}: {exc}", file=sys.stderr)
            return 4
    else:
        items = [{"doi": args.doi, "title": args.title, "url": args.url, "id": None}]
    if not items:
        print("No papers found in input.", file=sys.stderr)
        return 3

    records = manifest_tools.normalize_items(items)
    ready = manifest_tools.fetchable(records)
    if args.manifest_out:
        try:
            target = manifest_tools.write_manifest_csv(records, args.manifest_out.expanduser())
        except OSError as exc:
            print(f"Could not write manifest: {exc}", file=sys.stderr)
            return 4
        manifest_summary = {
            "total": len(records),
            "ready": len(ready),
            "duplicate": sum(r["manifest_status"] == "duplicate" for r in records),
            "invalid": sum(r["manifest_status"] == "invalid" for r in records),
        }
        print(json.dumps({"ok": bool(ready), "summary": manifest_summary,
                          "manifest": str(target), "records": records},
                         ensure_ascii=False, indent=2))
        return 0 if ready else 3

    out_dir = settings["output_dir"]
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        normalized_manifest_path = manifest_tools.write_manifest_csv(
            records, out_dir / "oa_fetch_manifest.csv"
        )
    except OSError as exc:
        print(f"Could not prepare output directory or manifest: {exc}", file=sys.stderr)
        return 4

    state = store.load_state(out_dir, warn=lambda message: print(message, file=sys.stderr))
    state["manifest_sha256"] = manifest_tools.manifest_sha256(records)
    results: list[dict | None] = [None] * len(records)
    transport_error = False
    network_items = 0

    for record in records:
        index = record["input_index"]
        if record["manifest_status"] == "invalid":
            results[index] = {
                "success": False,
                "status": "failed",
                "error": f"invalid_manifest_row:{record['validation_error']}",
                "meta": _item_meta(record),
                "canonical_id": None,
                "input_id": record["id"],
            }
            continue
        if record["manifest_status"] == "duplicate":
            continue

        if args.format == "text":
            label = record.get("title") or record.get("doi") or record.get("url") or record.get("id")
            print(f"[{index + 1}/{len(records)}] {label}", file=sys.stderr)

        recorded_path = store.recorded_pdf_path(out_dir, state, record["canonical_id"])
        if not args.overwrite and not args.dry_run and recorded_path and store.verify_pdf(recorded_path):
            previous = (state.get("records") or {}).get(record["canonical_id"]) or {}
            result = {
                "success": True,
                "status": "exists",
                "source": previous.get("source"),
                "file": str(recorded_path),
                "pdf_url": None,
                "meta": _item_meta(record),
                "attempts": [],
                "sources": [],
            }
            result = _decorate_result(result, record, out_dir)
        else:
            if network_items and settings["oa_delay"]:
                time.sleep(settings["oa_delay"])
            network_items += 1
            work_item = dict(record)
            if recorded_path:
                work_item["_state_filename"] = recorded_path.name
            try:
                result = resolve_item(
                    work_item,
                    out_dir,
                    settings["timeout"],
                    args.overwrite,
                    args.dry_run,
                )
                result = _decorate_result(result, record, out_dir)
            except Exception as exc:
                transport_error = True
                result = _decorate_result(
                    {
                        "success": False,
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                        "meta": _item_meta(record),
                    },
                    record,
                    out_dir,
                )
        results[index] = result
        if not args.dry_run:
            try:
                _persist_result(state, out_dir, record, result)
            except OSError as exc:
                transport_error = True
                print(f"Could not save run state: {exc}", file=sys.stderr)
        if args.format == "text":
            print(("  OK " if result.get("success") else "  MISS ") +
                  str(result.get("file") or result.get("error") or result.get("pdf_url")),
                  file=sys.stderr)

    if settings["institutional"] and not args.dry_run:
        retry = []
        for record in ready:
            index = record["input_index"]
            result = results[index]
            if result is None or result.get("success"):
                continue
            meta = result.get("meta") or {}
            doi = meta.get("doi") or record.get("doi")
            url = record.get("url")
            if not (doi or url):
                continue
            title = meta.get("title") or record.get("title")
            target_file = result.get("target_file")
            if not target_file:
                filename = metadata_filename(
                    meta,
                    title or doi or record.get("id") or "paper",
                    record.get("canonical_id"),
                )
                target_file = str(out_dir / filename)
            retry.append({
                "idx": index,
                "id": record.get("id"),
                "doi": doi,
                "title": title,
                "url": url,
                "dest": target_file,
            })
        if retry:
            import institutional_fetch

            if not institutional_fetch.profile_available(settings["browser_profile"]):
                inst_results = [
                    {
                        "idx": item["idx"],
                        "success": False,
                        "error": "profile_missing_login_required",
                    }
                    for item in retry
                ]
                print("[institutional] login profile is missing; run --institutional-login",
                      file=sys.stderr)
            else:
                print(f"[institutional] retrying {len(retry)} item(s) via logged-in browser",
                      file=sys.stderr)
                inst_results = institutional_fetch.fetch_batch(
                    retry,
                    profile_dir=str(settings["browser_profile"]),
                    delay=settings["inst_delay"],
                    jitter=settings["inst_jitter"],
                    headless=settings["headless"],
                    max_items=settings["max_institutional"],
                    timeout=settings["timeout"],
                    overwrite=args.overwrite,
                )
            pending_login_errors = {
                "profile_missing_login_required",
                "not_pdf_login_or_challenge",
                "aborted_after_repeated_blocks",
            }
            records_by_index = {record["input_index"]: record for record in ready}
            for inst_result in inst_results:
                index = inst_result.get("idx")
                if index is None or not (0 <= index < len(results)):
                    continue
                prev = results[index]
                if prev is None:
                    continue
                error = inst_result.get("error")
                prev["institutional"] = {
                    "success": inst_result.get("success"),
                    "error": error,
                    "pdf_url": inst_result.get("pdf_url"),
                }
                if inst_result.get("success"):
                    prev.update({
                        "success": True,
                        "status": "exists" if inst_result.get("note") == "exists" else "downloaded",
                        "source": inst_result.get("source", "institutional"),
                        "pdf_url": inst_result.get("pdf_url"),
                        "file": inst_result.get("file"),
                        "error": None,
                    })
                elif error == "institutional_cap_reached":
                    prev.update(status="pending", pending_reason=error)
                elif error == "profile_missing_login_required":
                    prev.update(status="pending", pending_reason=error)
                elif error in pending_login_errors or str(error or "").startswith("http_4"):
                    prev.update(status="pending", pending_reason="login_refresh_required")
                else:
                    prev.update(status="failed")
                record = records_by_index.get(index)
                if record:
                    try:
                        _persist_result(state, out_dir, record, prev)
                    except OSError as exc:
                        transport_error = True
                        print(f"Could not save run state: {exc}", file=sys.stderr)

    winners = {
        record["canonical_id"]: results[record["input_index"]]
        for record in ready
        if results[record["input_index"]] is not None
    }
    for record in records:
        if record["manifest_status"] != "duplicate":
            continue
        winner = winners.get(record["duplicate_of"]) or {}
        results[record["input_index"]] = {
            "success": bool(winner.get("success")),
            "status": "duplicate",
            "duplicate_of": record["duplicate_of"],
            "canonical_id": record["canonical_id"],
            "input_id": record["id"],
            "source": winner.get("source"),
            "file": winner.get("file"),
            "pdf_url": winner.get("pdf_url"),
            "meta": _item_meta(record),
            "error": None if winner.get("success") else "duplicate_source_unresolved",
        }

    if any(result is None for result in results):
        print("Internal error: not every manifest row received a result.", file=sys.stderr)
        return 4
    final_results = [result for result in results if result is not None]
    pending = [
        (record, final_results[record["input_index"]])
        for record in records
        if final_results[record["input_index"]].get("status") == "pending"
    ]
    try:
        if not args.dry_run:
            pending_path = store.write_pending_csv(out_dir, pending)
            store.save_state(out_dir, state)
        write_reports(final_results, out_dir)
    except OSError as exc:
        print(f"Could not write result reports: {exc}", file=sys.stderr)
        return 4

    summary = _summary(final_results, len(ready))
    ok = summary["failed"] == 0 and summary["pending"] == 0
    reports = {
        "json": str(out_dir / "oa_fetch_results.json"),
        "csv": str(out_dir / "oa_fetch_results.csv"),
        "manifest": str(normalized_manifest_path),
    }
    if not args.dry_run:
        reports["state"] = str(out_dir / store.STATE_FILENAME)
    if pending and not args.dry_run:
        reports["pending"] = str(pending_path)
    payload = {"ok": ok, "summary": summary, "results": final_results, "reports": reports}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if transport_error:
        return 4
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
