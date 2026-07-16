---
name: oa-paper-fetch
description: Use when the user asks to find or download academic paper PDFs by DOI, title, URL, or batch file. This is an OA-first paper-fetching skill with an optional institutional-session fallback limited to IEEE Xplore, Wiley Online Library, and Elsevier ScienceDirect. It never uses Sci-Hub, bypasses a paywall, or handles school credentials.
---

# OA Paper Fetch

This `SKILL.md` is the primary entry point. `oa_fetch.py` and
`institutional_fetch.py` are execution backends; do not make the user construct
CLI commands unless they ask for them.

Trigger this skill when the user asks to download a paper, fill missing PDFs in
a reference folder, or fetch papers listed by DOI, title, URL, Markdown, CSV, or
plain text.

## Non-negotiable rules

- OA first. Try direct open PDFs, arXiv, OpenAlex, Unpaywall, and Semantic
  Scholar before any institutional browser request.
- Institutional fallback is opt-in and is limited to content the user is
  entitled to access on IEEE Xplore, Wiley Online Library, and Elsevier
  ScienceDirect. Do not add or silently visit another publisher.
- Never ask for, read, type, or store a school password, SSO code, MFA code, or
  recovery code. The user completes authentication in the visible browser.
- Never use Sci-Hub, shared credentials, CAPTCHA bypass, paywall circumvention,
  proxy rotation, or anti-bot evasion.
- Keep institutional runs serial and small. The enforced base delay is at least
  4 seconds, jitter is 0--10 seconds, and the hard cap is 30 attempts per run.
  Do not weaken those limits or loop repeated runs automatically.
- Treat the browser profile as sensitive because it contains session cookies.
  Keep it local; never inspect, print, copy, upload, or commit its contents.

## Resolve the backend

Before running a command, resolve `SKILL_DIR` to the absolute directory that
contains this `SKILL.md`. Invoke the backend as:

```bash
python3 "$SKILL_DIR/oa_fetch.py" ...
```

This absolute-path rule applies even when the user's current working directory
is elsewhere. Resolve user-provided input and output paths independently; do not
assume they are inside the skill directory.

## Workflow

1. Identify the input type and the requested output directory. Preserve the
   user's DOI/title/URL and do not invent bibliographic metadata.
2. Run the OA layer first. For a single paper, use exactly one of `--doi`,
   `--title`, or `--url`. For multiple papers, use `--batch`.
3. If the user requested OA only, stop after the OA result.
4. Use `--institutional` only when the user explicitly requested school/library
   access, or when a previously configured local profile represents their
   standing choice to use institutional fallback. The backend still retries
   only the OA failures.
5. Inspect the returned JSON summary and `oa_fetch_results.json`. Report which
   papers succeeded, which source was used, and every unresolved item. A
   `--dry-run` candidate is not a confirmed download.

Examples:

```bash
python3 "$SKILL_DIR/oa_fetch.py" --doi "10.xxxx/yyyy" --out "/absolute/output"
python3 "$SKILL_DIR/oa_fetch.py" --title "paper title" --out "/absolute/output"
python3 "$SKILL_DIR/oa_fetch.py" --batch "/absolute/refs.csv" --out "/absolute/output" --format text
python3 "$SKILL_DIR/oa_fetch.py" --batch "/absolute/refs.md" --out "/absolute/output" --dry-run
```

If `UNPAYWALL_EMAIL` is already configured, the OA backend uses it. Do not ask
the user to expose the value in chat or logs.

## First institutional login

The institutional layer requires Playwright. If it is missing, explain the two
installation commands from the backend error; do not install dependencies
without the user's authorization.

Before the first institutional fetch, tell the user that a visible browser will
open and that they must complete SSO themselves. Then run:

```bash
python3 "$SKILL_DIR/oa_fetch.py" --institutional-login
```

The command opens IEEE Xplore, ScienceDirect, and Wiley Online Library. Do not
click or type in any login, SSO, MFA, or recovery field. Wait for the user to
finish and press Enter in the command session. A browser session is saved; the
school password is not saved by this tool.

After login, fetch with OA-first fallback:

```bash
python3 "$SKILL_DIR/oa_fetch.py" --batch "/absolute/refs.md" --out "/absolute/output" --institutional --format text
```

Use the visible browser by default. Headless mode is allowed only for reusing an
already working profile; never use it for first login or login repair.

## Failure and stop conditions

- `publisher_not_allowed`: leave the paper unresolved; do not extend the
  publisher allowlist.
- `unsafe_pdf_url`: stop that item; do not follow or download the URL manually.
- `not_pdf_login_or_challenge` or three repeated blocks: stop the run and ask
  the user to refresh the visible login with `--institutional-login`. Do not
  automate credentials or repeated retries.
- `institutional_cap_reached`: report the skipped items. Do not start another
  run without a new user request.
- No OA or entitled PDF found: retain a failure row for manual retrieval.

Outputs are written to `--out`: PDFs, `oa_fetch_results.json`, and
`oa_fetch_results.csv`. Exit codes are `0` for all resolved, `1` for one or more
unresolved papers, `2` for invalid CLI options, `3` for missing/empty input, and
`4` for a transport or file error.
