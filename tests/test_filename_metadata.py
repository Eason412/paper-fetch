import csv
import hashlib
import json
import sys
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import oa_fetch  # noqa: E402
import store  # noqa: E402


ARXIV_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1810.04805v2</id>
    <published>2018-10-11T00:50:01Z</published>
    <title>
      BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding
    </title>
    <author><name>Jacob Devlin</name></author>
    <author><name>Ming-Wei Chang</name></author>
    <link title="pdf" href="http://arxiv.org/pdf/1810.04805v2" type="application/pdf"/>
  </entry>
</feed>
"""


class FilenameMetadataTests(unittest.TestCase):
    def test_arxiv_id_extraction_covers_urls_versions_legacy_and_doi(self):
        cases = {
            "https://arxiv.org/abs/1810.04805": "1810.04805",
            "https://arxiv.org/pdf/1810.04805v2.pdf": "1810.04805v2",
            "https://export.arxiv.org/abs/1810.04805v3": "1810.04805v3",
            "arXiv:1810.04805": "1810.04805",
            "10.48550/arXiv.1810.04805": "1810.04805",
            "https://arxiv.org/abs/hep-th/9901001v2": "hep-th/9901001v2",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(oa_fetch.extract_arxiv_id(value), expected)

    def test_arxiv_id_lookup_parses_authoritative_atom_metadata(self):
        root = ET.fromstring(ARXIV_XML)
        with mock.patch.object(oa_fetch, "_request_arxiv_feed", return_value=root):
            meta = oa_fetch.arxiv_id_lookup("1810.04805", 5)

        self.assertEqual(
            meta["title"],
            "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
        )
        self.assertEqual(meta["year"], 2018)
        self.assertEqual(meta["first_author"], "Devlin")
        self.assertEqual(meta["doi"], "10.48550/arXiv.1810.04805")
        self.assertEqual(meta["urls"], ["https://arxiv.org/pdf/1810.04805v2.pdf"])

    def test_url_only_arxiv_dry_run_uses_title_in_filename(self):
        item = {
            "id": "row1",
            "title": None,
            "doi": None,
            "url": "https://arxiv.org/abs/1810.04805",
            "canonical_id": "url:https://arxiv.org/abs/1810.04805",
        }
        meta = {
            "title": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
            "year": 2018,
            "first_author": "Devlin",
            "doi": "10.48550/arXiv.1810.04805",
            "urls": ["https://arxiv.org/pdf/1810.04805v2.pdf"],
            "score": 1.0,
        }
        with mock.patch.object(oa_fetch, "arxiv_id_lookup", return_value=meta):
            result = oa_fetch.resolve_item(item, Path("/tmp/papers"), 5, False, True)

        filename = Path(result["file"]).name
        self.assertTrue(filename.startswith("2018_Devlin_BERT_Pre-training"))
        self.assertTrue(filename.endswith("_8a24a8c5.pdf"))
        self.assertEqual(result["meta"]["title"], meta["title"])

    def test_exact_arxiv_title_replaces_an_input_shorthand(self):
        item = {
            "id": "row1",
            "title": "BERT paper",
            "url": "https://arxiv.org/abs/1810.04805",
            "canonical_id": "url:https://arxiv.org/abs/1810.04805",
        }
        meta = {
            "title": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
            "year": 2018,
            "first_author": "Devlin",
            "doi": "10.48550/arXiv.1810.04805",
            "urls": ["https://arxiv.org/pdf/1810.04805v2.pdf"],
            "score": 1.0,
        }
        with mock.patch.object(oa_fetch, "arxiv_id_lookup", return_value=meta):
            result = oa_fetch.resolve_item(item, Path("/tmp/papers"), 5, False, True)

        self.assertEqual(result["meta"]["title"], meta["title"])
        self.assertIn("BERT_Pre-training", Path(result["file"]).name)

    def test_arxiv_metadata_failure_keeps_id_based_candidate_name(self):
        item = {
            "id": "row1",
            "url": "https://arxiv.org/abs/1810.04805",
            "canonical_id": "url:https://arxiv.org/abs/1810.04805",
        }
        with mock.patch.object(oa_fetch, "arxiv_id_lookup", return_value={}):
            result = oa_fetch.resolve_item(item, Path("/tmp/papers"), 5, False, True)

        self.assertTrue(result["success"])
        self.assertIn("arXiv_1810.04805", Path(result["file"]).name)
        self.assertEqual(result["pdf_url"], "https://arxiv.org/pdf/1810.04805.pdf")

    def test_existing_legacy_arxiv_file_is_migrated_without_pdf_download(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "papers"
            out.mkdir()
            batch = root / "refs.csv"
            batch.write_text(
                "id,title,doi,url\n"
                "row1,,,https://arxiv.org/abs/1810.04805\n",
                encoding="utf-8",
            )
            canonical_id = "url:https://arxiv.org/abs/1810.04805"
            old_name = store.build_filename({}, "row1", canonical_id)
            old_path = out / old_name
            payload = b"%PDF-1.7\nexisting-paper"
            old_path.write_bytes(payload)
            old_hash = hashlib.sha256(payload).hexdigest()
            state = store.new_state()
            state["records"][canonical_id] = {
                "input_ids": ["row1"],
                "file": old_name,
                "status": "downloaded",
                "source": "direct",
                "runs": [],
            }
            store.save_state(out, state)
            meta = {
                "title": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
                "year": 2018,
                "first_author": "Devlin",
                "doi": "10.48550/arXiv.1810.04805",
                "urls": ["https://arxiv.org/pdf/1810.04805v2.pdf"],
                "score": 1.0,
            }
            argv = [
                "oa_fetch.py",
                "--batch",
                str(batch),
                "--out",
                str(out),
                "--oa-delay",
                "0",
            ]
            stdout = StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(oa_fetch, "arxiv_id_lookup", return_value=meta),
                mock.patch.object(oa_fetch, "download_pdf") as download_pdf,
                redirect_stdout(stdout),
                redirect_stderr(StringIO()),
            ):
                self.assertEqual(oa_fetch.main(), 0)
            download_pdf.assert_not_called()
            first_result = json.loads(stdout.getvalue())["results"][0]
            new_path = Path(first_result["file"])

            self.assertFalse(old_path.exists())
            self.assertTrue(new_path.is_file())
            self.assertTrue(new_path.name.startswith("2018_Devlin_BERT_Pre-training"))
            self.assertEqual(hashlib.sha256(new_path.read_bytes()).hexdigest(), old_hash)
            self.assertEqual(first_result["status"], "exists")
            self.assertEqual(first_result["renamed_from"], old_name)

            saved = json.loads((out / store.STATE_FILENAME).read_text(encoding="utf-8"))
            saved_record = saved["records"][canonical_id]
            self.assertEqual(saved_record["file"], new_path.name)
            self.assertEqual(saved_record["meta"]["title"], meta["title"])
            self.assertEqual(saved_record["naming_version"], store.NAMING_VERSION)
            with (out / "oa_fetch_manifest.csv").open(encoding="utf-8") as handle:
                manifest_row = next(csv.DictReader(handle))
            self.assertEqual(manifest_row["title"], meta["title"])
            self.assertEqual(manifest_row["doi"], "")

            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    oa_fetch,
                    "resolve_item",
                    side_effect=AssertionError("resume must not query metadata again"),
                ),
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()),
            ):
                self.assertEqual(oa_fetch.main(), 0)

    def test_corrupt_legacy_file_is_redownloaded_then_migrated_to_accurate_name(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "papers"
            out.mkdir()
            batch = root / "refs.csv"
            batch.write_text(
                "id,title,doi,url\n"
                "row1,,,https://arxiv.org/abs/1810.04805\n",
                encoding="utf-8",
            )
            canonical_id = "url:https://arxiv.org/abs/1810.04805"
            old_name = store.build_filename({}, "row1", canonical_id)
            old_path = out / old_name
            old_path.write_bytes(b"<html>broken</html>")
            state = store.new_state()
            state["records"][canonical_id] = {
                "input_ids": ["row1"],
                "file": old_name,
                "status": "downloaded",
                "source": "direct",
                "runs": [],
            }
            store.save_state(out, state)
            meta = {
                "title": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
                "year": 2018,
                "first_author": "Devlin",
                "doi": "10.48550/arXiv.1810.04805",
                "urls": ["https://arxiv.org/pdf/1810.04805v2.pdf"],
                "score": 1.0,
            }

            def fake_download(url, dest, timeout, overwrite):
                self.assertEqual(dest, old_path)
                store.atomic_write_bytes(dest, b"%PDF-1.7\nreplacement")
                return True, "downloaded"

            argv = [
                "oa_fetch.py",
                "--batch",
                str(batch),
                "--out",
                str(out),
                "--oa-delay",
                "0",
            ]
            stdout = StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(oa_fetch, "arxiv_id_lookup", return_value=meta),
                mock.patch.object(oa_fetch, "download_pdf", side_effect=fake_download),
                redirect_stdout(stdout),
                redirect_stderr(StringIO()),
            ):
                self.assertEqual(oa_fetch.main(), 0)

            result = json.loads(stdout.getvalue())["results"][0]
            new_path = Path(result["file"])
            self.assertEqual(result["status"], "downloaded")
            self.assertEqual(result["renamed_from"], old_name)
            self.assertFalse(old_path.exists())
            self.assertTrue(store.verify_pdf(new_path))
            self.assertTrue(new_path.name.startswith("2018_Devlin_BERT_Pre-training"))


if __name__ == "__main__":
    unittest.main()
