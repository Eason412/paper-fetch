from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
from contextlib import redirect_stderr
from io import StringIO
import sys
import unittest
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import oa_fetch  # noqa: E402


class OaUrlSafetyTests(unittest.TestCase):
    def test_safe_url_rejects_local_and_legacy_numeric_hosts(self):
        rejected = (
            "http://127.0.0.1/paper.pdf",
            "http://[::1]/paper.pdf",
            "http://2130706433/paper.pdf",
            "http://0177.0.0.1/paper.pdf",
            "http://0x7f.0.0.1/paper.pdf",
            "http://metadata.google.internal/paper.pdf",
            "https://example.org:8443/paper.pdf",
            "file:///tmp/paper.pdf",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(oa_fetch.safe_url(url))

        self.assertTrue(oa_fetch.safe_url("https://example.org/paper.pdf"))

    def test_redirect_handler_rejects_unsafe_target(self):
        handler = oa_fetch.SafeRedirectHandler()
        request = urllib.request.Request("https://example.org/paper.pdf")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "http://127.0.0.1/private.pdf",
            )
        raised.exception.close()

    def test_output_directory_error_returns_four(self):
        with TemporaryDirectory() as tmp:
            output_file = Path(tmp) / "not-a-directory"
            output_file.write_text("fixture", encoding="utf-8")
            argv = [
                "oa_fetch.py",
                "--doi",
                "10.1109/example",
                "--out",
                str(output_file),
                "--dry-run",
            ]
            with mock.patch.object(sys, "argv", argv), redirect_stderr(StringIO()):
                exit_code = oa_fetch.main()

        self.assertEqual(exit_code, 4)

    def test_report_write_error_returns_four(self):
        with TemporaryDirectory() as tmp:
            argv = [
                "oa_fetch.py",
                "--doi",
                "10.1109/example",
                "--out",
                str(Path(tmp) / "out"),
                "--dry-run",
            ]
            result = {
                "success": False,
                "dry_run": True,
                "meta": {"doi": "10.1109/example"},
            }
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(oa_fetch, "resolve_item", return_value=result),
                mock.patch.object(oa_fetch, "write_reports", side_effect=OSError("disk full")),
                redirect_stderr(StringIO()),
            ):
                exit_code = oa_fetch.main()

        self.assertEqual(exit_code, 4)


if __name__ == "__main__":
    unittest.main()
