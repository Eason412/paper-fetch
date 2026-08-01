from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import oa_fetch  # noqa: E402


class CliContractTests(unittest.TestCase):
    def test_dry_run_only_accepts_public_url_candidates(self):
        lookups = (
            mock.patch.object(oa_fetch, "openalex_lookup", return_value={}),
            mock.patch.object(oa_fetch, "semantic_scholar_lookup", return_value={}),
        )
        with TemporaryDirectory() as tmp, lookups[0], lookups[1]:
            output = Path(tmp)
            safe = oa_fetch.resolve_item(
                {"url": "https://example.org/paper.pdf", "id": "safe"},
                output,
                timeout=5,
                overwrite=False,
                dry_run=True,
            )
            unsafe = oa_fetch.resolve_item(
                {"url": "http://127.0.0.1/private.pdf", "id": "unsafe"},
                output,
                timeout=5,
                overwrite=False,
                dry_run=True,
            )

        self.assertTrue(safe["success"])
        self.assertEqual(safe["status"], "candidate")
        self.assertEqual(safe["pdf_url"], "https://example.org/paper.pdf")
        self.assertFalse(unsafe["success"])
        self.assertEqual(unsafe["status"], "failed")
        self.assertIsNone(unsafe["pdf_url"])
        self.assertEqual(unsafe["candidates"], [])

    def test_transport_attempt_returns_four_but_content_failure_returns_one(self):
        cases = (("network_TimeoutError", 4), ("read_OSError", 4), ("not_pdf", 1))
        for reason, expected_exit in cases:
            with self.subTest(reason=reason), TemporaryDirectory() as tmp:
                out = Path(tmp) / "out"
                result = {
                    "success": False,
                    "status": "failed",
                    "meta": {"doi": "10.1000/example"},
                    "attempts": [
                        {
                            "source": "direct",
                            "url": "https://example.org/paper.pdf",
                            "result": reason,
                        }
                    ],
                    "error": "no_open_access_pdf_downloaded",
                }
                stdout = StringIO()
                argv = [
                    "oa_fetch.py",
                    "--doi",
                    "10.1000/example",
                    "--out",
                    str(out),
                    "--oa-delay",
                    "0",
                ]
                with (
                    mock.patch.object(sys, "argv", argv),
                    mock.patch.object(oa_fetch, "resolve_item", return_value=result),
                    redirect_stdout(stdout),
                    redirect_stderr(StringIO()),
                ):
                    exit_code = oa_fetch.main()

                payload = json.loads(stdout.getvalue())
                self.assertFalse(payload["ok"])
                self.assertEqual(exit_code, expected_exit)

    def test_transport_classification_includes_institutional_read_errors(self):
        self.assertTrue(
            oa_fetch._result_has_transport_failure(
                {"institutional": {"error": "read_OSError"}}
            )
        )
        for reason in ("http_500", "not_pdf", "unsafe_redirect"):
            with self.subTest(reason=reason):
                self.assertFalse(
                    oa_fetch._result_has_transport_failure(
                        {"institutional": {"error": reason}}
                    )
                )
        self.assertFalse(
            oa_fetch._result_has_transport_failure(
                {
                    "success": True,
                    "attempts": [{"result": "network_TimeoutError"}],
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
