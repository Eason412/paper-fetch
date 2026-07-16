#!/usr/bin/env python3
"""Institutional (SSO) publisher PDF fetch via a persistent logged-in browser.

For full text the user is entitled to through their institution
(Shibboleth / "Access through your institution"), reachable at IEEE Xplore,
ScienceDirect (Elsevier), and Wiley Online Library after signing in once.

Mechanism: a Playwright persistent browser profile. The user signs in
interactively (`login`) once per publisher via institutional SSO; the session
persists in the profile directory and is reused on later runs. This module
NEVER handles passwords — authentication happens in the browser the user drives.

Throttling is built in to respect publisher terms; systematic bulk downloading
violates most publisher ToS and can get an institution's IP range blocked.
This is meant for filling in a handful of missing PDFs, not scraping.
"""
from __future__ import annotations

import difflib
import html
import math
import random
import re
import time
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import manifest as manifest_tools
import store

MAX_PDF_BYTES = 80 * 1024 * 1024
PUBLISHER_TITLE_MIN_SCORE = 0.93
MIN_INSTITUTIONAL_DELAY = 4.0
MAX_INSTITUTIONAL_JITTER = 10.0
MAX_INSTITUTIONAL_ITEMS = 30
DOI_HOSTS = {"doi.org", "dx.doi.org"}

# Base URLs opened during `login` so the user can SSO into each publisher once.
PUBLISHERS = {
    "ieee": "https://ieeexplore.ieee.org/Xplore/home.jsp",
    "elsevier": "https://www.sciencedirect.com/",
    "wiley": "https://onlinelibrary.wiley.com/",
}

PUBLISHER_LANDING_HOSTS = {
    "ieee": ("ieeexplore.ieee.org",),
    "elsevier": ("sciencedirect.com", "linkinghub.elsevier.com"),
    "wiley": ("onlinelibrary.wiley.com",),
}

PUBLISHER_PDF_HOSTS = {
    "ieee": ("ieeexplore.ieee.org",),
    "elsevier": ("sciencedirect.com",),
    "wiley": ("onlinelibrary.wiley.com",),
}


def _host_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def _publisher_from_url(url: str, *, pdf: bool = False) -> str | None:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
    except (TypeError, ValueError):
        return None
    if parsed.scheme != "https" or not host:
        return None
    hosts_by_publisher = PUBLISHER_PDF_HOSTS if pdf else PUBLISHER_LANDING_HOSTS
    for publisher, suffixes in hosts_by_publisher.items():
        if any(_host_matches(host, suffix) for suffix in suffixes):
            return publisher
    return None


def validate_institutional_options(
    delay: float, jitter: float, max_items: int
) -> None:
    if not math.isfinite(delay) or delay < MIN_INSTITUTIONAL_DELAY:
        raise ValueError(
            f"--inst-delay must be at least {MIN_INSTITUTIONAL_DELAY:g} seconds"
        )
    if not math.isfinite(jitter) or not 0 <= jitter <= MAX_INSTITUTIONAL_JITTER:
        raise ValueError(
            f"--inst-jitter must be between 0 and {MAX_INSTITUTIONAL_JITTER:g} seconds"
        )
    if not 1 <= max_items <= MAX_INSTITUTIONAL_ITEMS:
        raise ValueError(
            f"--max-institutional must be between 1 and {MAX_INSTITUTIONAL_ITEMS}"
        )


def _allowed_landing_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return (
        parsed.scheme == "https"
        and (host in DOI_HOSTS or _publisher_from_url(url) is not None)
    )


def _prepare_profile_dir(profile_dir: str) -> Path:
    path = Path(profile_dir).expanduser()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def profile_available(profile_dir: str | Path) -> bool:
    """Return whether a persistent profile exists without reading its contents."""
    path = Path(profile_dir).expanduser()
    if not path.is_dir():
        return False
    try:
        next(path.iterdir())
    except (StopIteration, OSError):
        return False
    return True


def _quoted_doi(doi: str) -> str:
    return quote(doi.strip(), safe="/:;()-._")


def _load_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "Playwright is required for institutional fetch.\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        ) from exc
    return sync_playwright


def _launch(p, profile_dir: str, headless: bool):
    """Launch a persistent context, preferring the system Chrome channel."""
    profile_path = _prepare_profile_dir(profile_dir)
    last_error: Exception | None = None
    for channel in ("chrome", None):
        try:
            kwargs = dict(
                user_data_dir=str(profile_path),
                headless=headless,
                accept_downloads=True,
            )
            if channel:
                kwargs["channel"] = channel
            return p.chromium.launch_persistent_context(**kwargs)
        except Exception as exc:  # pragma: no cover - environment dependent
            last_error = exc
    raise SystemExit(
        "Could not launch Chrome or bundled Chromium.\n"
        "  playwright install chromium\n"
        f"(last error: {last_error})"
    )


def login(profile_dir: str, timeout: int = 600) -> int:
    """Open each publisher headed so the user can sign in via institutional SSO.

    The session is saved to the persistent profile and reused by fetch_batch.
    """
    sync_playwright = _load_playwright()
    with sync_playwright() as p:
        ctx = _launch(p, profile_dir, headless=False)
        for name, url in PUBLISHERS.items():
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass
            print(f"[login] opened {name}: {url}")
        print(
            "\nSign in to each publisher via 'Access through your institution' "
            "in the opened tabs.\nWhen all three show you are signed in, press "
            "Enter here to save the session."
        )
        try:
            input()
        except EOFError:
            time.sleep(min(timeout, 120))
        ctx.close()
    print(f"[login] session saved to {profile_dir}")
    return 0


def _meta(item: dict) -> dict:
    return {
        "doi": item.get("doi"),
        "title": item.get("title"),
        "year": item.get("year"),
        "first_author": item.get("first_author"),
        "source_id": item.get("id"),
        "url": item.get("url"),
    }


def _citation_value(page, *names: str) -> str | None:
    for name in names:
        try:
            value = page.get_attribute(
                f'meta[name="{name}"]', "content", timeout=3000
            )
        except Exception:
            value = None
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        if value:
            return value
    return None


def _first_author_family(name: str | None) -> str | None:
    value = re.sub(r"\s+", " ", str(name or "")).strip()
    if not value:
        return None
    if "," in value:
        return value.split(",", 1)[0].strip() or None
    return value.split()[-1] or None


def _publisher_title_evidence(
    expected_title: str | None, citation_title: str | None
) -> dict:
    if not expected_title or not citation_title:
        return {"match": None, "score": None}
    expected = manifest_tools.normalize_title(html.unescape(str(expected_title)))
    citation = manifest_tools.normalize_title(html.unescape(str(citation_title)))
    if not expected or not citation:
        return {"match": None, "score": None}
    score = difflib.SequenceMatcher(None, expected, citation).ratio()
    return {
        "match": expected == citation or score >= PUBLISHER_TITLE_MIN_SCORE,
        "score": score,
    }


def _citation_metadata(page, item: dict) -> dict:
    """Read publisher citation tags for identity checks and file naming."""
    meta = {key: value for key, value in _meta(item).items() if value not in (None, "")}
    title = _citation_value(page, "citation_title")
    author = _first_author_family(_citation_value(page, "citation_author"))
    date = _citation_value(page, "citation_publication_date", "citation_date")
    year_match = re.search(r"(?<!\d)(?:18|19|20|21)\d{2}(?!\d)", date or "")
    page_doi = manifest_tools.normalize_doi(_citation_value(page, "citation_doi"))
    expected_title = item.get("expected_title")
    title_evidence = _publisher_title_evidence(expected_title, title)

    # The publisher page is authoritative for bibliographic display fields.
    if title:
        meta["title"] = title
        meta["citation_title"] = title
    if expected_title:
        meta["expected_title"] = expected_title
    meta["publisher_title_match"] = title_evidence["match"]
    meta["publisher_title_score"] = title_evidence["score"]
    if author:
        meta["first_author"] = author
    if year_match:
        meta["year"] = int(year_match.group(0))
    if page_doi:
        input_doi = manifest_tools.normalize_doi(meta.get("doi"))
        if not input_doi:
            meta["doi"] = page_doi
        elif input_doi != page_doi:
            meta["metadata_conflicts"] = {"citation_doi": page_doi}
    return meta


def _pdf_url_from_page(
    page, doi: str | None, publisher: str
) -> tuple[str | None, str | None]:
    """Prefer the citation_pdf_url meta tag; fall back to per-publisher patterns."""
    try:
        meta = page.get_attribute(
            'meta[name="citation_pdf_url"]', "content", timeout=3000
        )
    except Exception:
        meta = None
    if meta:
        if _publisher_from_url(meta, pdf=True) == publisher:
            return meta, None
        return None, "unsafe_pdf_url"

    url = page.url
    if publisher == "ieee":
        m = re.search(r"/document/(\d+)", url)
        if m:
            return (
                f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={m.group(1)}",
                None,
            )
    if publisher == "elsevier":
        m = re.search(r"/pii/(\w+)", url)
        if m:
            return (
                f"https://www.sciencedirect.com/science/article/pii/{m.group(1)}/pdfft?download=true",
                None,
            )
    if publisher == "wiley" and doi:
        return (
            f"https://onlinelibrary.wiley.com/doi/pdfdirect/{_quoted_doi(doi)}?download=true",
            None,
        )
    return None, "no_pdf_link_found"


def _download(
    ctx, pdf_url: str, dest: Path, timeout: int, *, publisher: str
) -> tuple[bool, str]:
    if _publisher_from_url(pdf_url, pdf=True) != publisher:
        return False, "unsafe_pdf_url"
    current_url = pdf_url
    resp = None
    for _ in range(6):
        try:
            resp = ctx.request.get(
                current_url, timeout=timeout * 1000, max_redirects=0
            )
        except Exception as exc:
            return False, f"network_{type(exc).__name__}"
        if not 300 <= resp.status < 400:
            break
        headers = {
            str(key).lower(): value
            for key, value in (getattr(resp, "headers", {}) or {}).items()
        }
        location = headers.get("location")
        if not location:
            return False, f"http_{resp.status}"
        current_url = urljoin(current_url, location)
        if _publisher_from_url(current_url, pdf=True) != publisher:
            return False, "unsafe_pdf_url"
    else:
        return False, "too_many_redirects"
    if resp is None:  # pragma: no cover - loop always executes
        return False, "network_no_response"
    if not resp.ok:
        return False, f"http_{resp.status}"
    final_url = getattr(resp, "url", current_url)
    if _publisher_from_url(final_url, pdf=True) != publisher:
        return False, "unsafe_pdf_url"
    try:
        body = resp.body()
    except Exception as exc:
        return False, f"read_{type(exc).__name__}"
    if not body.startswith(b"%PDF"):
        return False, "not_pdf_login_or_challenge"
    if len(body) > MAX_PDF_BYTES:
        return False, "too_large"
    dest.parent.mkdir(parents=True, exist_ok=True)
    store.atomic_write_bytes(dest, body)
    return True, "downloaded"


def fetch_batch(
    items: list[dict],
    *,
    profile_dir: str,
    delay: float = 4.0,
    jitter: float = 3.0,
    headless: bool = False,
    max_items: int = 30,
    timeout: int = 60,
    overwrite: bool = False,
) -> list[dict]:
    """Fetch entitled PDFs for `items` through the logged-in browser session.

    Each item: {id, doi, title, expected_title, year, first_author, url, dest,
    idx}. `dest` is the provisional PDF path;
    `idx` (if present) is echoed back so callers can merge with prior results.
    """
    validate_institutional_options(delay, jitter, max_items)
    if not items:
        return []
    sync_playwright = _load_playwright()
    results: list[dict] = []
    consecutive_blocks = 0
    capped = items[:max_items]
    dropped = len(items) - len(capped)
    if dropped > 0:
        print(f"[institutional] capped at {max_items}; {dropped} item(s) skipped this run")

    with sync_playwright() as p:
        ctx = _launch(p, profile_dir, headless)
        try:
            for i, item in enumerate(capped, 1):
                base = {"meta": _meta(item), "idx": item.get("idx")}
                doi = item.get("doi")
                landing = f"https://doi.org/{_quoted_doi(doi)}" if doi else item.get("url")
                dest = Path(item["dest"])

                if store.verify_pdf(dest) and not overwrite:
                    consecutive_blocks = 0
                    results.append({**base, "success": True, "source": "institutional",
                                    "file": str(dest), "pdf_url": None, "note": "exists"})
                    continue
                if not landing:
                    results.append({**base, "success": False, "error": "no_doi_or_url"})
                    continue
                if not _allowed_landing_url(landing):
                    results.append({**base, "success": False,
                                    "error": "publisher_not_allowed"})
                    continue

                label = item.get("title") or doi or landing
                print(f"[institutional {i}/{len(capped)}] {label}")
                page = ctx.new_page()
                try:
                    page.goto(landing, wait_until="domcontentloaded", timeout=timeout * 1000)
                    page.wait_for_timeout(1500)
                    publisher = _publisher_from_url(page.url)
                    if not publisher:
                        results.append({**base, "success": False,
                                        "error": "publisher_not_allowed"})
                    else:
                        page_base = {**base, "meta": _citation_metadata(page, item)}
                        if (
                            page_base["meta"].get("expected_title")
                            and not page_base["meta"].get("citation_title")
                        ):
                            results.append({
                                **page_base,
                                "success": False,
                                "error": "publisher_title_unverifiable",
                            })
                        elif page_base["meta"].get("publisher_title_match") is False:
                            results.append({
                                **page_base,
                                "success": False,
                                "error": "publisher_title_mismatch",
                            })
                        else:
                            resolved_doi = page_base["meta"].get("doi") or doi
                            pdf_url, pdf_error = _pdf_url_from_page(
                                page, resolved_doi, publisher
                            )
                            if not pdf_url:
                                results.append({**page_base, "success": False,
                                                "error": pdf_error or "no_pdf_link_found"})
                            else:
                                ok, reason = _download(
                                    ctx, pdf_url, dest, timeout, publisher=publisher
                                )
                                if ok:
                                    consecutive_blocks = 0
                                    results.append({**page_base, "success": True,
                                                    "source": "institutional",
                                                    "pdf_url": pdf_url, "file": str(dest)})
                                else:
                                    if reason.startswith("http_4") or "challenge" in reason:
                                        consecutive_blocks += 1
                                    else:
                                        consecutive_blocks = 0
                                    results.append({**page_base, "success": False,
                                                    "pdf_url": pdf_url, "error": reason})
                except Exception as exc:
                    results.append({**base, "success": False,
                                    "error": f"{type(exc).__name__}"})
                finally:
                    page.close()

                if consecutive_blocks >= 3:
                    print("[institutional] aborting: 3 blocks/login walls since the "
                          "last successful PDF — check that you are still signed in.")
                    for remaining in capped[i:]:
                        results.append({
                            "meta": _meta(remaining),
                            "idx": remaining.get("idx"),
                            "success": False,
                            "error": "aborted_after_repeated_blocks",
                        })
                    break
                if i < len(capped):
                    time.sleep(delay + random.random() * jitter)
        finally:
            ctx.close()
    for item in items[max_items:]:
        results.append({
            "meta": _meta(item),
            "idx": item.get("idx"),
            "success": False,
            "error": "institutional_cap_reached",
        })
    return results
