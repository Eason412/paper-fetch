from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import institutional_fetch  # noqa: E402
import oa_fetch  # noqa: E402


class OaFirstTests(unittest.TestCase):
    def test_only_oa_failures_enter_institutional_retry(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            batch = tmp_path / "dois.txt"
            batch.write_text("10.1109/success\n10.1002/failure\n", encoding="utf-8")
            out = tmp_path / "out"
            captured = []

            def fake_resolve(item, out_dir, timeout, overwrite, dry_run):
                success = item["doi"].endswith("success")
                return {
                    "success": success,
                    "source": "openalex" if success else None,
                    "file": str(out_dir / "success.pdf") if success else None,
                    "meta": {"doi": item["doi"], "title": item.get("title")},
                    "error": None if success else "no_open_access_pdf_downloaded",
                }

            def fake_fetch_batch(items, **kwargs):
                captured.extend(items)
                return []

            argv = [
                "oa_fetch.py",
                "--batch",
                str(batch),
                "--out",
                str(out),
                "--institutional",
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(oa_fetch, "resolve_item", side_effect=fake_resolve),
                mock.patch.object(institutional_fetch, "fetch_batch", side_effect=fake_fetch_batch),
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()),
            ):
                exit_code = oa_fetch.main()

        self.assertEqual(exit_code, 1)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["doi"], "10.1002/failure")
        self.assertEqual(captured[0]["idx"], 1)

    def test_dry_run_never_enters_institutional_retry(self):
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            argv = [
                "oa_fetch.py",
                "--doi",
                "10.1109/failure",
                "--out",
                str(out),
                "--institutional",
                "--dry-run",
            ]
            result = {
                "success": False,
                "dry_run": True,
                "meta": {"doi": "10.1109/failure"},
                "error": "no_open_access_pdf_downloaded",
            }
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(oa_fetch, "resolve_item", return_value=result),
                mock.patch.object(institutional_fetch, "fetch_batch") as fetch_batch,
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()),
            ):
                exit_code = oa_fetch.main()

        self.assertEqual(exit_code, 1)
        fetch_batch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
