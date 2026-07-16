from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
from contextlib import redirect_stdout
from io import StringIO
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import institutional_fetch  # noqa: E402


class FakePage:
    def __init__(self, page_url, citation_url=None, metadata=None):
        self.url = page_url
        self.citation_url = citation_url
        self.metadata = metadata or {}

    def get_attribute(self, selector, attribute, timeout):
        self.last_meta_request = (selector, attribute, timeout)
        if selector == 'meta[name="citation_pdf_url"]':
            return self.citation_url
        return self.metadata.get(selector)


class FakeRequest:
    def __init__(self):
        self.calls = []

    def get(self, url, timeout, max_redirects=0):
        self.calls.append((url, timeout, max_redirects))
        raise AssertionError("request.get must not be called for a blocked URL")


class FakeContext:
    def __init__(self):
        self.request = FakeRequest()


class InstitutionalBoundaryTests(unittest.TestCase):
    def test_publisher_title_evidence_normalizes_punctuation_and_case(self):
        evidence = institutional_fetch._publisher_title_evidence(
            "A Study: On Reliable Signals",
            "a study on reliable signals",
        )

        self.assertTrue(evidence["match"])
        self.assertEqual(evidence["score"], 1.0)

    def test_publisher_title_evidence_rejects_a_different_title(self):
        evidence = institutional_fetch._publisher_title_evidence(
            "Expected Paper Title",
            "A Completely Different Article",
        )

        self.assertFalse(evidence["match"])
        self.assertLess(evidence["score"], institutional_fetch.PUBLISHER_TITLE_MIN_SCORE)

    def test_only_three_supported_publisher_hosts_are_recognized(self):
        accepted = {
            "https://ieeexplore.ieee.org/document/1": "ieee",
            "https://www.sciencedirect.com/science/article/pii/X": "elsevier",
            "https://onlinelibrary.wiley.com/doi/10.1002/x": "wiley",
        }
        for url, expected in accepted.items():
            with self.subTest(url=url):
                self.assertEqual(institutional_fetch._publisher_from_url(url), expected)

        rejected = (
            "http://ieeexplore.ieee.org/document/1",
            "https://ieeexplore.ieee.org.evil.example/document/1",
            "https://sciencedirect.com.evil.example/article/1",
            "https://onlinelibrary.wiley.com.evil.example/doi/1",
            "https://example.org/paper",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertIsNone(institutional_fetch._publisher_from_url(url))

    def test_initial_navigation_is_limited_to_doi_and_supported_publishers(self):
        accepted = (
            "https://doi.org/10.1109/example",
            "https://dx.doi.org/10.1002/example",
            "https://ieeexplore.ieee.org/document/1",
            "https://www.sciencedirect.com/science/article/pii/X",
            "https://onlinelibrary.wiley.com/doi/10.1002/x",
        )
        for url in accepted:
            with self.subTest(url=url):
                self.assertTrue(institutional_fetch._allowed_landing_url(url))

        rejected = (
            "http://doi.org/10.1109/example",
            "https://doi.org.evil.example/10.1109/example",
            "https://example.org/paper",
            "file:///tmp/paper.pdf",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(institutional_fetch._allowed_landing_url(url))

    def test_citation_pdf_must_belong_to_the_same_supported_publisher(self):
        page = FakePage(
            "https://ieeexplore.ieee.org/document/1",
            "https://evil.example/paper.pdf",
        )
        pdf_url, error = institutional_fetch._pdf_url_from_page(page, None, "ieee")
        self.assertIsNone(pdf_url)
        self.assertEqual(error, "unsafe_pdf_url")

        page.citation_url = "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?arnumber=1"
        pdf_url, error = institutional_fetch._pdf_url_from_page(page, None, "ieee")
        self.assertEqual(pdf_url, page.citation_url)
        self.assertIsNone(error)

    def test_three_publishers_share_accurate_citation_metadata_contract(self):
        pages = (
            "https://ieeexplore.ieee.org/document/123",
            "https://www.sciencedirect.com/science/article/pii/S123",
            "https://onlinelibrary.wiley.com/doi/10.1002/example",
        )
        metadata = {
            'meta[name="citation_title"]': "  A Canonical   Paper Title ",
            'meta[name="citation_author"]': "Smith, Jane",
            'meta[name="citation_publication_date"]': "2024/03/02",
            'meta[name="citation_doi"]': "https://doi.org/10.1000/Example",
        }
        for page_url in pages:
            with self.subTest(page_url=page_url):
                meta = institutional_fetch._citation_metadata(
                    FakePage(page_url, metadata=metadata),
                    {"id": "ref-1", "url": page_url},
                )
                self.assertEqual(meta["title"], "A Canonical Paper Title")
                self.assertEqual(meta["first_author"], "Smith")
                self.assertEqual(meta["year"], 2024)
                self.assertEqual(meta["doi"], "10.1000/example")

    def test_citation_doi_conflict_preserves_input_identity(self):
        page = FakePage(
            "https://ieeexplore.ieee.org/document/123",
            metadata={
                'meta[name="citation_doi"]': "10.1000/page",
                'meta[name="citation_title"]': "Publisher title",
            },
        )
        meta = institutional_fetch._citation_metadata(
            page,
            {"doi": "10.1000/input", "title": "Input title"},
        )

        self.assertEqual(meta["doi"], "10.1000/input")
        self.assertEqual(meta["title"], "Publisher title")
        self.assertEqual(meta["metadata_conflicts"]["citation_doi"], "10.1000/page")

    def test_missing_or_broken_citation_tags_do_not_block_fallback_metadata(self):
        class BrokenPage:
            def get_attribute(self, *args, **kwargs):
                raise RuntimeError("challenge page")

        meta = institutional_fetch._citation_metadata(
            BrokenPage(),
            {
                "doi": "10.1000/input",
                "title": "Known title",
                "year": 2023,
                "first_author": "Known",
            },
        )

        self.assertEqual(meta["title"], "Known title")
        self.assertEqual(meta["year"], 2023)
        self.assertEqual(meta["first_author"], "Known")

    def test_publisher_title_mismatch_stops_before_pdf_resolution(self):
        class PlaywrightManager:
            def __enter__(self):
                return object()

            def __exit__(self, exc_type, exc, tb):
                return False

        class Page:
            url = "https://ieeexplore.ieee.org/document/123"

            def goto(self, *args, **kwargs):
                return None

            def wait_for_timeout(self, milliseconds):
                return None

            def get_attribute(self, selector, attribute, timeout):
                values = {
                    'meta[name="citation_title"]': "Different Publisher Paper",
                    'meta[name="citation_doi"]': "10.1109/example",
                }
                return values.get(selector)

            def close(self):
                return None

        class Context:
            def new_page(self):
                return Page()

            def close(self):
                return None

        with TemporaryDirectory() as tmp:
            item = {
                "idx": 0,
                "id": "ref-1",
                "doi": "10.1109/example",
                "title": "Expected Paper Title",
                "expected_title": "Expected Paper Title",
                "dest": str(Path(tmp) / "paper.pdf"),
            }
            with (
                mock.patch.object(
                    institutional_fetch,
                    "_load_playwright",
                    return_value=lambda: PlaywrightManager(),
                ),
                mock.patch.object(
                    institutional_fetch, "_launch", return_value=Context()
                ),
                mock.patch.object(
                    institutional_fetch, "_pdf_url_from_page"
                ) as pdf_url,
                mock.patch.object(institutional_fetch, "_download") as download,
                redirect_stdout(StringIO()),
            ):
                results = institutional_fetch.fetch_batch(
                    [item],
                    profile_dir=str(Path(tmp) / "profile"),
                    delay=4,
                    jitter=0,
                )

        pdf_url.assert_not_called()
        download.assert_not_called()
        self.assertEqual(results[0]["error"], "publisher_title_mismatch")
        self.assertFalse(results[0]["meta"]["publisher_title_match"])
        self.assertEqual(
            results[0]["meta"]["citation_title"], "Different Publisher Paper"
        )

    def test_missing_publisher_title_stops_when_expected_title_is_known(self):
        class PlaywrightManager:
            def __enter__(self):
                return object()

            def __exit__(self, exc_type, exc, tb):
                return False

        class Page:
            url = "https://ieeexplore.ieee.org/document/123"

            def goto(self, *args, **kwargs):
                return None

            def wait_for_timeout(self, milliseconds):
                return None

            def get_attribute(self, selector, attribute, timeout):
                if selector == 'meta[name="citation_doi"]':
                    return "10.1109/example"
                return None

            def close(self):
                return None

        class Context:
            def new_page(self):
                return Page()

            def close(self):
                return None

        with TemporaryDirectory() as tmp:
            item = {
                "idx": 0,
                "id": "ref-1",
                "doi": "10.1109/example",
                "title": "Expected Paper Title",
                "expected_title": "Expected Paper Title",
                "dest": str(Path(tmp) / "paper.pdf"),
            }
            with (
                mock.patch.object(
                    institutional_fetch,
                    "_load_playwright",
                    return_value=lambda: PlaywrightManager(),
                ),
                mock.patch.object(
                    institutional_fetch, "_launch", return_value=Context()
                ),
                mock.patch.object(
                    institutional_fetch, "_pdf_url_from_page"
                ) as pdf_url,
                mock.patch.object(institutional_fetch, "_download") as download,
                redirect_stdout(StringIO()),
            ):
                results = institutional_fetch.fetch_batch(
                    [item],
                    profile_dir=str(Path(tmp) / "profile"),
                    delay=4,
                    jitter=0,
                )

        pdf_url.assert_not_called()
        download.assert_not_called()
        self.assertEqual(results[0]["error"], "publisher_title_unverifiable")
        self.assertEqual(
            results[0]["meta"]["expected_title"], "Expected Paper Title"
        )
        self.assertNotIn("citation_title", results[0]["meta"])
        self.assertIsNone(results[0]["meta"]["publisher_title_match"])

    def test_explicit_doi_without_expected_title_skips_publisher_title_guard(self):
        class PlaywrightManager:
            def __enter__(self):
                return object()

            def __exit__(self, exc_type, exc, tb):
                return False

        class Page:
            url = "https://ieeexplore.ieee.org/document/123"

            def goto(self, *args, **kwargs):
                return None

            def wait_for_timeout(self, milliseconds):
                return None

            def get_attribute(self, selector, attribute, timeout):
                if selector == 'meta[name="citation_title"]':
                    return "Publisher Canonical Title"
                return None

            def close(self):
                return None

        class Context:
            def new_page(self):
                return Page()

            def close(self):
                return None

        with TemporaryDirectory() as tmp:
            item = {
                "idx": 0,
                "id": "ref-1",
                "doi": "10.1109/example",
                "title": "Metadata Title",
                "dest": str(Path(tmp) / "paper.pdf"),
            }
            with (
                mock.patch.object(
                    institutional_fetch,
                    "_load_playwright",
                    return_value=lambda: PlaywrightManager(),
                ),
                mock.patch.object(
                    institutional_fetch, "_launch", return_value=Context()
                ),
                mock.patch.object(
                    institutional_fetch,
                    "_pdf_url_from_page",
                    return_value=("https://ieeexplore.ieee.org/paper.pdf", None),
                ),
                mock.patch.object(
                    institutional_fetch, "_download", return_value=(True, "downloaded")
                ) as download,
                redirect_stdout(StringIO()),
            ):
                results = institutional_fetch.fetch_batch(
                    [item],
                    profile_dir=str(Path(tmp) / "profile"),
                    delay=4,
                    jitter=0,
                )

        download.assert_called_once()
        self.assertTrue(results[0]["success"])
        self.assertIsNone(results[0]["meta"]["publisher_title_match"])

    def test_download_guard_blocks_an_unsupported_host_before_request(self):
        ctx = FakeContext()
        with TemporaryDirectory() as tmp:
            ok, reason = institutional_fetch._download(
                ctx,
                "https://evil.example/paper.pdf",
                Path(tmp) / "paper.pdf",
                timeout=5,
                publisher="ieee",
            )
        self.assertFalse(ok)
        self.assertEqual(reason, "unsafe_pdf_url")
        self.assertEqual(ctx.request.calls, [])

    def test_download_guard_blocks_cross_publisher_redirect(self):
        class RedirectResponse:
            status = 302
            ok = False
            url = "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?arnumber=1"
            headers = {"location": "https://evil.example/paper.pdf"}

        class RedirectRequest:
            def __init__(self):
                self.calls = []

            def get(self, url, timeout, max_redirects):
                self.calls.append((url, timeout, max_redirects))
                return RedirectResponse()

        ctx = FakeContext()
        ctx.request = RedirectRequest()
        with TemporaryDirectory() as tmp:
            ok, reason = institutional_fetch._download(
                ctx,
                "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?arnumber=1",
                Path(tmp) / "paper.pdf",
                timeout=5,
                publisher="ieee",
            )
        self.assertFalse(ok)
        self.assertEqual(reason, "unsafe_pdf_url")
        self.assertEqual(len(ctx.request.calls), 1)
        self.assertEqual(ctx.request.calls[0][2], 0)

    def test_download_follows_same_publisher_redirect_and_writes_pdf(self):
        class Response:
            def __init__(self, status, url, headers=None, body=b""):
                self.status = status
                self.url = url
                self.headers = headers or {}
                self.ok = 200 <= status < 300
                self._body = body

            def body(self):
                return self._body

        class Request:
            def __init__(self):
                self.responses = [
                    Response(
                        302,
                        "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?arnumber=1",
                        {"Location": "/stampPDF/final.pdf"},
                    ),
                    Response(
                        200,
                        "https://ieeexplore.ieee.org/stampPDF/final.pdf",
                        body=b"%PDF-1.7\nfixture",
                    ),
                ]

            def get(self, url, timeout, max_redirects):
                return self.responses.pop(0)

        ctx = FakeContext()
        ctx.request = Request()
        with TemporaryDirectory() as tmp:
            dest = Path(tmp) / "paper.pdf"
            ok, reason = institutional_fetch._download(
                ctx,
                "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?arnumber=1",
                dest,
                timeout=5,
                publisher="ieee",
            )
            self.assertEqual(dest.read_bytes(), b"%PDF-1.7\nfixture")
        self.assertTrue(ok)
        self.assertEqual(reason, "downloaded")

    def test_fetch_api_rejects_unsafe_throttle_values_before_playwright(self):
        invalid = (
            {"delay": 0},
            {"delay": 3.99},
            {"delay": float("nan")},
            {"delay": float("inf")},
            {"jitter": -1},
            {"jitter": 11},
            {"jitter": float("nan")},
            {"jitter": float("inf")},
            {"max_items": 0},
            {"max_items": 31},
        )
        for override in invalid:
            kwargs = {
                "profile_dir": "/tmp/unused-paper-fetch-profile",
                "delay": 4.0,
                "jitter": 3.0,
                "max_items": 30,
            }
            kwargs.update(override)
            with self.subTest(override=override):
                with self.assertRaises(ValueError):
                    institutional_fetch.fetch_batch([{"dest": "/tmp/unused.pdf"}], **kwargs)

    def test_cli_rejects_zero_institutional_delay(self):
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "oa_fetch.py"),
                "--doi",
                "10.1109/example",
                "--institutional",
                "--inst-delay",
                "0",
                "--dry-run",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--inst-delay", proc.stderr)

    def test_cap_returns_one_result_for_every_input(self):
        class PlaywrightManager:
            def __enter__(self):
                return object()

            def __exit__(self, exc_type, exc, tb):
                return False

        class Context:
            def close(self):
                pass

        with TemporaryDirectory() as tmp:
            existing = Path(tmp) / "existing.pdf"
            existing.write_bytes(b"%PDF-1.7\nfixture")
            items = [
                {"idx": idx, "doi": f"10.1109/{idx}", "dest": str(existing)}
                for idx in range(31)
            ]
            with (
                mock.patch.object(
                    institutional_fetch,
                    "_load_playwright",
                    return_value=lambda: PlaywrightManager(),
                ),
                mock.patch.object(institutional_fetch, "_launch", return_value=Context()),
                redirect_stdout(StringIO()),
            ):
                results = institutional_fetch.fetch_batch(
                    items,
                    profile_dir=str(Path(tmp) / "profile"),
                    delay=4,
                    jitter=0,
                    max_items=30,
                )

        self.assertEqual(len(results), len(items))
        self.assertEqual(results[-1]["idx"], 30)
        self.assertEqual(results[-1]["error"], "institutional_cap_reached")

    def test_non_successes_do_not_reset_the_block_streak(self):
        class PlaywrightManager:
            def __enter__(self):
                return object()

            def __exit__(self, exc_type, exc, tb):
                return False

        class Page:
            url = "https://ieeexplore.ieee.org/document/1"

            def goto(self, *args, **kwargs):
                return None

            def wait_for_timeout(self, milliseconds):
                return None

            def close(self):
                return None

        class Context:
            def new_page(self):
                return Page()

            def close(self):
                return None

        with TemporaryDirectory() as tmp:
            items = [
                {
                    "idx": index,
                    "doi": f"10.1109/{index}",
                    "dest": str(Path(tmp) / f"paper-{index}.pdf"),
                }
                for index in range(5)
            ]
            pdf_results = [
                ("https://ieeexplore.ieee.org/one.pdf", None),
                (None, "no_pdf_link_found"),
                ("https://ieeexplore.ieee.org/three.pdf", None),
                ("https://ieeexplore.ieee.org/four.pdf", None),
            ]
            download_results = [
                (False, "http_403"),
                (False, "http_403"),
                (False, "not_pdf_login_or_challenge"),
            ]
            with (
                mock.patch.object(
                    institutional_fetch,
                    "_load_playwright",
                    return_value=lambda: PlaywrightManager(),
                ),
                mock.patch.object(institutional_fetch, "_launch", return_value=Context()),
                mock.patch.object(
                    institutional_fetch,
                    "_pdf_url_from_page",
                    side_effect=pdf_results,
                ),
                mock.patch.object(
                    institutional_fetch,
                    "_download",
                    side_effect=download_results,
                ) as download,
                mock.patch.object(institutional_fetch.time, "sleep"),
                redirect_stdout(StringIO()),
            ):
                results = institutional_fetch.fetch_batch(
                    items,
                    profile_dir=str(Path(tmp) / "profile"),
                    delay=4,
                    jitter=0,
                    max_items=30,
                )

        self.assertEqual(len(results), 5)
        self.assertEqual(download.call_count, 3)
        self.assertEqual(results[-1]["idx"], 4)
        self.assertEqual(results[-1]["error"], "aborted_after_repeated_blocks")


if __name__ == "__main__":
    unittest.main()
