from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SkillContractTests(unittest.TestCase):
    def test_skill_is_the_primary_oa_first_entrypoint(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("primary entry point", text)
        self.assertIn("OA first", text)
        self.assertIn("IEEE Xplore", text)
        self.assertIn("Wiley Online Library", text)
        self.assertIn("ScienceDirect", text)
        self.assertIn("Never ask for, read, type, or store", text)
        self.assertIn("oa_fetch.py", text)
        self.assertIn("~/Desktop/Papers", text)
        self.assertIn("manifest", text.lower())
        self.assertIn("Do not invent", text)
        self.assertIn("pending", text.lower())
        self.assertIn("citation_title", text)
        self.assertIn("renamed_from", text)
        self.assertIn("filename_error", text)
        self.assertIn("title_resolution_ambiguous", text)
        self.assertIn("publisher_title_mismatch", text)
        self.assertIn("publisher_title_unverifiable", text)

    def test_claude_project_skill_routes_to_the_canonical_contract(self):
        path = ROOT / ".claude" / "skills" / "oa-paper-fetch" / "SKILL.md"
        text = path.read_text(encoding="utf-8")

        self.assertIn("name: oa-paper-fetch", text)
        self.assertIn("${CLAUDE_SKILL_DIR}/../../..", text)
        self.assertIn("${CLAUDE_SKILL_DIR}/../../../SKILL.md", text)
        self.assertIn("canonical workflow and safety contract", text)
        self.assertIn("$ARGUMENTS", text)
        self.assertNotIn("playwright install", text)

    def test_bilingual_readmes_share_the_core_user_contract(self):
        english = (ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

        self.assertIn("[简体中文](README.zh-CN.md)", english)
        self.assertIn("[English](README.md)", chinese)
        for token in (
            "0.5.0",
            "Python 3.10",
            "~/Desktop/Papers",
            "title_resolution_ambiguous",
            "publisher_title_mismatch",
            "publisher_title_unverifiable",
            "profile_missing_login_required",
            "login_refresh_required",
            "institutional_cap_reached",
            "0.85",
            "0.93",
            "oa_fetch_pending.csv",
            "AGENTS.md",
            "SKILL.md",
        ):
            self.assertIn(token, english)
            self.assertIn(token, chinese)
        self.assertNotIn("Python 3.9", english)
        self.assertNotIn("Python 3.9", chinese)

        def bash_blocks(text):
            blocks = re.findall(r"```bash\n(.*?)\n```", text, flags=re.S)
            return [
                tuple(
                    line.rstrip()
                    for line in block.splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                )
                for block in blocks
            ]

        self.assertEqual(bash_blocks(english), bash_blocks(chinese))

    def test_repository_ai_manual_is_canonical_and_claude_is_a_thin_router(self):
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

        for token in (
            "canonical maintenance manual",
            "SKILL.md",
            "README.md",
            "README.zh-CN.md",
            "oa_fetch.py",
            "institutional_fetch.py",
            "tests/test_title_resolution.py",
            "python3 -m unittest discover -s tests -v",
            "git diff --check",
            "Never inspect the contents of `~/.oa-paper-fetch/profile`",
            "Do not commit or push unless the user explicitly asks",
            "publisher_title_unverifiable",
        ):
            self.assertIn(token, agents)

        self.assertTrue(claude.startswith("@AGENTS.md\n"))
        self.assertIn("canonical AI development and maintenance", claude)
        self.assertIn("read the root `SKILL.md`", claude)
        self.assertIn("update both `README.md` and `README.zh-CN.md`", claude)
        self.assertLess(len(claude.splitlines()), 40)
        self.assertNotIn("inst_delay", claude)
        self.assertNotIn("publisher_title_mismatch", claude)


if __name__ == "__main__":
    unittest.main()
