import csv
import json
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import manifest  # noqa: E402
import oa_fetch  # noqa: E402


class ManifestTests(unittest.TestCase):
    def test_doi_is_canonicalized_without_inventing_missing_fields(self):
        records = manifest.normalize_items(
            [
                {
                    "id": "ref-1",
                    "title": None,
                    "doi": " https://doi.org/10.1109/ABC.123. ",
                    "url": None,
                }
            ]
        )

        self.assertEqual(records[0]["doi"], "10.1109/abc.123")
        self.assertIsNone(records[0]["title"])
        self.assertIsNone(records[0]["url"])
        self.assertEqual(records[0]["manifest_status"], "ready")
        self.assertEqual(records[0]["canonical_id"], "doi:10.1109/abc.123")

    def test_hard_dedupe_prefers_doi_then_url_and_preserves_every_input(self):
        records = manifest.normalize_items(
            [
                {"id": "a", "title": "A", "doi": "10.1000/X", "url": "https://example.org/a"},
                {"id": "b", "title": "A copy", "doi": "doi:10.1000/x", "url": None},
                {"id": "c", "title": "URL copy", "doi": None, "url": "https://EXAMPLE.org/a#fragment"},
            ]
        )

        self.assertEqual([r["manifest_status"] for r in records], ["ready", "duplicate", "duplicate"])
        self.assertEqual(records[1]["duplicate_of"], records[0]["canonical_id"])
        self.assertEqual(records[2]["duplicate_of"], records[0]["canonical_id"])

    def test_title_only_matches_are_flagged_but_not_merged(self):
        records = manifest.normalize_items(
            [
                {"id": "x", "title": "Same: Paper!", "doi": None, "url": None},
                {"id": "y", "title": "same paper", "doi": None, "url": None},
            ]
        )

        self.assertEqual([r["manifest_status"] for r in records], ["ready", "ready"])
        self.assertNotEqual(records[0]["canonical_id"], records[1]["canonical_id"])
        self.assertEqual(
            records[1]["possible_title_duplicate_of"], records[0]["canonical_id"]
        )

    def test_missing_ids_are_stable_and_duplicate_ids_are_suffixed(self):
        records = manifest.normalize_items(
            [
                {"id": None, "title": "First", "doi": None, "url": None},
                {"id": "same", "title": "Second", "doi": None, "url": None},
                {"id": "same", "title": "Third", "doi": None, "url": None},
            ]
        )

        self.assertEqual([r["id"] for r in records], ["row1", "same", "same-2"])

    def test_invalid_rows_are_retained_and_excluded_from_canonical_manifest(self):
        records = manifest.normalize_items(
            [
                {"id": "bad", "title": None, "doi": "not-a-doi", "url": None},
                {"id": "good", "title": "Good", "doi": None, "url": None},
            ]
        )

        self.assertEqual(records[0]["manifest_status"], "invalid")
        self.assertEqual(records[0]["validation_error"], "invalid_doi")
        self.assertEqual(records[1]["manifest_status"], "ready")

        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "manifest.csv"
            manifest.write_manifest_csv(records, target)
            with target.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(list(rows[0]), ["id", "title", "doi", "url"])
        self.assertEqual([row["id"] for row in rows], ["good"])

    def test_manifest_mode_deduplicates_without_calling_network_resolver(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            batch = tmp_path / "refs.csv"
            batch.write_text(
                "id,title,doi,url\n"
                "a,First,10.1000/X,\n"
                "b,Duplicate,https://doi.org/10.1000/x,\n",
                encoding="utf-8",
            )
            target = tmp_path / "normalized.csv"
            stdout = StringIO()
            argv = [
                "oa_fetch.py",
                "--batch",
                str(batch),
                "--manifest-out",
                str(target),
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(oa_fetch, "resolve_item") as resolver,
                redirect_stdout(stdout),
            ):
                exit_code = oa_fetch.main()

            payload = json.loads(stdout.getvalue())
            with target.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(exit_code, 0)
        resolver.assert_not_called()
        self.assertEqual(payload["summary"]["ready"], 1)
        self.assertEqual(payload["summary"]["duplicate"], 1)
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
