---
name: oa-paper-fetch
description: Download one or many academic paper PDFs from titles, DOI values, URLs, Markdown, CSV, or plain text. Use when Claude Code should turn AI-found references into a resumable OA-first download job and optionally reuse the user's visible institutional session for IEEE Xplore, Wiley Online Library, or Elsevier ScienceDirect.
---

# OA Paper Fetch for Claude Code

The repository root is three levels above this project Skill:

```bash
cd -- "${CLAUDE_SKILL_DIR}/../../.."
```

Before taking any action, read `${CLAUDE_SKILL_DIR}/../../../SKILL.md`
completely and follow it as the canonical workflow and safety contract. Do not
reproduce or weaken that contract in this wrapper. Run only the backends under
`${CLAUDE_SKILL_DIR}/../../..`; do not create a second implementation.

Treat `$ARGUMENTS` as the user's paper-download request. Execute the canonical
workflow for the user and report its results; do not merely return commands
unless the user asks for commands.
