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
            profile = tmp_path / "profile"
            profile.mkdir()
            (profile / "session-marker").write_text("fixture", encoding="utf-8")
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
                "--browser-profile",
                str(profile),
                "--oa-delay",
                "0",
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

    def test_institutional_page_metadata_renames_download_and_updates_state(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out = tmp_path / "out"
            profile = tmp_path / "profile"
            profile.mkdir()
            (profile / "marker").write_text("fixture", encoding="utf-8")
            batch = tmp_path / "refs.txt"
            batch.write_text("10.1109/example\n", encoding="utf-8")
            page_meta = {
                "doi": "10.1109/example",
                "title": "An Accurate IEEE Paper Title",
                "year": 2025,
                "first_author": "Zhang",
                "source_id": "1",
            }

            def fake_fetch(items, **kwargs):
                provisional = Path(items[0]["dest"])
                provisional.write_bytes(b"%PDF-1.7\ninstitutional")
                return [{
                    "idx": items[0]["idx"],
                    "success": True,
                    "source": "institutional",
                    "file": str(provisional),
                    "pdf_url": "https://ieeexplore.ieee.org/stampPDF/final.pdf",
                    "meta": page_meta,
                }]

            argv = [
                "oa_fetch.py",
                "--batch",
                str(batch),
                "--out",
                str(out),
                "--institutional",
                "--browser-profile",
                str(profile),
                "--oa-delay",
                "0",
            ]
            failure = {
                "success": False,
                "status": "failed",
                "meta": {"doi": "10.1109/example"},
                "error": "no_open_access_pdf_downloaded",
            }
            stdout = StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(oa_fetch, "resolve_item", return_value=failure),
                mock.patch.object(institutional_fetch, "fetch_batch", side_effect=fake_fetch),
                redirect_stdout(stdout),
                redirect_stderr(StringIO()),
            ):
                self.assertEqual(oa_fetch.main(), 0)

            payload = __import__("json").loads(stdout.getvalue())
            result = payload["results"][0]
            final_path = Path(result["file"])
            expected_name = oa_fetch.metadata_filename(
                page_meta,
                page_meta["title"],
                "doi:10.1109/example",
            )
            state = __import__("json").loads(
                (out / "oa_fetch_state.json").read_text(encoding="utf-8")
            )
            final_exists = final_path.is_file()

        self.assertEqual(final_path.name, expected_name)
        self.assertTrue(final_path.name.startswith("2025_Zhang_An_Accurate_IEEE_Paper_Title"))
        self.assertTrue(final_exists)
        self.assertEqual(result["meta"]["title"], page_meta["title"])
        self.assertEqual(state["records"]["doi:10.1109/example"]["file"], expected_name)

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
                "--oa-delay",
                "0",
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

    def test_dry_run_preserves_an_existing_pending_manifest(self):
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            out.mkdir()
            pending = out / "oa_fetch_pending.csv"
            original = (
                "id,title,doi,url,pending_reason\n"
                "ref-1,Paper,10.1109/example,,institutional_cap_reached\n"
            )
            pending.write_text(original, encoding="utf-8")
            argv = [
                "oa_fetch.py",
                "--doi",
                "10.1109/preview",
                "--out",
                str(out),
                "--dry-run",
                "--oa-delay",
                "0",
            ]
            result = {
                "success": False,
                "status": "failed",
                "dry_run": True,
                "meta": {"doi": "10.1109/preview"},
                "error": "no_open_access_pdf_downloaded",
            }
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(oa_fetch, "resolve_item", return_value=result),
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()),
            ):
                exit_code = oa_fetch.main()

            self.assertEqual(exit_code, 1)
            self.assertEqual(pending.read_text(encoding="utf-8"), original)

    def test_missing_profile_becomes_pending_without_launching_playwright(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out = tmp_path / "out"
            missing_profile = tmp_path / "missing-profile"
            argv = [
                "oa_fetch.py",
                "--doi",
                "10.1109/failure",
                "--out",
                str(out),
                "--institutional",
                "--browser-profile",
                str(missing_profile),
                "--oa-delay",
                "0",
            ]
            result = {
                "success": False,
                "status": "failed",
                "meta": {"doi": "10.1109/failure", "title": "Failure"},
                "error": "no_open_access_pdf_downloaded",
            }
            stdout = StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(oa_fetch, "resolve_item", return_value=result),
                mock.patch.object(institutional_fetch, "fetch_batch") as fetch_batch,
                redirect_stdout(stdout),
                redirect_stderr(StringIO()),
            ):
                exit_code = oa_fetch.main()

            payload = __import__("json").loads(stdout.getvalue())

        self.assertEqual(exit_code, 1)
        fetch_batch.assert_not_called()
        self.assertEqual(payload["results"][0]["status"], "pending")
        self.assertEqual(
            payload["results"][0]["pending_reason"],
            "profile_missing_login_required",
        )
        self.assertIn("pending", payload["reports"])

    def test_oa_delay_is_applied_only_between_resolved_items(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            batch = tmp_path / "refs.txt"
            batch.write_text("First title\nSecond title\n", encoding="utf-8")
            argv = [
                "oa_fetch.py",
                "--batch",
                str(batch),
                "--out",
                str(tmp_path / "out"),
                "--oa-delay",
                "2",
                "--dry-run",
            ]
            result = {
                "success": False,
                "status": "failed",
                "meta": {},
                "error": "no_open_access_pdf_downloaded",
            }
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(oa_fetch, "resolve_item", return_value=result),
                mock.patch.object(oa_fetch.time, "sleep") as sleep,
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()),
            ):
                exit_code = oa_fetch.main()

        self.assertEqual(exit_code, 1)
        sleep.assert_called_once_with(2.0)

    def test_configured_institutional_fallback_can_be_overridden_by_oa_only(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile = tmp_path / "profile"
            profile.mkdir()
            (profile / "marker").write_text("fixture", encoding="utf-8")
            config_path = tmp_path / "config.json"
            config_path.write_text(
                __import__("json").dumps(
                    {
                        "version": 1,
                        "institutional": True,
                        "browser_profile": str(profile),
                    }
                ),
                encoding="utf-8",
            )
            base_argv = [
                "oa_fetch.py",
                "--doi",
                "10.1109/failure",
                "--out",
                str(tmp_path / "out"),
                "--config",
                str(config_path),
                "--oa-delay",
                "0",
            ]
            result = {
                "success": False,
                "status": "failed",
                "meta": {"doi": "10.1109/failure"},
                "error": "no_open_access_pdf_downloaded",
            }

            with (
                mock.patch.object(sys, "argv", base_argv),
                mock.patch.object(oa_fetch, "resolve_item", return_value=result),
                mock.patch.object(institutional_fetch, "fetch_batch", return_value=[]) as enabled,
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()),
            ):
                self.assertEqual(oa_fetch.main(), 1)
            with (
                mock.patch.object(sys, "argv", [*base_argv, "--oa-only"]),
                mock.patch.object(oa_fetch, "resolve_item", return_value=result),
                mock.patch.object(institutional_fetch, "fetch_batch") as disabled,
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()),
            ):
                self.assertEqual(oa_fetch.main(), 1)

        enabled.assert_called_once()
        disabled.assert_not_called()

    def test_institutional_overflow_is_reported_as_pending(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile = tmp_path / "profile"
            profile.mkdir()
            (profile / "marker").write_text("fixture", encoding="utf-8")
            batch = tmp_path / "refs.txt"
            batch.write_text(
                "\n".join(f"10.1109/item-{index}" for index in range(31)) + "\n",
                encoding="utf-8",
            )
            argv = [
                "oa_fetch.py",
                "--batch",
                str(batch),
                "--out",
                str(tmp_path / "out"),
                "--institutional",
                "--browser-profile",
                str(profile),
                "--oa-delay",
                "0",
            ]
            oa_failure = {
                "success": False,
                "status": "failed",
                "meta": {},
                "error": "no_open_access_pdf_downloaded",
            }

            def fake_fetch(items, **kwargs):
                return [
                    {
                        "idx": item["idx"],
                        "success": index < 30,
                        "source": "institutional" if index < 30 else None,
                        "file": item["dest"] if index < 30 else None,
                        "error": None if index < 30 else "institutional_cap_reached",
                    }
                    for index, item in enumerate(items)
                ]

            stdout = StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(oa_fetch, "resolve_item", return_value=oa_failure),
                mock.patch.object(institutional_fetch, "fetch_batch", side_effect=fake_fetch),
                redirect_stdout(stdout),
                redirect_stderr(StringIO()),
            ):
                exit_code = oa_fetch.main()

            payload = __import__("json").loads(stdout.getvalue())
            pending_text = Path(payload["reports"]["pending"]).read_text(encoding="utf-8")

        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["summary"]["pending"], 1)
        self.assertIn("institutional_cap_reached", pending_text)


if __name__ == "__main__":
    unittest.main()
