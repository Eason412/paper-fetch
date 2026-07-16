# oa-paper-fetch

`oa-paper-fetch` is a Skill-first downloader for academic PDFs. The user invokes
the Skill with a DOI, title, URL, or batch file; the Skill calls the local Python
backend and returns a per-paper result.

The acquisition order is fixed:

1. **Open access first** — direct open PDFs, arXiv, OpenAlex, Unpaywall,
   Semantic Scholar `openAccessPdf`, plus Crossref metadata for title-to-DOI
   resolution. This layer uses only the Python standard library.
2. **Institutional fallback, when enabled** — retries only OA failures using a
   browser session the user authenticated. It is restricted to IEEE Xplore,
   Wiley Online Library, and Elsevier ScienceDirect.
3. **Unresolved/manual** — unsupported publishers and failed downloads remain
   explicit failure rows.

It does not use Sci-Hub, shared credentials, CAPTCHA bypass, or paywall
circumvention. It never reads, types, or stores a school password or MFA code.

## Use as a Skill

Keep the repository files together in a skill directory, then invoke
`oa-paper-fetch` by name or ask the agent to download a paper. `SKILL.md` is the
primary entry point and contains the full operating contract. The Python files
are backends, not the intended user interface.

The Skill resolves the directory containing `SKILL.md`, so it can call the
backend from any working directory:

```bash
python3 "$SKILL_DIR/oa_fetch.py" --doi "10.xxxx/yyyy" --out "/absolute/output"
```

Set `UNPAYWALL_EMAIL` in the local environment to enable Unpaywall. The Skill
must not print or request its value.

## Install

OA downloads need Python 3.10+ and no third-party package.

The optional institutional layer needs Playwright and Chromium:

```bash
pip install playwright
playwright install chromium
```

## Institutional setup

Run the one-time login command:

```bash
python3 "$SKILL_DIR/oa_fetch.py" --institutional-login
```

A visible browser opens IEEE Xplore, ScienceDirect, and Wiley Online Library.
The user chooses “Access through your institution” and completes campus SSO and
MFA directly in that browser. The tool does not interact with credential fields.
After the user presses Enter in the terminal, the browser session is retained in
`~/.oa-paper-fetch/profile` for later runs.

The profile contains authenticated cookies. Its directory is restricted to the
local user where the operating system permits, and it must not be inspected,
synced, uploaded, or committed. When the session expires, run the visible login
command again; do not save the campus password in a config file.

Then request a normal OA-first run with institutional fallback:

```bash
python3 "$SKILL_DIR/oa_fetch.py" --batch "/absolute/refs.md" \
  --out "/absolute/output" --institutional --format text
```

The institutional backend navigates only to HTTPS DOI links and the three
supported publisher sites. After DOI resolution it checks the final publisher
host, validates the PDF URL against the same publisher, and verifies that the
response is a PDF before writing it.

## Limits

Institutional use is serial and deliberately conservative. These are enforced
boundaries, not merely defaults:

| option | default | accepted range |
|---|---:|---:|
| `--inst-delay` | 4 seconds | at least 4 seconds |
| `--inst-jitter` | 3 seconds | 0--10 seconds |
| `--max-institutional` | 30 | 1--30 attempts |

The effective default interval is 4--7 seconds. A run stops after three
consecutive login walls/blocks. Do not automatically start follow-on batches:
publisher and institutional-library terms may prohibit systematic downloading.

## Direct CLI usage

The CLI remains useful for debugging or scripted local use:

```bash
# single OA paper
python3 oa_fetch.py --title "Attention is all you need" --out ./pdfs
python3 oa_fetch.py --doi "10.1109/LSP.2023.1234567" --out ./pdfs
python3 oa_fetch.py --url "https://arxiv.org/abs/1706.03762" --out ./pdfs

# batch: Markdown, CSV, or one DOI/URL/title per line
python3 oa_fetch.py --batch refs.md --out ./pdfs --format text

# metadata/candidate preview only; this does not prove that a PDF downloads
python3 oa_fetch.py --batch refs.md --out ./pdfs --dry-run
```

## Batch input

- Markdown tables with headers such as `题名`/`title`, `链接`/`url`, `doi`, and
  `标记`/`id`.
- CSV files with equivalent columns.
- Plain text with one DOI, URL, or title per line; lines starting with `#` are
  ignored.

## Results

The output directory contains:

- downloaded PDFs named from resolved metadata;
- `oa_fetch_results.json`, with source attempts and institutional retry status;
- `oa_fetch_results.csv`, with a flat summary including
  `institutional_error`.

Important institutional errors are `publisher_not_allowed`, `unsafe_pdf_url`,
`not_pdf_login_or_challenge`, `aborted_after_repeated_blocks`, and
`institutional_cap_reached`.

Exit codes are `0` when every paper resolves, `1` when at least one remains
unresolved, `2` for invalid CLI options, `3` for missing or empty input, and `4`
for a transport or file error.

## Layout

```text
SKILL.md                primary agent entry point and operating contract
oa_fetch.py             OA resolver, CLI, reports, and fallback orchestration
institutional_fetch.py  restricted Playwright session backend
requirements.txt        optional institutional dependency
tests/                  offline contract and boundary tests
```
