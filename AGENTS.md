# paper-fetch AI development manual

## Scope

This file applies only to the `paper-fetch` repository. Do not mix source,
artifacts, or rules from its parent workspace or sibling repositories.

This is the canonical maintenance manual for AI agents changing the repository.
It is not the paper-download workflow itself. For a user request to find or
download papers, read the root `SKILL.md` completely and follow it.

## Document roles and authority

Keep each document in one role:

| File | Audience and authority |
| --- | --- |
| `AGENTS.md` | Canonical development, safety, change-coupling, and verification rules for AI maintainers. |
| `CLAUDE.md` | Thin Claude Code router to this file and the root `SKILL.md`. It must not copy policy. |
| `SKILL.md` | Only operational contract for an AI executing paper-download requests. |
| `.claude/skills/oa-paper-fetch/SKILL.md` | Thin project Skill that routes Claude Code to the root `SKILL.md`. |
| `README.md` | English guide for people. |
| `README.zh-CN.md` | Simplified Chinese guide for people. |
| Python modules and tests | Runtime truth for implemented behavior, schemas, constants, and regressions. |

When documents and code disagree, do not silently choose the convenient
version. Preserve the stricter safety boundary, inspect the implementation and
tests, then update every affected contract in the same change.

`README.md` and `README.zh-CN.md` must remain semantically equivalent. A
user-visible CLI, default, status, file, dependency, safety rule, or limitation
change requires both files to be updated. Translation does not need to be
line-for-line, but commands, numbers, paths, and behavior must match.

## Architecture map

| Path | Responsibility |
| --- | --- |
| `oa_fetch.py` | CLI, input parsing, title identity resolution, OA candidates, downloads, reporting, exit codes, and institutional fallback orchestration. |
| `institutional_fetch.py` | Visible login, isolated Playwright profile, publisher allowlist, citation metadata, publisher-title guard, pacing, cap, and block-streak stop. |
| `manifest.py` | `id,title,doi,url` normalization, validation, hard DOI/URL deduplication, possible title duplicates, canonical identity, and manifest hash. |
| `config.py` | Non-sensitive preference whitelist, defaults, validation, precedence, permissions, and atomic persistence. |
| `store.py` | Atomic writes, `%PDF` validation, collision-safe filenames, hard-link migration, state, and pending manifests. |
| `agents/openai.yaml` | Codex Skill UI metadata and implicit-invocation setting only. |
| `tests/` | Behavioral, safety, orchestration, storage, and Skill/document contract evidence. |

The OA layer uses the Python standard library. Playwright is an optional
dependency used only for institutional access.

## Non-negotiable product invariants

### Identity and manifest

- Preserve exact source titles, DOI values, and URLs. Do not invent or repair
  bibliographic data from memory.
- Hard-deduplicate DOI first and URL second. A title-only match is only a
  possible duplicate and must not be silently merged.
- Treat title-only input as identity resolution, not permission to select the
  highest-scoring search result.
- Keep the confirmation rule conservative: at least two independent sources
  must agree on one DOI at the confirmation threshold. An exact title from one
  source alone is insufficient.
- An exact arXiv repository DOI can be an alias only under the corroborated
  publisher-DOI rule implemented in `resolve_title_identity`. It must never
  hide two conflicting publisher DOI values.
- A title ambiguity, unresolved title, publisher-title mismatch, or
  unverifiable publisher title must stop PDF acquisition for that identity and
  remain auditable in results.

### OA and institutional order

- OA always runs before institutional access.
- Institutional fallback is limited to IEEE Xplore, Wiley Online Library, and
  Elsevier ScienceDirect. Do not expand the allowlist without an explicit
  product decision, implementation changes, boundary tests, and documentation.
- Only an OA miss with a confirmed DOI or supported original URL may enter the
  institutional backend.
- When an expected title exists, require the publisher page to expose
  `citation_title` and enforce the publisher-title guard before resolving or
  requesting the PDF. Preserve `publisher_title_unverifiable` when the page
  omits that title.

### Authentication and authorization

- Never ask for, read, type, print, copy, store, upload, or commit school
  passwords, SSO/MFA/recovery codes, Cookies, Authorization headers, tokens, or
  Playwright storage state.
- Never inspect the contents of `~/.oa-paper-fetch/profile`. It may only be
  treated as an opaque local session directory.
- Authentication is completed by the user in a visible browser. Do not
  automate credential fields, CAPTCHA, paywall bypass, anti-bot evasion, proxy
  rotation, or shared credentials.
- Do not use Sci-Hub or any source that bypasses access controls.
- A previous successful login is not proof that the session is still valid.
  Preserve `profile_missing_login_required` and `login_refresh_required` as
  explicit pending states.

### Network, pacing, and limits

- Keep paper processing serial.
- Preserve the institutional base delay of at least 4 seconds, jitter range of
  0--10 seconds, and cap of 1--30 attempts per run.
- Never automatically start another institutional batch to work around the cap.
- Preserve the stop after three HTTP 4xx/challenge/login-wall responses since
  the last successful institutional PDF.
- Keep OA URL SSRF checks, redirect revalidation, standard-port restrictions,
  same-publisher institutional PDF checks, the 80 MiB limit, and `%PDF`
  signature validation.

### Storage and recovery

- Preserve canonical identity, the stable 8-character filename suffix, and the
  240-byte UTF-8 filename limit.
- Do not overwrite a distinct existing target.
- Keep PDF, config, state, manifest, and report writes atomic.
- Naming migration must verify the old PDF, create a non-overwriting hard link,
  persist the new state, then remove the old name. Roll back the new link if the
  state write fails.
- `candidate` is not a download. Keep `candidate`, `downloaded`, `exists`,
  `duplicate`, `failed`, and `pending` distinct in code, reports, docs, and
  handoff messages.

## Change-coupling matrix

Every behavior change must update its evidence and public contracts:

| Change | Required code/tests/docs review |
| --- | --- |
| Title scoring, DOI confirmation, arXiv aliases | `oa_fetch.py`; `tests/test_title_resolution.py`; title-only sections in `SKILL.md` and both READMEs. |
| OA source order or institutional retry orchestration | `oa_fetch.py`; `tests/test_oa_first.py`; `institutional_fetch.py` when applicable; `SKILL.md` and both READMEs. |
| Publisher allowlist, landing/PDF URL rules, citation metadata | `institutional_fetch.py`; `tests/test_institutional_boundaries.py`; `tests/test_oa_url_safety.py`; `SKILL.md` and both READMEs. |
| Config key, default, range, or precedence | `config.py`; `tests/test_config.py`; config tables in `SKILL.md` and both READMEs. |
| Manifest fields, validation, deduplication, canonical identity | `manifest.py`; `tests/test_manifest.py`; manifest instructions in `SKILL.md` and both READMEs. |
| Filename, atomic write, state, migration, or pending schema | `store.py` and relevant `oa_fetch.py` paths; `tests/test_store_resume.py`; `tests/test_filename_metadata.py`; both READMEs and `SKILL.md` when agent behavior changes. |
| Report field, status, pending reason, or exit code | `oa_fetch.py`; orchestration/report tests; result sections in `SKILL.md` and both READMEs. |
| Codex UI metadata or Claude project wrapper | `agents/openai.yaml` or `.claude/skills/oa-paper-fetch/SKILL.md`; `tests/test_skill_contract.py`. |
| Human documentation structure | Both READMEs, language links, repository maps, and `tests/test_skill_contract.py`. |
| AI maintenance policy | `AGENTS.md`; keep `CLAUDE.md` a thin pointer. |

Do not update one README and leave the other stale.

## Working method

1. Inspect `git status --short --branch`. Preserve unrelated and pre-existing
   changes; never reset or discard them.
2. Read the exact implementation and tests for the behavior being changed.
   Read `SKILL.md` when the change affects an agent-executed download.
3. State the intended behavior, write or update a regression test, and confirm
   the test fails for the intended reason when practical.
4. Make the smallest implementation change that satisfies the contract.
5. Update every affected source-of-truth and both human READMEs.
6. Run proportional verification and inspect the final diff.
7. Report code, test, live-network, login, commit, and push states separately.

Do not refactor adjacent code, add publishers, loosen limits, or introduce new
configuration merely because it appears useful. Product-policy changes require
an explicit request.

## Verification levels

### Required offline gate

Run from the repository root:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile oa_fetch.py institutional_fetch.py config.py manifest.py store.py
python3 oa_fetch.py --help
python3 oa_fetch.py --version
git diff --check
```

The tests may write only temporary artifacts. Do not claim the gate passed
unless the commands were run in the current change.

### Optional live OA smoke

Run a real OA fetch only when network use and a temporary output directory are
within the user's request. Keep the result separate from offline test status.
Do not commit PDFs or reports.

### User-gated institutional verification

Institutional login and publisher downloads require the user's entitlement and
visible interaction. Do not start them merely to validate a code or docs
change. If explicitly requested, use a small batch, preserve pacing, never read
the profile, and report browser launch, completed user authentication, article
page validation, and PDF validation as separate checkpoints.

## Secrets, local state, and generated artifacts

Never add these to source control or AI prompts:

- `~/.oa-paper-fetch/profile` or any browser profile;
- `~/.oa-paper-fetch/config.json` contents when they contain local paths that
  are irrelevant to the task;
- passwords, MFA/SSO codes, Cookies, tokens, secrets, or authorization data;
- downloaded PDFs, `oa_fetch_results.*`, `oa_fetch_state.json`,
  `oa_fetch_manifest.csv`, `oa_fetch_pending.csv`, logs, caches,
  `__pycache__`, or virtual environments.

Use sanitized placeholders in tests and documentation. Do not add screenshots
of institutional pages unless the user explicitly requests diagnostics and
understands that campus or account information may be visible.

## Definition of done

A change is complete only when:

- requested behavior and scope are implemented without unrelated edits;
- affected tests were added or updated and the required offline gate passes;
- the root `SKILL.md` still expresses the canonical download workflow;
- the Claude project Skill and `CLAUDE.md` remain thin routers;
- `README.md` and `README.zh-CN.md` agree on commands, defaults, numbers,
  statuses, output files, safety boundaries, and limitations;
- no secret, profile content, generated paper artifact, or private account data
  entered the diff;
- `git diff --check` is clean;
- the handoff distinguishes uncommitted, committed, pushed, live-OA-tested, and
  institutional-session-tested states.

Do not commit or push unless the user explicitly asks.
