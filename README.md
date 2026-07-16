# oa-paper-fetch

Download academic paper PDFs into a local folder, by DOI, title, URL, or a batch
file. Two layers:

1. **Open access (default, no dependencies)** — arXiv, Unpaywall, OpenAlex,
   Crossref title→DOI, Semantic Scholar `openAccessPdf`, and direct open PDF
   links. Pure Python standard library.
2. **Institutional (opt-in)** — for papers behind IEEE Xplore, Wiley Online
   Library, and ScienceDirect (Elsevier) that you can access through your
   institution's SSO. Reuses a browser session **you** signed in, via Playwright.

It never uses Sci-Hub, credential sharing, or paywall circumvention, and it never
handles your password — institutional sign-in happens in a real browser you drive.

## Please read: institutional use and terms

The institutional layer only downloads full text you are already entitled to
through your institution. But **IEEE, Wiley, and Elsevier all prohibit systematic
or bulk downloading** in their terms of use, and tripping their rate limits can
get your **whole institution's IP range blocked** — not just your account. This
tool is for filling in a handful of missing PDFs, not for scraping. It throttles
requests, caps each run, and stops after repeated blocks. Keep it that way.

## Install

The OA layer needs nothing beyond Python 3.10+.

The institutional layer needs Playwright and a Chromium build:

```bash
pip install playwright
playwright install chromium
```

## Open-access usage

```bash
# single paper
python oa_fetch.py --title "Attention is all you need" --out ./pdfs
python oa_fetch.py --doi 10.1109/LSP.2023.1234567 --out ./pdfs
python oa_fetch.py --url https://arxiv.org/abs/1706.03762 --out ./pdfs

# batch (markdown table, CSV, or one DOI/URL/title per line)
python oa_fetch.py --batch refs.md --out ./pdfs --format text

# preview what would be fetched without downloading
python oa_fetch.py --batch refs.md --out ./pdfs --dry-run
```

Set an email to enable Unpaywall (recommended, still works without it):

```bash
export UNPAYWALL_EMAIL="you@example.com"
```

## Institutional usage

**Step 1 — sign in once.** Opens IEEE, ScienceDirect, and Wiley in a browser.
Use "Access through your institution" / your campus SSO in each tab, then press
Enter. The session is saved to a persistent profile and reused later.

```bash
python oa_fetch.py --institutional-login
```

**Step 2 — fetch, OA first then institutional for what's left.** Add
`--institutional` to any normal command. OA sources are tried first (free, fast);
whatever is still missing and has a DOI/URL is retried through the logged-in
browser.

```bash
python oa_fetch.py --batch refs.md --out ./pdfs --institutional --format text
```

How the institutional fetch finds the PDF: it opens the DOI landing page in your
authenticated session, reads the `citation_pdf_url` meta tag (IEEE/Wiley/Elsevier
all emit it), and downloads it with your cookies; if that tag is absent it falls
back to each publisher's known PDF URL pattern.

Throttling knobs (defaults are deliberately gentle):

| flag | default | meaning |
|---|---|---|
| `--inst-delay` | `4.0` | minimum seconds between institutional requests |
| `--inst-jitter` | `3.0` | added random 0–N seconds |
| `--max-institutional` | `30` | max institutional downloads per run |
| `--browser-profile` | `~/.oa-paper-fetch/profile` | where the logged-in session lives |
| `--headless` | off | run the browser headless (headed is more reliable for SSO/challenges) |

If sign-in expires you'll see repeated `not_pdf_login_or_challenge`; the run
aborts after 3 in a row. Re-run `--institutional-login` to refresh the session.

## Batch input formats

- **Markdown table** with headers like `题名`/`title`, `链接`/`url`, `doi`, `标记`/`id`.
- **CSV** with similar columns.
- **Plain text** — one DOI, URL, or title per line (`#` comments ignored).

## Output

Written into `--out`:

- the downloaded PDFs (named `year_firstauthor_title.pdf`)
- `oa_fetch_results.json` — full per-item detail, including which sources were
  tried and the institutional retry outcome
- `oa_fetch_results.csv` — flat summary

Exit codes: `0` all resolved · `1` at least one paper had no PDF · `3` invalid
input · `4` transport/file error.

## Layout

```
oa_fetch.py             OA fetch + CLI + institutional orchestration
institutional_fetch.py  Playwright logged-in-browser downloader (opt-in)
requirements.txt        Playwright, only needed for the institutional layer
SKILL.md                Claude Code skill entry point
```
