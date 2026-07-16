---
name: oa-paper-fetch
description: Use when the user asks to find or download academic paper PDFs, download many references found by an AI, fill a reference folder, resume an earlier paper-download job, configure a default paper directory or pacing, or reuse institutional access. Accept DOI, title, URL, Markdown, CSV, or plain text; normalize a stable manifest; download OA first; and optionally reuse a user-authenticated session limited to IEEE Xplore, Wiley Online Library, and Elsevier ScienceDirect. Never use Sci-Hub, bypass a paywall, or handle school credentials.
---

# OA Paper Fetch

Use this `SKILL.md` as the primary entry point. Treat `oa_fetch.py` and
`institutional_fetch.py` as execution backends. Run the backend for the user;
do not make them assemble CLI commands unless they request the commands.

## Non-negotiable rules

- Apply OA first. Try direct open PDFs, arXiv, OpenAlex, Unpaywall, and Semantic
  Scholar before any institutional browser request.
- Limit institutional fallback to content the user is entitled to access on
  IEEE Xplore, Wiley Online Library, and Elsevier ScienceDirect. Never expand
  the publisher allowlist silently.
- Never ask for, read, type, or store a school password, SSO code, MFA code,
  recovery code, cookie, authorization header, token, or Playwright storage
  state. Let the user complete authentication in the visible browser.
- Never use Sci-Hub, shared credentials, CAPTCHA automation, paywall
  circumvention, proxy rotation, or anti-bot evasion.
- Keep all downloads serial. Keep institutional delay at least 4 seconds,
  jitter within 0--10 seconds, and institutional attempts at or below 30 per
  run. Never chain additional runs automatically to bypass the cap.
- Treat `~/.oa-paper-fetch/profile` as sensitive. Keep it local; never inspect,
  print, copy, upload, synchronize, or commit its contents.
- Do not invent bibliographic metadata. Preserve titles, DOI values, and URLs
  from the user or source material; leave unknown fields empty. For filename
  metadata, accept arXiv Atom records and citation tags on the allowed
  publisher article page as source material. Never infer year or author from
  a URL, journal name, or memory.
- Treat title-only input as a request to resolve identity, not permission to
  download the most similar result. Preserve the exact source title and let
  the backend confirm a DOI or report ambiguity.

## Resolve paths

Resolve `SKILL_DIR` to the absolute directory containing this `SKILL.md`, then
invoke:

```bash
python3 "$SKILL_DIR/oa_fetch.py" ...
```

Resolve input and output paths independently of the current working directory.
When the user supplies an output path, convert it to an absolute path and pass
`--out`. When they do not supply one, omit `--out`; the backend uses the saved
preference or `~/Desktop/Papers`.

## Turn AI references into a manifest

For multiple references found in the conversation, pasted by the user, or
read from an attachment:

1. Create a temporary UTF-8 CSV outside the repository with exactly these
   columns:

   ```csv
   id,title,doi,url
   ref-0001,Exact title from the source,10.xxxx/yyyy,https://example.org/article
   ```

2. Give every row a stable unique `id`. Copy only known values. Require at
   least one of `title`, `doi`, or `url`; leave the other cells empty.
3. Do not infer a DOI from general knowledge and do not repair a title by
   guessing. Let the backend normalize and resolve it.
4. Run the temporary CSV with `--batch`. The backend normalizes DOI/URL values,
   removes hard DOI/URL duplicates, flags possible title-only duplicates, and
   writes the canonical `oa_fetch_manifest.csv` into the output directory.
5. If a referenced paper has none of a source title, DOI, or URL, leave it out
   of the executable CSV, report the missing source information, and ask for
   the reference or attachment instead of inventing a row.

For an offline preflight without metadata queries or downloads, run:

```bash
python3 "$SKILL_DIR/oa_fetch.py" \
  --batch "/absolute/raw-references.csv" \
  --manifest-out "/absolute/oa_fetch_manifest.csv"
```

Report invalid and duplicate rows. Do not describe a normalized manifest as a
completed download.

## Resolve title-only references safely

Allow a row to contain only its exact source title. The backend queries arXiv,
Crossref, and OpenAlex for independent candidates before it uses a resolved
DOI.

- Accept one DOI automatically only when at least two independent sources
  return the same DOI with strong title agreement. An exact title from only
  one source remains insufficient; exact agreement changes the recorded
  reason, not the independent-corroboration requirement.
- Treat an exact-title `10.48550/arXiv.*` repository DOI as an alias of a
  publisher DOI only when at least two independent sources corroborate that
  one publisher DOI. Keep the arXiv OA URL and alias in the JSON evidence. One
  publisher source is insufficient, and two publisher DOIs always conflict.
- Treat lower similarity thresholds as candidate discovery only. Never select
  a DOI merely because it is the highest-scoring result.
- Preserve the candidate title, DOI, source, score, year, and first author in
  the JSON evidence.
- For `title_resolution_ambiguous`, keep the item pending and ask for a DOI,
  publisher URL, or corrected full title. Do not download any candidate.
- For `title_resolution_unresolved`, report failure and ask for a DOI,
  publisher URL, or the exact original title.
- An explicit DOI or supported URL remains the identity anchor. Metadata
  searches must not silently replace it.

## Configure standing preferences

Store only non-sensitive preferences in
`~/.oa-paper-fetch/config.json`. Use `--save-config` only after the user asks to
set or change a default.

Examples:

```bash
# Save a custom default directory and OA item interval.
python3 "$SKILL_DIR/oa_fetch.py" \
  --out "/absolute/default/Papers" \
  --oa-delay 1 \
  --save-config

# Save institutional fallback as the user's standing choice.
python3 "$SKILL_DIR/oa_fetch.py" \
  --institutional \
  --inst-delay 4 \
  --inst-jitter 3 \
  --max-institutional 30 \
  --save-config
```

Use explicit request values for the current run, then saved preferences, then
built-in defaults. Use `--oa-only` when the user wants to override a saved
institutional preference for one run. Never enable institutional fallback as a
standing preference without an explicit user request.

## Establish or refresh institutional login

The institutional backend requires Playwright. If it is missing, explain the
two installation commands from the backend error; do not install it without
authorization.

Before login, tell the user that a visible browser will open and they must
complete SSO/MFA themselves. Run:

```bash
python3 "$SKILL_DIR/oa_fetch.py" --institutional-login
```

The command opens IEEE Xplore, ScienceDirect, and Wiley Online Library. Do not
click or type in authentication fields. Wait for the user to finish in the
browser and press Enter in the command session. Reuse the persistent profile on
later runs while the publishers still accept it; do not treat its age as proof
that it is valid.

Use a visible browser by default. Use `--headless` only to reuse a profile that
has already worked; never use it for first login or login repair.

## Download

For one paper, use exactly one selector:

```bash
python3 "$SKILL_DIR/oa_fetch.py" --doi "10.xxxx/yyyy" --format text
python3 "$SKILL_DIR/oa_fetch.py" --title "Exact paper title" --format text
python3 "$SKILL_DIR/oa_fetch.py" --url "https://arxiv.org/abs/1706.03762" --format text
```

For multiple papers, use the prepared batch:

```bash
python3 "$SKILL_DIR/oa_fetch.py" \
  --batch "/absolute/raw-references.csv" \
  --format text
```

Pass `--out "/absolute/output"` only when the user specifies a destination.
Pass `--institutional` for a one-run institutional request; otherwise honor the
saved standing choice. The backend still sends only unresolved OA items with a
DOI or supported original URL to the institutional layer.

Treat a clear statement such as “use my configured school access” or “学校访问已经
配置过，这次直接使用” as authorization for institutional fallback in this run;
pass `--institutional`, but do not change the saved standing preference unless
the user explicitly asks to save it. A statement that the browser was once
logged in is not proof that the session is still valid; let the backend detect
a missing or rejected profile without inspecting its contents.

If `UNPAYWALL_EMAIL` is already configured, let the backend use it. Never ask
the user to reveal its value in chat or logs.

## Preserve accurate PDF names

Let the backend name PDFs as
`year_first-author_full-title_stable-hash.pdf`. The stable hash belongs to the
canonical identity and must remain present even when all bibliographic fields
are known.

- For arXiv URLs and arXiv DOI values, let the backend query the exact arXiv ID
  and use its title, publication year, and first author.
- For entitled IEEE Xplore, Wiley Online Library, and ScienceDirect downloads,
  let the institutional backend read the article page's `citation_title`,
  first `citation_author`, publication date, and DOI before finalizing the
  filename.
- When the task has an expected title from the user or DOI-anchored metadata,
  require the publisher page to expose `citation_title` and require that title
  to agree before requesting the PDF. Keep a missing title as
  `publisher_title_unverifiable` and a disagreement as
  `publisher_title_mismatch`; both remain pending and neither page is
  downloaded.
- When no expected title is attached to an explicit DOI or URL task, missing
  bibliographic fields alone do not block the anchored identity. Use the most
  specific non-invented fallback: known title, arXiv ID, DOI, PII/IEEE document
  ID, or URL basename.
- Never overwrite a different file at the desired name. Report
  `filename_error` and retain the verified PDF at its existing name.
- If the output filesystem does not support same-directory hard links, retain
  the old filename and report the migration error; do not copy over or delete
  the original as a fallback.
- Treat `renamed_from` as evidence that an already downloaded PDF was migrated,
  not downloaded again.

## Resume and continue

Treat rerunning the same canonical manifest in the same output directory as a
resume. Let the backend verify `%PDF`, reuse the canonical identity and state,
skip verified successes as `exists`, and retry unresolved items. Do not use
`--overwrite` unless the user explicitly requests a replacement.

An old state record without the current naming version may perform one
metadata-only refresh. The backend must verify the old PDF, create the new name
without overwriting, save state, and only then remove the old name. It must not
redownload the PDF merely to improve its filename. Subsequent resumes should
return `exists` without another metadata lookup.

When `oa_fetch_pending.csv` exists:

- Report every pending item and reason.
- For `institutional_cap_reached`, wait for a new user request before running
  the pending CSV as a new batch.
- For `login_refresh_required` or `profile_missing_login_required`, ask the
  user to refresh the visible login. Resume only after they confirm completion.
- For `title_resolution_ambiguous`, `publisher_title_mismatch`, or
  `publisher_title_unverifiable`, report the available identity evidence and
  wait for a corrected title, DOI, or supported article URL. Do not
  automatically retry the same unverified identity.
- Never start a second institutional batch automatically.
- If one pending CSV contains both a login-refresh reason and
  `institutional_cap_reached`, the login refresh gates the whole resume. After
  the user confirms it, rerun that same pending CSV once; splitting it is not
  required, and the new run still has the 30-attempt cap.

## Report results

Inspect the stdout JSON and `oa_fetch_results.json`. Report counts and paths for
`downloaded`, `exists`, `duplicate`, `failed`, and `pending`. Treat `candidate`
as dry-run evidence only, never as a downloaded PDF.

When present, also report `renamed_from`, `filename_error`, and
`filename_metadata_error`. Verify that a renamed file exists at the reported
path before describing the migration as complete.

Also report `title_resolution_status`, `title_resolution_reason`,
`resolved_doi`, `citation_title`, `publisher_title_match`, and
`publisher_title_score` when present. Candidate details remain in the JSON
report even when the flat CSV contains only their summary.

The output directory contains:

- accurately named, collision-safe PDF files;
- `oa_fetch_manifest.csv`, the normalized unique manifest;
- `oa_fetch_results.json` and `oa_fetch_results.csv`;
- `oa_fetch_state.json`, the local resume state;
- `oa_fetch_pending.csv` only when explicit continuation is required.

Preserve every unresolved item in the report. State whether it lacked a
resolvable identity, had no OA PDF, was outside the publisher allowlist, needed
a login refresh, or was deferred by the institutional cap.

## Stop conditions

- `publisher_not_allowed`: leave it unresolved; do not expand the allowlist.
- `title_resolution_ambiguous`: do not download; request a DOI, supported URL,
  or corrected exact title.
- `title_resolution_unresolved`: retain a failed result and request a DOI,
  supported URL, or exact source title.
- `publisher_title_mismatch`: do not request the PDF; retain a pending result
  for manual identity resolution.
- `publisher_title_unverifiable`: do not request the PDF; retain a pending
  result because an expected title could not be checked on the publisher page.
- `unsafe_pdf_url`: stop that item; do not download the URL manually.
- `not_pdf_login_or_challenge`, `profile_missing_login_required`, or repeated
  HTTP 4xx/challenge responses: stop institutional work and request a visible
  login refresh. Do not automate credentials or retry loops.
- `institutional_cap_reached`: write/report pending items and wait for a new
  user request.
- No OA or entitled PDF: retain a failed result for manual retrieval.

Interpret exits as: `0` all resolved (or manifest preflight produced usable
rows), `1` failed/pending items remain, `2` invalid CLI/config, `3` missing or
empty input, and `4` transport/output/state/report failure.
