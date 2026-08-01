@AGENTS.md

# Claude Code repository instructions

`AGENTS.md` is imported above as the canonical AI development and maintenance
manual.

Claude Code also loads ancestor `CLAUDE.md` files. Their unrelated project
facts do not apply here; use `/context` to inspect loaded instructions, and
stop to report any conflict that cannot be reconciled with this repository's
local contract.

A personal Skill named `oa-paper-fetch` can override this project's Skill. For
repository work, `/oa-paper-fetch` must use
`.claude/skills/oa-paper-fetch/SKILL.md`; report a name collision instead of
silently following another copy.

For a user request to find or download papers, read the root `SKILL.md`
completely and execute that canonical workflow. The project Skill at
`.claude/skills/oa-paper-fetch/SKILL.md` is only a router to the same contract.

Do not reproduce architecture, safety, publisher, pacing, identity-resolution,
or storage rules here. If a repository change affects user-visible behavior,
update both `README.md` and `README.zh-CN.md` as required by `AGENTS.md`.

Run the offline gate from the repository root before reporting completion:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile oa_fetch.py institutional_fetch.py config.py manifest.py store.py
python3 oa_fetch.py --help
python3 oa_fetch.py --version
git diff --check
```

Report implementation, tests, live OA, institutional login/download, commit,
push, installed-copy update, and fresh-session discovery as separate states
when those surfaces are relevant. Do not commit or push unless the user asks.
