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

VERSION = "0.2.0"
MAX_PDF_BYTES = 80 * 1024 * 1024
DEFAULT_TIMEOUT = 30
DEFAULT_PROFILE_DIR = Path.home() / ".oa-paper-fetch" / "profile"
UA = "oa-paper-fetch/0.1.0 (+legal-open-access)"
PDF_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
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
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.port not in {None, 80, 443}:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in {"localhost", "metadata.google.internal", "metadata.aws.internal", "metadata"}:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast)


def normalize_title(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def title_score(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def extract_doi(text: str | None) -> str | None:
    if not text:
        return None
    match = DOI_RE.search(text)
    if not match:
        return None
    return match.group(0).rstrip(".")


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


def metadata_filename(meta: dict, fallback: str) -> str:
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
    if best and best["score"] >= 0.62:
        return best
    return best if best and best["score"] >= 0.50 else None


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
    if dest.exists() and not overwrite:
        return True, "exists"
    req = urllib.request.Request(url, headers={"User-Agent": PDF_UA, "Accept": "application/pdf,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = response.read(MAX_PDF_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return False, f"http_{exc.code}"
    except Exception as exc:
        return False, f"network_{type(exc).__name__}"
    if len(data) > MAX_PDF_BYTES:
        return False, "too_large"
    if not data.startswith(b"%PDF"):
        return False, "not_pdf"
    dest.write_bytes(data)
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
    filename = metadata_filename(meta, title or doi or item.get("id") or "paper")
    dest = out_dir / filename

    if dry_run:
        return {
            "success": bool(candidates),
            "dry_run": True,
            "file": str(dest) if candidates else None,
            "pdf_url": candidates[0][1] if candidates else None,
            "source": candidates[0][0] if candidates else None,
            "meta": meta,
            "sources": sources,
            "candidates": [{"source": s, "url": u} for s, u, _ in candidates],
        }

    attempts = []
    for source, url, _ in candidates:
        ok, reason = download_pdf(url, dest, timeout, overwrite)
        attempts.append({"source": source, "url": url, "result": reason})
        if ok:
            return {
                "success": True,
                "source": source,
                "pdf_url": url,
                "file": str(dest),
                "meta": meta,
                "sources": sources,
                "attempts": attempts,
            }
        time.sleep(0.5)

    return {
        "success": False,
        "source": None,
        "pdf_url": None,
        "file": None,
        "meta": meta,
        "sources": sources,
        "attempts": attempts,
        "error": "no_open_access_pdf_downloaded",
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
        with path.open(newline="", encoding="utf-8") as f:
            rows = []
            for rec in csv.DictReader(f):
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
    (out_dir / "oa_fetch_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = ["success", "source", "file", "pdf_url", "doi", "title", "source_id", "error"]
    with (out_dir / "oa_fetch_results.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for result in results:
            meta = result.get("meta") or {}
            writer.writerow({
                "success": result.get("success"),
                "source": result.get("source"),
                "file": result.get("file"),
                "pdf_url": result.get("pdf_url"),
                "doi": meta.get("doi"),
                "title": meta.get("title"),
                "source_id": meta.get("source_id"),
                "error": result.get("error"),
            })


def main() -> int:
    parser = argparse.ArgumentParser(description="Download legal open-access academic PDFs.")
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--doi")
    group.add_argument("--title")
    group.add_argument("--url")
    group.add_argument("--batch", type=Path)
    parser.add_argument("--out", type=Path, default=Path("pdfs"))
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--format", choices=["json", "text"], default="json")
    parser.add_argument("--version", action="store_true")

    inst = parser.add_argument_group("institutional (SSO) fetch")
    inst.add_argument("--institutional", action="store_true",
                      help="after OA fails, retry via a logged-in browser session (IEEE/Wiley/Elsevier)")
    inst.add_argument("--institutional-login", action="store_true",
                      help="open a browser to sign in via institutional SSO once, then exit")
    inst.add_argument("--browser-profile", type=Path, default=DEFAULT_PROFILE_DIR,
                      help=f"persistent browser profile dir (default: {DEFAULT_PROFILE_DIR})")
    inst.add_argument("--inst-delay", type=float, default=4.0, help="min seconds between institutional requests")
    inst.add_argument("--inst-jitter", type=float, default=3.0, help="added random seconds of delay")
    inst.add_argument("--max-institutional", type=int, default=30, help="max institutional downloads per run")
    inst.add_argument("--headless", action="store_true", help="run the institutional browser headless")
    args = parser.parse_args()

    if args.version:
        print(VERSION)
        return 0

    if args.institutional_login:
        from institutional_fetch import login
        return login(str(args.browser_profile))

    if not (args.doi or args.title or args.url or args.batch):
        parser.error("one of --doi/--title/--url/--batch is required (or use --institutional-login)")

    args.out.mkdir(parents=True, exist_ok=True)
    if args.batch:
        if not args.batch.exists():
            print(f"Batch file not found: {args.batch}", file=sys.stderr)
            return 3
        items = parse_batch(args.batch)
    else:
        items = [{"doi": args.doi, "title": args.title, "url": args.url, "id": None}]
    if not items:
        print("No papers found in input.", file=sys.stderr)
        return 3

    results = []
    transport_error = False
    for idx, item in enumerate(items, 1):
        if args.format == "text":
            label = item.get("title") or item.get("doi") or item.get("url") or item.get("id")
            print(f"[{idx}/{len(items)}] {label}", file=sys.stderr)
        try:
            result = resolve_item(item, args.out, args.timeout, args.overwrite, args.dry_run)
        except Exception as exc:
            transport_error = True
            result = {"success": False, "error": f"{type(exc).__name__}: {exc}", "meta": item}
        results.append(result)
        if args.format == "text":
            print(("  OK " if result.get("success") else "  MISS ") + str(result.get("file") or result.get("error") or result.get("pdf_url")), file=sys.stderr)

    if args.institutional and not args.dry_run:
        retry = []
        for idx, (result, item) in enumerate(zip(results, items)):
            if result.get("success"):
                continue
            meta = result.get("meta") or {}
            doi = meta.get("doi") or item.get("doi")
            url = item.get("url")
            if not (doi or url):
                continue
            title = meta.get("title") or item.get("title")
            filename = metadata_filename(meta, title or doi or item.get("id") or "paper")
            retry.append({"idx": idx, "id": item.get("id"), "doi": doi,
                          "title": title, "url": url, "dest": str(args.out / filename)})
        if retry:
            print(f"[institutional] retrying {len(retry)} item(s) via logged-in browser", file=sys.stderr)
            from institutional_fetch import fetch_batch
            inst_results = fetch_batch(
                retry, profile_dir=str(args.browser_profile), delay=args.inst_delay,
                jitter=args.inst_jitter, headless=args.headless,
                max_items=args.max_institutional, timeout=args.timeout, overwrite=args.overwrite,
            )
            for r in inst_results:
                idx = r.get("idx")
                if idx is None or not (0 <= idx < len(results)):
                    continue
                prev = results[idx]
                prev["institutional"] = {"success": r.get("success"),
                                         "error": r.get("error"), "pdf_url": r.get("pdf_url")}
                if r.get("success"):
                    prev.update({"success": True, "source": r.get("source", "institutional"),
                                 "pdf_url": r.get("pdf_url"), "file": r.get("file"), "error": None})

    write_reports(results, args.out)
    summary = {"total": len(results), "succeeded": sum(1 for r in results if r.get("success")), "failed": sum(1 for r in results if not r.get("success"))}
    payload = {"ok": summary["failed"] == 0, "summary": summary, "results": results, "reports": {"json": str(args.out / "oa_fetch_results.json"), "csv": str(args.out / "oa_fetch_results.csv")}}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if transport_error:
        return 4
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
