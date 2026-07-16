@AGENTS.md

# Claude Code repository instructions

`AGENTS.md` is imported above as the canonical AI development and maintenance
manual.

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
git diff --check
```

Report implementation, tests, live OA, institutional login/download, commit,
and push as separate states.
