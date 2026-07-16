from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
import csv
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import oa_fetch  # noqa: E402


class TitleResolutionTests(unittest.TestCase):
    def test_flat_report_exposes_title_and_publisher_decision_summary(self):
        result = {
            "success": False,
            "status": "pending",
            "pending_reason": "publisher_title_mismatch",
            "title_resolution": {
                "status": "confirmed",
                "reason": "exact_title",
                "selected_doi": "10.1109/example",
            },
            "meta": {
                "doi": "10.1109/example",
                "title": "Publisher title",
                "citation_title": "Publisher title",
                "publisher_title_match": False,
                "publisher_title_score": 0.2,
            },
        }
        with TemporaryDirectory() as tmp:
            out = Path(tmp)
            oa_fetch.write_reports([result], out)
            with (out / "oa_fetch_results.csv").open(encoding="utf-8") as handle:
                row = next(csv.DictReader(handle))

        self.assertEqual(row["title_resolution_status"], "confirmed")
        self.assertEqual(row["title_resolution_reason"], "exact_title")
        self.assertEqual(row["resolved_doi"], "10.1109/example")
        self.assertEqual(row["citation_title"], "Publisher title")
        self.assertEqual(row["publisher_title_match"], "False")
        self.assertEqual(row["publisher_title_score"], "0.2")

    def test_exact_single_source_title_is_not_enough(self):
        crossref = {
            "doi": "10.1109/EXAMPLE",
            "title": "A Reliable Paper Title",
            "score": 1.0,
            "year": 2024,
            "first_author": "Smith",
        }
        with (
            mock.patch.object(oa_fetch, "arxiv_title_lookup", return_value={}),
            mock.patch.object(oa_fetch, "crossref_title_to_doi", return_value=crossref),
            mock.patch.object(oa_fetch, "openalex_lookup", return_value={}),
        ):
            resolution = oa_fetch.resolve_title_identity(
                "A Reliable Paper Title", timeout=5
            )

        self.assertEqual(resolution["status"], "ambiguous")
        self.assertEqual(resolution["reason"], "insufficient_confirmation")
        self.assertIsNone(resolution["selected_doi"])
        self.assertIsNone(resolution["selected"])

    def test_two_exact_sources_confirm_the_same_doi(self):
        title = "A Reliable Paper Title"
        crossref = {
            "doi": "10.1109/EXAMPLE",
            "title": title,
            "score": 1.0,
        }
        openalex = {
            "doi": "https://doi.org/10.1109/example",
            "title": title,
            "score": 1.0,
        }
        with (
            mock.patch.object(oa_fetch, "arxiv_title_lookup", return_value={}),
            mock.patch.object(oa_fetch, "crossref_title_to_doi", return_value=crossref),
            mock.patch.object(oa_fetch, "openalex_lookup", return_value=openalex),
        ):
            resolution = oa_fetch.resolve_title_identity(title, timeout=5)

        self.assertEqual(resolution["status"], "confirmed")
        self.assertEqual(resolution["reason"], "exact_title")
        self.assertEqual(resolution["selected_doi"], "10.1109/example")
        self.assertEqual(resolution["selected"]["title"], title)

    def test_two_sources_must_agree_on_the_same_doi(self):
        crossref = {
            "doi": "10.1002/example",
            "title": "Robust Learning for Industrial Signals",
            "score": 0.91,
        }
        openalex = {
            "doi": "https://doi.org/10.1002/EXAMPLE",
            "title": "Robust Learning of Industrial Signals",
            "score": 0.89,
        }
        with (
            mock.patch.object(oa_fetch, "arxiv_title_lookup", return_value={}),
            mock.patch.object(oa_fetch, "crossref_title_to_doi", return_value=crossref),
            mock.patch.object(oa_fetch, "openalex_lookup", return_value=openalex),
        ):
            resolution = oa_fetch.resolve_title_identity(
                "Robust Learning with Industrial Signals", timeout=5
            )

        self.assertEqual(resolution["status"], "confirmed")
        self.assertEqual(resolution["reason"], "multiple_sources_same_doi")
        self.assertEqual(resolution["selected_doi"], "10.1002/example")

    def test_conflicting_high_confidence_dois_are_ambiguous(self):
        with (
            mock.patch.object(oa_fetch, "arxiv_title_lookup", return_value={}),
            mock.patch.object(
                oa_fetch,
                "crossref_title_to_doi",
                return_value={
                    "doi": "10.1109/one",
                    "title": "The Same Exact Title",
                    "score": 1.0,
                },
            ),
            mock.patch.object(
                oa_fetch,
                "openalex_lookup",
                return_value={
                    "doi": "10.1002/two",
                    "title": "The Same Exact Title",
                    "score": 1.0,
                },
            ),
        ):
            resolution = oa_fetch.resolve_title_identity(
                "The Same Exact Title", timeout=5
            )

        self.assertEqual(resolution["status"], "ambiguous")
        self.assertEqual(resolution["reason"], "conflicting_dois")
        self.assertIsNone(resolution["selected_doi"])
        self.assertEqual(
            {candidate["doi"] for candidate in resolution["candidates"]},
            {"10.1109/one", "10.1002/two"},
        )

    def test_exact_arxiv_alias_does_not_conflict_with_corroborated_publisher_doi(self):
        title = "A Shared Preprint and Publisher Title"
        with (
            mock.patch.object(
                oa_fetch,
                "arxiv_title_lookup",
                return_value={
                    "doi": "10.48550/arXiv.2401.00001",
                    "title": title,
                    "score": 1.0,
                    "urls": ["https://arxiv.org/pdf/2401.00001.pdf"],
                },
            ),
            mock.patch.object(
                oa_fetch,
                "crossref_title_to_doi",
                return_value={
                    "doi": "10.1109/publisher",
                    "title": title,
                    "score": 1.0,
                },
            ),
            mock.patch.object(
                oa_fetch,
                "openalex_lookup",
                return_value={
                    "doi": "https://doi.org/10.1109/PUBLISHER",
                    "title": title,
                    "score": 1.0,
                },
            ),
        ):
            resolution = oa_fetch.resolve_title_identity(title, timeout=5)

        self.assertEqual(resolution["status"], "confirmed")
        self.assertEqual(resolution["selected_doi"], "10.1109/publisher")
        self.assertEqual(
            resolution["accepted_alias_dois"],
            ["10.48550/arxiv.2401.00001"],
        )

    def test_one_publisher_source_cannot_override_an_arxiv_doi(self):
        title = "A Shared Preprint and Publisher Title"
        with (
            mock.patch.object(
                oa_fetch,
                "arxiv_title_lookup",
                return_value={
                    "doi": "10.48550/arXiv.2401.00001",
                    "title": title,
                    "score": 1.0,
                },
            ),
            mock.patch.object(
                oa_fetch,
                "crossref_title_to_doi",
                return_value={
                    "doi": "10.1109/publisher",
                    "title": title,
                    "score": 1.0,
                },
            ),
            mock.patch.object(oa_fetch, "openalex_lookup", return_value={}),
        ):
            resolution = oa_fetch.resolve_title_identity(title, timeout=5)

        self.assertEqual(resolution["status"], "ambiguous")
        self.assertEqual(resolution["reason"], "conflicting_dois")
        self.assertIsNone(resolution["selected_doi"])

    def test_arxiv_alias_does_not_mask_two_publisher_doi_conflicts(self):
        title = "A Conflicting Publisher Title"
        with (
            mock.patch.object(
                oa_fetch,
                "arxiv_title_lookup",
                return_value={
                    "doi": "10.48550/arXiv.2401.00001",
                    "title": title,
                    "score": 1.0,
                },
            ),
            mock.patch.object(
                oa_fetch,
                "crossref_title_to_doi",
                return_value={
                    "doi": "10.1109/one",
                    "title": title,
                    "score": 1.0,
                },
            ),
            mock.patch.object(
                oa_fetch,
                "openalex_lookup",
                return_value={
                    "doi": "10.1002/two",
                    "title": title,
                    "score": 1.0,
                },
            ),
        ):
            resolution = oa_fetch.resolve_title_identity(title, timeout=5)

        self.assertEqual(resolution["status"], "ambiguous")
        self.assertEqual(resolution["reason"], "conflicting_dois")
        self.assertIsNone(resolution["selected_doi"])

    def test_single_non_exact_candidate_is_not_enough(self):
        with (
            mock.patch.object(oa_fetch, "arxiv_title_lookup", return_value={}),
            mock.patch.object(
                oa_fetch,
                "crossref_title_to_doi",
                return_value={
                    "doi": "10.1016/example",
                    "title": "A Similar but Different Article Title",
                    "score": 0.94,
                },
            ),
            mock.patch.object(oa_fetch, "openalex_lookup", return_value={}),
        ):
            resolution = oa_fetch.resolve_title_identity(
                "A Similar Article Title", timeout=5
            )

        self.assertEqual(resolution["status"], "ambiguous")
        self.assertEqual(resolution["reason"], "insufficient_confirmation")
        self.assertIsNone(resolution["selected_doi"])

    def test_no_title_candidates_is_unresolved(self):
        with (
            mock.patch.object(oa_fetch, "arxiv_title_lookup", return_value={}),
            mock.patch.object(oa_fetch, "crossref_title_to_doi", return_value={}),
            mock.patch.object(oa_fetch, "openalex_lookup", return_value={}),
        ):
            resolution = oa_fetch.resolve_title_identity(
                "A Title Missing From Every Index", timeout=5
            )

        self.assertEqual(resolution["status"], "unresolved")
        self.assertEqual(resolution["reason"], "no_candidates")
        self.assertEqual(resolution["candidates"], [])

    def test_confirmed_title_doi_survives_an_oa_miss_for_institutional_retry(self):
        item = {
            "id": "ref-1",
            "title": "A Confirmed Publisher Paper",
            "doi": None,
            "url": None,
            "canonical_id": "title:a confirmed publisher paper:deadbeef",
        }
        selected = {
            "source": "crossref_title",
            "doi": "10.1109/example",
            "title": "A Confirmed Publisher Paper",
            "score": 1.0,
            "year": 2025,
            "first_author": "Smith",
            "urls": [],
        }
        resolution = {
            "status": "confirmed",
            "reason": "exact_title",
            "selected_doi": "10.1109/example",
            "selected": selected,
            "candidates": [selected],
            "lookups": [],
        }
        with (
            mock.patch.object(
                oa_fetch, "resolve_title_identity", return_value=resolution
            ),
            mock.patch.object(oa_fetch, "openalex_lookup", return_value={}),
            mock.patch.object(oa_fetch, "unpaywall_lookup", return_value={}),
            mock.patch.object(oa_fetch, "semantic_scholar_lookup", return_value={}),
        ):
            result = oa_fetch.resolve_item(
                item, Path("/tmp/papers"), 5, False, False
            )

        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["meta"]["doi"], "10.1109/example")
        self.assertEqual(result["title_resolution"]["status"], "confirmed")
        self.assertEqual(result["error"], "no_open_access_pdf_downloaded")

    def test_confirmed_arxiv_alias_remains_an_oa_candidate(self):
        item = {
            "id": "ref-1",
            "title": "A Confirmed Publisher Paper",
            "doi": None,
            "url": None,
            "canonical_id": "title:a confirmed publisher paper:deadbeef",
        }
        selected = {
            "source": "crossref_title",
            "doi": "10.1109/example",
            "title": "A Confirmed Publisher Paper",
            "score": 1.0,
            "urls": [],
        }
        alias = {
            "source": "arxiv_title",
            "doi": "10.48550/arxiv.2401.00001",
            "title": "A Confirmed Publisher Paper",
            "score": 1.0,
            "urls": ["https://arxiv.org/pdf/2401.00001.pdf"],
        }
        resolution = {
            "status": "confirmed",
            "reason": "exact_title",
            "selected_doi": "10.1109/example",
            "accepted_alias_dois": ["10.48550/arxiv.2401.00001"],
            "selected": selected,
            "candidates": [selected, alias],
            "lookups": [],
        }
        with (
            mock.patch.object(
                oa_fetch, "resolve_title_identity", return_value=resolution
            ),
            mock.patch.object(oa_fetch, "download_pdf", return_value=(True, "downloaded")) as download_pdf,
            mock.patch.object(oa_fetch, "openalex_lookup", return_value={}),
            mock.patch.object(oa_fetch, "unpaywall_lookup", return_value={}),
            mock.patch.object(oa_fetch, "semantic_scholar_lookup", return_value={}),
        ):
            result = oa_fetch.resolve_item(
                item, Path("/tmp/papers"), 5, False, False
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "arxiv_title")
        self.assertEqual(
            download_pdf.call_args.args[0],
            "https://arxiv.org/pdf/2401.00001.pdf",
        )

    def test_ambiguous_title_never_attempts_a_pdf_download(self):
        item = {
            "id": "ref-1",
            "title": "Ambiguous Paper Title",
            "doi": None,
            "url": None,
            "canonical_id": "title:ambiguous paper title:deadbeef",
        }
        resolution = {
            "status": "ambiguous",
            "reason": "conflicting_dois",
            "selected_doi": None,
            "selected": None,
            "candidates": [
                {
                    "source": "crossref_title",
                    "doi": "10.1109/one",
                    "title": "Ambiguous Paper Title",
                    "score": 1.0,
                },
                {
                    "source": "openalex_title",
                    "doi": "10.1002/two",
                    "title": "Ambiguous Paper Title",
                    "score": 1.0,
                },
            ],
        }
        with (
            mock.patch.object(
                oa_fetch, "resolve_title_identity", return_value=resolution
            ),
            mock.patch.object(oa_fetch, "download_pdf") as download_pdf,
        ):
            result = oa_fetch.resolve_item(
                item, Path("/tmp/papers"), 5, False, False
            )

        download_pdf.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["pending_reason"], "title_resolution_ambiguous")
        self.assertIsNone(result["meta"]["doi"])
        self.assertEqual(result["title_resolution"], resolution)


if __name__ == "__main__":
    unittest.main()
