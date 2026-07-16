import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import store  # noqa: E402
import oa_fetch  # noqa: E402


class StoreResumeTests(unittest.TestCase):
    def test_atomic_pdf_write_and_verification(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "paper.pdf"
            store.atomic_write_bytes(target, b"%PDF-1.7\nfixture")

            self.assertTrue(store.verify_pdf(target))
            self.assertFalse(list(Path(tmp).glob("*.part-*")))
            target.write_bytes(b"<html>login</html>")
            self.assertFalse(store.verify_pdf(target))

    def test_filename_is_utf8_safe_stable_and_collision_resistant(self):
        meta = {
            "year": 2026,
            "first_author": "张",
            "title": "非常长的中文标题" * 80,
        }
        first = store.build_filename(meta, "paper", "doi:10.1000/a")
        same = store.build_filename(meta, "paper", "doi:10.1000/a")
        second = store.build_filename(meta, "paper", "doi:10.1000/b")

        self.assertEqual(first, same)
        self.assertNotEqual(first, second)
        self.assertLessEqual(len(first.encode("utf-8")), 240)
        self.assertTrue(first.endswith(".pdf"))

    def test_pdf_filename_migration_is_verified_and_non_overwriting(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "old.pdf"
            target = root / "new.pdf"
            source.write_bytes(b"%PDF-1.7\nfixture")

            store.prepare_pdf_migration(source, target)
            self.assertTrue(source.samefile(target))
            store.finish_pdf_migration(source, target)

            self.assertFalse(source.exists())
            self.assertEqual(target.read_bytes(), b"%PDF-1.7\nfixture")

    def test_pdf_filename_migration_refuses_a_distinct_existing_target(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "old.pdf"
            target = root / "new.pdf"
            source.write_bytes(b"%PDF-1.7\nsource")
            target.write_bytes(b"%PDF-1.7\ntarget")

            with self.assertRaises(FileExistsError):
                store.prepare_pdf_migration(source, target)

            self.assertEqual(source.read_bytes(), b"%PDF-1.7\nsource")
            self.assertEqual(target.read_bytes(), b"%PDF-1.7\ntarget")

    def test_interrupted_same_inode_migration_can_finish_on_retry(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "old.pdf"
            target = root / "new.pdf"
            source.write_bytes(b"%PDF-1.7\nfixture")
            store.prepare_pdf_migration(source, target)

            store.prepare_pdf_migration(source, target)
            store.finish_pdf_migration(source, target)

            self.assertFalse(source.exists())
            self.assertTrue(store.verify_pdf(target))

    def test_failed_state_write_rolls_back_new_name_and_result_path(self):
        with TemporaryDirectory() as tmp:
            out = Path(tmp)
            source = out / "old.pdf"
            target = out / "new.pdf"
            source.write_bytes(b"%PDF-1.7\nfixture")
            store.prepare_pdf_migration(source, target)
            canonical_id = "doi:10.1000/a"
            state = store.new_state()
            state["records"][canonical_id] = {
                "input_ids": ["ref-1"],
                "file": source.name,
                "status": "downloaded",
                "runs": [],
            }
            item = {"id": "ref-1", "canonical_id": canonical_id}
            result = {
                "success": True,
                "status": "exists",
                "file": str(target),
                "target_file": str(target),
                "meta": {"title": "Paper"},
                "renamed_from": source.name,
            }

            with mock.patch.object(store, "save_state", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    oa_fetch._persist_result(
                        state,
                        out,
                        item,
                        result,
                        migration_source=source,
                    )

            self.assertTrue(source.is_file())
            self.assertFalse(target.exists())
            self.assertEqual(result["file"], str(source))
            self.assertEqual(state["records"][canonical_id]["file"], source.name)

    def test_state_round_trip_preserves_file_and_attempt_history(self):
        with TemporaryDirectory() as tmp:
            out = Path(tmp)
            state = store.new_state("manifest-hash")
            item = {"id": "ref-1", "canonical_id": "doi:10.1000/a"}
            result = {
                "status": "downloaded",
                "success": True,
                "source": "openalex",
                "file": str(out / "paper_deadbeef.pdf"),
                "attempts": [{"source": "openalex", "result": "downloaded"}],
                "meta": {
                    "title": "Stored title",
                    "year": 2026,
                    "first_author": "Author",
                },
            }

            store.record_result(state, item, result)
            store.save_state(out, state)
            loaded = store.load_state(out)

            record = loaded["records"]["doi:10.1000/a"]
            self.assertEqual(record["file"], "paper_deadbeef.pdf")
            self.assertEqual(record["status"], "downloaded")
            self.assertEqual(record["input_ids"], ["ref-1"])
            self.assertEqual(record["meta"]["title"], "Stored title")
            self.assertEqual(record["naming_version"], store.NAMING_VERSION)
            self.assertEqual(len(record["runs"]), 1)
            self.assertEqual(json.loads((out / store.STATE_FILENAME).read_text())["version"], 1)

    def test_pending_csv_is_a_valid_future_batch_manifest(self):
        with TemporaryDirectory() as tmp:
            out = Path(tmp)
            item = {
                "id": "ref-31",
                "title": "Pending paper",
                "doi": "10.1000/pending",
                "url": None,
            }
            result = {
                "status": "pending",
                "pending_reason": "institutional_cap_reached",
            }
            path = store.write_pending_csv(out, [(item, result)])

            text = path.read_text(encoding="utf-8")
            self.assertIn("id,title,doi,url,pending_reason", text)
            self.assertIn("ref-31", text)
            self.assertIn("institutional_cap_reached", text)

    def test_second_main_run_skips_a_verified_state_file(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out = tmp_path / "papers"
            batch = tmp_path / "refs.txt"
            batch.write_text("10.1000/resume\n", encoding="utf-8")
            calls = []

            def fake_resolve(item, out_dir, timeout, overwrite, dry_run):
                calls.append(item["canonical_id"])
                filename = store.build_filename(
                    {"year": 2026, "first_author": "Test", "title": "Resume"},
                    "Resume",
                    item["canonical_id"],
                )
                target = out_dir / filename
                store.atomic_write_bytes(target, b"%PDF-1.7\nfixture")
                return {
                    "success": True,
                    "status": "downloaded",
                    "source": "fixture",
                    "file": str(target),
                    "meta": {
                        "doi": item["doi"],
                        "title": "Resume",
                        "source_id": item["id"],
                    },
                    "attempts": [{"source": "fixture", "result": "downloaded"}],
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
            with mock.patch.object(oa_fetch, "resolve_item", side_effect=fake_resolve):
                for _ in range(2):
                    with (
                        mock.patch.object(sys, "argv", argv),
                        redirect_stdout(StringIO()),
                        redirect_stderr(StringIO()),
                    ):
                        self.assertEqual(oa_fetch.main(), 0)

            results = json.loads((out / "oa_fetch_results.json").read_text(encoding="utf-8"))
            manifest_exists = (out / "oa_fetch_manifest.csv").is_file()

        self.assertEqual(calls, ["doi:10.1000/resume"])
        self.assertEqual(results[0]["status"], "exists")
        self.assertTrue(manifest_exists)


if __name__ == "__main__":
    unittest.main()
