from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
