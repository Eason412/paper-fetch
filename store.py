"""Durable local storage helpers for paper-fetch.

This module owns collision-safe filenames, verified PDF reuse, atomic writes,
run state, and the continuation manifest for pending institutional items.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


STATE_VERSION = 1
NAMING_VERSION = 1
STATE_FILENAME = "oa_fetch_state.json"
PENDING_FILENAME = "oa_fetch_pending.csv"
MAX_FILENAME_BYTES = 240


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def atomic_write_bytes(path: Path, data: bytes, *, mode: int | None = None) -> None:
    """Write bytes beside the destination and atomically replace it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f"{path.name}.part-",
            dir=path.parent,
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            temp_path.chmod(mode)
        os.replace(temp_path, path)
        temp_path = None
        if mode is not None:
            path.chmod(mode)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def atomic_write_text(
    path: Path, text: str, *, encoding: str = "utf-8", mode: int | None = None
) -> None:
    atomic_write_bytes(Path(path), text.encode(encoding), mode=mode)


def atomic_write_json(path: Path, payload: Any, *, mode: int | None = None) -> None:
    atomic_write_text(
        Path(path),
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        mode=mode,
    )


def atomic_write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict]) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    atomic_write_text(Path(path), buffer.getvalue())


def verify_pdf(path: Path) -> bool:
    path = Path(path)
    try:
        if not path.is_file() or path.stat().st_size <= 5:
            return False
        with path.open("rb") as handle:
            return handle.read(4) == b"%PDF"
    except OSError:
        return False


def prepare_pdf_migration(source: Path, target: Path) -> None:
    """Create a verified, non-overwriting hard link at ``target``.

    The original remains in place until the caller has durably updated state.
    A pre-existing target is accepted only when it is already the same file,
    which also makes interrupted migrations safe to resume.
    """
    source = Path(source)
    target = Path(target)
    if source == target:
        return
    if not verify_pdf(source):
        raise OSError(f"migration source is not a valid PDF: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        try:
            same_file = source.samefile(target)
        except OSError:
            same_file = False
        if same_file and verify_pdf(target):
            return
        raise FileExistsError(f"refusing to overwrite existing target: {target}")
    try:
        os.link(source, target)
        if not verify_pdf(target):
            raise OSError(f"migration target is not a valid PDF: {target}")
    except Exception:
        try:
            if target.exists() and source.exists() and source.samefile(target):
                target.unlink()
        except OSError:
            pass
        raise


def finish_pdf_migration(source: Path, target: Path) -> None:
    """Remove the old name after state points at the verified new name."""
    source = Path(source)
    target = Path(target)
    if source == target or not source.exists():
        return
    if not verify_pdf(source) or not verify_pdf(target):
        raise OSError("cannot finish migration without two valid PDF links")
    try:
        same_file = source.samefile(target)
    except OSError as exc:
        raise OSError("cannot verify PDF migration identity") from exc
    if not same_file:
        raise OSError("refusing to unlink a migration source with different content")
    source.unlink()


def rollback_pdf_migration(source: Path, target: Path) -> None:
    """Discard the new link after a failed state write, preserving ``source``."""
    source = Path(source)
    target = Path(target)
    if source == target or not target.exists() or not source.exists():
        return
    try:
        if source.samefile(target):
            target.unlink()
    except OSError:
        return


def _clean_stem(text: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", text)
    text = re.sub(r"\s+", "_", text.strip())
    text = re.sub(r"_+", "_", text).strip("._")
    return text or "paper"


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", "ignore").rstrip("._") or "paper"


def build_filename(meta: dict, fallback: str, canonical_id: str) -> str:
    year = str(meta.get("year") or "unknown")
    title = str(meta.get("title") or fallback or "paper")
    first_author = str(meta.get("first_author") or "unknown")
    suffix = f"_{hashlib.sha256(canonical_id.encode('utf-8')).hexdigest()[:8]}.pdf"
    available = MAX_FILENAME_BYTES - len(suffix.encode("utf-8"))
    stem = _truncate_utf8(_clean_stem(f"{year}_{first_author}_{title}"), available)
    return stem + suffix


def new_state(manifest_sha256: str = "") -> dict:
    return {
        "version": STATE_VERSION,
        "updated_at": utc_now(),
        "manifest_sha256": manifest_sha256,
        "records": {},
    }


def load_state(out_dir: Path, warn: Callable[[str], None] | None = None) -> dict:
    path = Path(out_dir) / STATE_FILENAME
    if not path.exists():
        return new_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if warn:
            warn(f"Ignoring unreadable state file {path}: {type(exc).__name__}")
        return new_state()
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), dict):
        if warn:
            warn(f"Ignoring invalid state file {path}")
        return new_state()
    payload.setdefault("version", STATE_VERSION)
    payload.setdefault("updated_at", utc_now())
    payload.setdefault("manifest_sha256", "")
    return payload


def save_state(out_dir: Path, state: dict) -> Path:
    path = Path(out_dir) / STATE_FILENAME
    state["version"] = STATE_VERSION
    state["updated_at"] = utc_now()
    atomic_write_json(path, state)
    return path


def recorded_pdf_path(out_dir: Path, state: dict, canonical_id: str) -> Path | None:
    record = (state.get("records") or {}).get(canonical_id) or {}
    filename = record.get("file")
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        return None
    return Path(out_dir) / filename


def record_result(state: dict, item: dict, result: dict) -> None:
    canonical_id = item.get("canonical_id")
    if not canonical_id:
        return
    records = state.setdefault("records", {})
    record = records.setdefault(
        canonical_id,
        {"input_ids": [], "file": None, "status": "failed", "runs": []},
    )
    input_id = item.get("id")
    if input_id is not None and input_id not in record["input_ids"]:
        record["input_ids"].append(input_id)
    result_file = result.get("file")
    if result_file:
        record["file"] = Path(result_file).name
    record["status"] = result.get("status") or (
        "downloaded" if result.get("success") else "failed"
    )
    record["source"] = result.get("source")
    meta = result.get("meta") or {}
    record["meta"] = {
        key: meta.get(key)
        for key in ("title", "doi", "year", "first_author", "source_id", "url")
        if meta.get(key) not in (None, "")
    }
    record["naming_version"] = NAMING_VERSION
    record["runs"].append(
        {
            "at": utc_now(),
            "status": record["status"],
            "source": result.get("source"),
            "error": result.get("error"),
            "attempts": result.get("attempts") or [],
            "sources": result.get("sources") or [],
            "institutional": result.get("institutional"),
        }
    )
    state["updated_at"] = utc_now()


def write_pending_csv(out_dir: Path, pending: list[tuple[dict, dict]]) -> Path:
    path = Path(out_dir) / PENDING_FILENAME
    if not pending:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return path
    rows = []
    for item, result in pending:
        rows.append(
            {
                "id": item.get("id") or "",
                "title": item.get("title") or "",
                "doi": item.get("doi") or "",
                "url": item.get("url") or "",
                "pending_reason": result.get("pending_reason") or result.get("error") or "",
            }
        )
    atomic_write_csv(
        path,
        ["id", "title", "doi", "url", "pending_reason"],
        rows,
    )
    return path
