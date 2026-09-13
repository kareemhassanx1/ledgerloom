"""Verified, deterministic, and atomic journal exports."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from ledgerloom.application.errors import ExportError
from ledgerloom.journal import JournalEntry, verify_journal_entries
from ledgerloom.serialization import canonical_json_bytes, loads_canonical_json
from ledgerloom.serialization.fingerprints import ContentFingerprint


@dataclass(frozen=True, slots=True)
class JournalExportSummary:
    """Describe a completely published JSON Lines export."""

    output: Path
    entry_count: int
    bytes_written: int
    fingerprint: ContentFingerprint


def _resolved_path(value: str | os.PathLike[str], *, path: str) -> Path:
    try:
        candidate = Path(value)
    except TypeError as error:
        raise ExportError(
            "export.path_type",
            path,
            "path must be text or path-like",
        ) from error
    if not str(candidate) or "\x00" in str(candidate):
        raise ExportError(
            "export.path_format",
            path,
            "path must be non-empty text without null characters",
        )
    return candidate.resolve()


def _same_existing_file(left: Path, right: Path) -> bool:
    try:
        return left.exists() and right.exists() and left.samefile(right)
    except OSError:
        return False


def _require_safe_destination(output: Path, protected_paths: tuple[Path, ...]) -> None:
    for protected in protected_paths:
        if output == protected or _same_existing_file(output, protected):
            raise ExportError(
                "export.output_protected",
                "$.output",
                "output must not replace a protected journal file",
            )
    if not output.parent.is_dir():
        raise ExportError(
            "export.output_parent",
            "$.output",
            "output parent directory does not exist",
        )


def _entry_line(entry: JournalEntry) -> bytes:
    return (
        canonical_json_bytes(
            {
                "document": loads_canonical_json(entry.document.content),
                "document_fingerprint": str(entry.document.fingerprint),
                "journal_fingerprint": str(entry.fingerprint),
                "sequence": entry.sequence,
            }
        )
        + b"\n"
    )


def _publish(temporary: Path, output: Path, *, force: bool) -> None:
    if force:
        os.replace(temporary, output)
        return
    try:
        os.link(temporary, output)
    except FileExistsError as error:
        raise ExportError(
            "export.output_exists",
            "$.output",
            "output already exists; pass --force to replace it",
        ) from error
    else:
        temporary.unlink()


def export_journal_jsonl(
    entries: Iterable[JournalEntry],
    output: str | os.PathLike[str],
    *,
    force: bool = False,
    protected_paths: Iterable[str | os.PathLike[str]] = (),
) -> JournalExportSummary:
    """Verify entries and atomically publish their canonical JSON Lines form.

    Without ``force``, publication uses an atomic hard-link operation so a file
    created by another process between validation and publication is never
    overwritten. Temporary files are placed beside the destination, keeping the
    operation on one filesystem.
    """

    if isinstance(entries, str | bytes | bytearray) or not isinstance(entries, Iterable):
        raise ExportError(
            "export.entries_type",
            "$.entries",
            "entries must be an iterable of JournalEntry values",
        )
    if not isinstance(force, bool):
        raise ExportError(
            "export.force_type",
            "$.force",
            "force must be a boolean",
        )
    if isinstance(protected_paths, str | bytes) or not isinstance(protected_paths, Iterable):
        raise ExportError(
            "export.protected_paths_type",
            "$.protected_paths",
            "protected_paths must be an iterable of paths",
        )

    materialized = tuple(entries)
    verify_journal_entries(materialized)
    resolved_output = _resolved_path(output, path="$.output")
    resolved_protected = tuple(
        _resolved_path(item, path=f"$.protected_paths[{index}]")
        for index, item in enumerate(protected_paths)
    )
    _require_safe_destination(resolved_output, resolved_protected)

    descriptor, temporary_name = tempfile.mkstemp(
        dir=resolved_output.parent,
        prefix=f".{resolved_output.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    bytes_written = 0
    try:
        with os.fdopen(descriptor, "wb") as stream:
            for entry in materialized:
                line = _entry_line(entry)
                stream.write(line)
                digest.update(line)
                bytes_written += len(line)
            stream.flush()
            os.fsync(stream.fileno())
        _publish(temporary, resolved_output, force=force)
    except ExportError:
        with suppress(FileNotFoundError):
            temporary.unlink()
        raise
    except OSError as error:
        with suppress(FileNotFoundError):
            temporary.unlink()
        raise ExportError(
            "export.output_write",
            "$.output",
            "output file could not be written",
        ) from error

    return JournalExportSummary(
        resolved_output,
        len(materialized),
        bytes_written,
        ContentFingerprint("sha256", digest.hexdigest()),
    )


__all__ = ["JournalExportSummary", "export_journal_jsonl"]
