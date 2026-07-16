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

import random
import re
import time
from pathlib import Path
from urllib.parse import urlparse

MAX_PDF_BYTES = 80 * 1024 * 1024

# Base URLs opened during `login` so the user can SSO into each publisher once.
PUBLISHERS = {
    "ieee": "https://ieeexplore.ieee.org/Xplore/home.jsp",
    "elsevier": "https://www.sciencedirect.com/",
    "wiley": "https://onlinelibrary.wiley.com/",
}


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
    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for channel in ("chrome", None):
        try:
            kwargs = dict(
                user_data_dir=profile_dir,
                headless=headless,
                accept_downloads=True,
                args=["--disable-blink-features=AutomationControlled"],
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
        "source_id": item.get("id"),
    }


def _pdf_url_from_page(page, doi: str | None) -> str | None:
    """Prefer the citation_pdf_url meta tag; fall back to per-publisher patterns."""
    try:
        meta = page.get_attribute(
            'meta[name="citation_pdf_url"]', "content", timeout=3000
        )
    except Exception:
        meta = None
    if meta:
        return meta

    url = page.url
    host = (urlparse(url).hostname or "").lower()
    if "ieee" in host:
        m = re.search(r"/document/(\d+)", url)
        if m:
            return f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={m.group(1)}"
    if "sciencedirect" in host:
        m = re.search(r"/pii/(\w+)", url)
        if m:
            return f"https://www.sciencedirect.com/science/article/pii/{m.group(1)}/pdfft?download=true"
    if "wiley" in host and doi:
        return f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}?download=true"
    return None


def _download(ctx, pdf_url: str, dest: Path, timeout: int) -> tuple[bool, str]:
    try:
        resp = ctx.request.get(pdf_url, timeout=timeout * 1000)
    except Exception as exc:
        return False, f"network_{type(exc).__name__}"
    if not resp.ok:
        return False, f"http_{resp.status}"
    try:
        body = resp.body()
    except Exception as exc:
        return False, f"read_{type(exc).__name__}"
    if not body.startswith(b"%PDF"):
        return False, "not_pdf_login_or_challenge"
    if len(body) > MAX_PDF_BYTES:
        return False, "too_large"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
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

    Each item: {id, doi, title, url, dest, idx}. `dest` is the target PDF path;
    `idx` (if present) is echoed back so callers can merge with prior results.
    """
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
        for i, item in enumerate(capped, 1):
            base = {"meta": _meta(item), "idx": item.get("idx")}
            doi = item.get("doi")
            landing = item.get("url") or (f"https://doi.org/{doi}" if doi else None)
            dest = Path(item["dest"])

            if dest.exists() and not overwrite:
                results.append({**base, "success": True, "source": "institutional",
                                "file": str(dest), "pdf_url": None, "note": "exists"})
                continue
            if not landing:
                results.append({**base, "success": False, "error": "no_doi_or_url"})
                continue

            label = item.get("title") or doi or landing
            print(f"[institutional {i}/{len(capped)}] {label}")
            page = ctx.new_page()
            try:
                page.goto(landing, wait_until="domcontentloaded", timeout=timeout * 1000)
                page.wait_for_timeout(1500)
                pdf_url = _pdf_url_from_page(page, doi)
                if not pdf_url:
                    results.append({**base, "success": False, "error": "no_pdf_link_found"})
                else:
                    ok, reason = _download(ctx, pdf_url, dest, timeout)
                    if ok:
                        consecutive_blocks = 0
                        results.append({**base, "success": True, "source": "institutional",
                                        "pdf_url": pdf_url, "file": str(dest)})
                    else:
                        if reason.startswith("http_4") or "challenge" in reason:
                            consecutive_blocks += 1
                        results.append({**base, "success": False, "pdf_url": pdf_url, "error": reason})
            except Exception as exc:
                results.append({**base, "success": False, "error": f"{type(exc).__name__}"})
            finally:
                page.close()

            if consecutive_blocks >= 3:
                print("[institutional] aborting: 3 consecutive blocks/login walls — "
                      "check that you are still signed in.")
                results.append({"success": False, "idx": None,
                                "error": "aborted_after_repeated_blocks", "meta": {}})
                break
            time.sleep(delay + random.random() * jitter)
        ctx.close()
    return results
