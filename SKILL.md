---
name: oa-paper-fetch
description: Use when the user wants to find or download PDFs for academic papers by DOI, title, URL, or a batch file. Layer 1 is legal open access (arXiv, Unpaywall, OpenAlex, Crossref, Semantic Scholar, direct OA links). Layer 2 (opt-in) fetches IEEE / Wiley / Elsevier papers the user is entitled to through institutional SSO, by reusing a browser session the user signed in. Never uses Sci-Hub or paywall bypass, never handles passwords.
---

# OA Paper Fetch

Download paper PDFs into a chosen local directory. Two layers: open access by
default; institutional (SSO) publisher fetch on explicit opt-in.

Use when the user asks to download papers, recover missing PDFs, fill a reference
folder, or retry papers that were not downloadable from publisher pages.

## Policy

- OA layer: only legal/open sources (arXiv, Unpaywall, OpenAlex, Crossref
  metadata, Semantic Scholar `openAccessPdf`, publisher/repository OA PDFs).
- Institutional layer: only full text the user can already access through their
  institution, via a browser session the user signed in themselves. Never handle
  the user's password; never automate the SSO credential entry.
- Never use Sci-Hub, credential sharing, or paywall circumvention.
- Bulk/systematic downloading violates IEEE/Wiley/Elsevier ToS and can get an
  institution's IP range blocked. Keep runs small; the tool throttles and caps.
- If no PDF is found, return a failure row with DOI/title/URL for manual retrieval.

## Quick commands

```bash
# open access
python oa_fetch.py --title "paper title" --out ./pdfs
python oa_fetch.py --doi 10.xxxx/yyyy --out ./pdfs
python oa_fetch.py --batch refs.md --out ./pdfs --format text

# preview only
python oa_fetch.py --batch refs.md --out ./pdfs --dry-run
```

Optional but recommended for the OA layer:

```bash
export UNPAYWALL_EMAIL="your-email@example.com"
```

## Institutional (SSO) fetch — opt-in

```bash
# 1) sign in once (IEEE + ScienceDirect + Wiley open; user does SSO, presses Enter)
python oa_fetch.py --institutional-login

# 2) OA first, then retry the rest through the logged-in browser
python oa_fetch.py --batch refs.md --out ./pdfs --institutional --format text
```

Needs `pip install playwright && playwright install chromium`. Throttling flags:
`--inst-delay` (4s), `--inst-jitter` (3s), `--max-institutional` (30),
`--browser-profile` (default `~/.oa-paper-fetch/profile`), `--headless`.

## Batch input

Markdown tables (`题名`/`title`, `链接`/`url`, `doi`, `标记`/`id`), CSV with
similar columns, or plain text with one DOI/URL/title per line.

## Output

Into `--out`: downloaded PDFs, `oa_fetch_results.json`, `oa_fetch_results.csv`.

Exit codes: `0` all resolved · `1` a paper had no PDF · `3` invalid input ·
`4` transport/file error.
