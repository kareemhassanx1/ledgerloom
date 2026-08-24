"""Non-destructive journal integrity inspection and diagnostics."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ledgerloom.journal.entries import MAX_JOURNAL_SEQUENCE, JournalEntry
from ledgerloom.journal.errors import JournalError, JournalIntegrityError
from ledgerloom.serialization.fingerprints import ContentFingerprint


@dataclass(frozen=True, slots=True)
class JournalIntegrityIssue:
    """One stable integrity failure found while inspecting a journal chain."""

    code: str
    path: str
    message: str
    sequence: int | None = None

    def to_error(self) -> JournalIntegrityError:
        return JournalIntegrityError(code=self.code, path=self.path, message=self.message)


@dataclass(frozen=True, slots=True)
class JournalIntegrityReport:
    """Describe the verified prefix and any failure without mutating storage."""

    starting_sequence: int
    entries_seen: int
    valid_entries: int
    head_fingerprint: ContentFingerprint | None
    issues: tuple[JournalIntegrityIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not self.issues

    @property
    def next_sequence(self) -> int:
        return self.starting_sequence + self.valid_entries

    def require_valid(self) -> None:
        """Raise the first reported issue when a binary outcome is required."""

        if self.issues:
            raise self.issues[0].to_error()


def _integer(
    value: object,
    *,
    type_code: str,
    range_code: str,
    path: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise JournalError(code=type_code, path=path, message="must be an integer")
    if not minimum <= value <= maximum:
        raise JournalError(
            code=range_code,
            path=path,
            message=f"must be between {minimum} and {maximum}",
        )
    return value


def inspect_journal_entries(
    entries: Iterable[JournalEntry],
    *,
    starting_sequence: int = 1,
    previous_fingerprint: ContentFingerprint | None = None,
    expected_count: int | None = None,
    expected_head: ContentFingerprint | None = None,
) -> JournalIntegrityReport:
    """Inspect a complete journal or a segment and return structured findings.

    Segment verification starts at ``starting_sequence`` and links its first
    entry to ``previous_fingerprint``. Optional count and head expectations make
    otherwise invisible truncation or stale-copy failures observable.
    Inspection stops at the first broken link so later damage does not produce a
    cascade of misleading secondary issues.
    """

    if isinstance(entries, (str, bytes, bytearray)) or not isinstance(entries, Iterable):
        raise JournalError(
            code="journal.entries_type",
            path="$",
            message="entries must be an iterable of JournalEntry values",
        )
    resolved_start = _integer(
        starting_sequence,
        type_code="journal.starting_sequence_type",
        range_code="journal.starting_sequence_range",
        path="$.starting_sequence",
        minimum=1,
        maximum=MAX_JOURNAL_SEQUENCE,
    )
    if previous_fingerprint is not None and not isinstance(
        previous_fingerprint, ContentFingerprint
    ):
        raise JournalError(
            code="journal.previous_fingerprint_type",
            path="$.previous_fingerprint",
            message="previous fingerprint must be a ContentFingerprint or None",
        )
    if resolved_start == 1 and previous_fingerprint is not None:
        raise JournalError(
            code="journal.segment_genesis_previous",
            path="$.previous_fingerprint",
            message="a segment starting at sequence one must not have a predecessor",
        )
    if resolved_start > 1 and previous_fingerprint is None:
        raise JournalError(
            code="journal.segment_previous_missing",
            path="$.previous_fingerprint",
            message="a segment after sequence one must declare its predecessor",
        )
    if expected_count is None:
        resolved_expected_count = None
    else:
        resolved_expected_count = _integer(
            expected_count,
            type_code="journal.expected_count_type",
            range_code="journal.expected_count_range",
            path="$.expected_count",
            minimum=0,
            maximum=MAX_JOURNAL_SEQUENCE,
        )
    if expected_head is not None and not isinstance(expected_head, ContentFingerprint):
        raise JournalError(
            code="journal.expected_head_type",
            path="$.expected_head",
            message="expected head must be a ContentFingerprint or None",
        )

    materialized = tuple(entries)
    previous = previous_fingerprint
    valid_entries = 0
    issues: list[JournalIntegrityIssue] = []

    for offset, entry in enumerate(materialized):
        expected_sequence = resolved_start + offset
        if expected_sequence > MAX_JOURNAL_SEQUENCE:
            issues.append(
                JournalIntegrityIssue(
                    code="journal.sequence_exhausted",
                    path=f"$[{offset}].sequence",
                    message="journal segment exceeds the supported sequence range",
                    sequence=None,
                )
            )
            break
        if not isinstance(entry, JournalEntry):
            issues.append(
                JournalIntegrityIssue(
                    code="journal.entry_type",
                    path=f"$[{offset}]",
                    message="journal chains may contain only JournalEntry values",
                    sequence=expected_sequence,
                )
            )
            break
        try:
            entry.verify(
                expected_sequence=expected_sequence,
                expected_previous=previous,
            )
        except JournalIntegrityError as error:
            issues.append(
                JournalIntegrityIssue(
                    code=error.code,
                    path=error.path,
                    message=error.message,
                    sequence=expected_sequence,
                )
            )
            break
        previous = entry.fingerprint
        valid_entries += 1

    if not issues and resolved_expected_count is not None:
        if len(materialized) < resolved_expected_count:
            issues.append(
                JournalIntegrityIssue(
                    code="journal.truncated",
                    path="$",
                    message=(
                        f"expected {resolved_expected_count} entries, found {len(materialized)}"
                    ),
                    sequence=resolved_start + len(materialized),
                )
            )
        elif len(materialized) > resolved_expected_count:
            issues.append(
                JournalIntegrityIssue(
                    code="journal.extra_entries",
                    path="$",
                    message=(
                        f"expected {resolved_expected_count} entries, found {len(materialized)}"
                    ),
                    sequence=resolved_start + resolved_expected_count,
                )
            )

    if not issues and expected_head is not None and previous != expected_head:
        issues.append(
            JournalIntegrityIssue(
                code="journal.head_mismatch",
                path="$.head_fingerprint",
                message="verified chain does not end at the expected fingerprint",
                sequence=(resolved_start + valid_entries - 1 if valid_entries else None),
            )
        )

    return JournalIntegrityReport(
        starting_sequence=resolved_start,
        entries_seen=len(materialized),
        valid_entries=valid_entries,
        head_fingerprint=previous,
        issues=tuple(issues),
    )
