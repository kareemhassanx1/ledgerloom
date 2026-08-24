"""Immutable entries and integrity verification for append-only journals."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Self

from ledgerloom.journal.errors import JournalError, JournalIntegrityError
from ledgerloom.serialization.canonical import canonical_json_bytes
from ledgerloom.serialization.errors import SerializationError
from ledgerloom.serialization.fingerprints import ContentFingerprint
from ledgerloom.serialization.schema import SchemaDocument

MAX_JOURNAL_SEQUENCE = (2**53) - 1


def _entry_content(
    sequence: int,
    document: SchemaDocument,
    previous_fingerprint: ContentFingerprint | None,
) -> bytes:
    return canonical_json_bytes(
        {
            "event_fingerprint": str(document.fingerprint),
            "event_schema": str(document.schema.name),
            "event_version": str(document.schema.version),
            "previous_fingerprint": (
                None if previous_fingerprint is None else str(previous_fingerprint)
            ),
            "sequence": sequence,
        }
    )


def _validate_sequence(sequence: object, *, path: str) -> int:
    if isinstance(sequence, bool) or not isinstance(sequence, int):
        raise JournalError(
            code="journal.sequence_type",
            path=path,
            message="sequence must be an integer",
        )
    if not 1 <= sequence <= MAX_JOURNAL_SEQUENCE:
        raise JournalError(
            code="journal.sequence_range",
            path=path,
            message=f"sequence must be between 1 and {MAX_JOURNAL_SEQUENCE}",
        )
    return sequence


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """One sequenced schema document linked to the preceding journal entry."""

    sequence: int
    document: SchemaDocument
    previous_fingerprint: ContentFingerprint | None
    fingerprint: ContentFingerprint

    def __post_init__(self) -> None:
        _validate_sequence(self.sequence, path="$.sequence")
        if not isinstance(self.document, SchemaDocument):
            raise JournalError(
                code="journal.document_type",
                path="$.document",
                message="document must be a SchemaDocument",
            )
        if self.previous_fingerprint is not None and not isinstance(
            self.previous_fingerprint, ContentFingerprint
        ):
            raise JournalError(
                code="journal.previous_fingerprint_type",
                path="$.previous_fingerprint",
                message="previous fingerprint must be a ContentFingerprint or None",
            )
        if self.sequence == 1 and self.previous_fingerprint is not None:
            raise JournalIntegrityError(
                code="journal.genesis_previous",
                path="$.previous_fingerprint",
                message="the first entry must not reference a previous fingerprint",
            )
        if self.sequence > 1 and self.previous_fingerprint is None:
            raise JournalIntegrityError(
                code="journal.previous_missing",
                path="$.previous_fingerprint",
                message="entries after the first must reference the previous fingerprint",
            )
        if not isinstance(self.fingerprint, ContentFingerprint):
            raise JournalError(
                code="journal.fingerprint_type",
                path="$.fingerprint",
                message="fingerprint must be a ContentFingerprint",
            )
        self._verify_document()
        expected = self._calculate_fingerprint()
        if self.fingerprint != expected:
            raise JournalIntegrityError(
                code="journal.fingerprint_mismatch",
                path="$.fingerprint",
                message="entry metadata does not match its fingerprint",
            )

    @classmethod
    def create(
        cls,
        sequence: int,
        document: SchemaDocument,
        previous_fingerprint: ContentFingerprint | None,
    ) -> Self:
        """Build an entry whose fingerprint covers its position, event, and predecessor."""

        resolved_sequence = _validate_sequence(sequence, path="$.sequence")
        if not isinstance(document, SchemaDocument):
            raise JournalError(
                code="journal.document_type",
                path="$.document",
                message="document must be a SchemaDocument",
            )
        if previous_fingerprint is not None and not isinstance(
            previous_fingerprint, ContentFingerprint
        ):
            raise JournalError(
                code="journal.previous_fingerprint_type",
                path="$.previous_fingerprint",
                message="previous fingerprint must be a ContentFingerprint or None",
            )
        fingerprint = ContentFingerprint.from_bytes(
            _entry_content(resolved_sequence, document, previous_fingerprint)
        )
        return cls(resolved_sequence, document, previous_fingerprint, fingerprint)

    def verify(
        self,
        *,
        expected_sequence: int,
        expected_previous: ContentFingerprint | None,
    ) -> None:
        """Verify this entry against its expected position in a journal chain."""

        if self.sequence != expected_sequence:
            raise JournalIntegrityError(
                code="journal.sequence_gap",
                path=f"$[{expected_sequence - 1}].sequence",
                message=f"expected sequence {expected_sequence}, found {self.sequence}",
            )
        if self.previous_fingerprint != expected_previous:
            raise JournalIntegrityError(
                code="journal.chain_mismatch",
                path=f"$[{expected_sequence - 1}].previous_fingerprint",
                message="entry does not reference the preceding fingerprint",
            )
        self._verify_document()
        if self.fingerprint != self._calculate_fingerprint():
            raise JournalIntegrityError(
                code="journal.fingerprint_mismatch",
                path=f"$[{expected_sequence - 1}].fingerprint",
                message="entry metadata does not match its fingerprint",
            )

    def _calculate_fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_bytes(
            _entry_content(self.sequence, self.document, self.previous_fingerprint)
        )

    def _verify_document(self) -> None:
        try:
            self.document.fingerprint.require_bytes(self.document.content)
        except SerializationError as error:
            raise JournalIntegrityError(
                code="journal.event_fingerprint_mismatch",
                path="$.document",
                message="event content does not match its fingerprint",
            ) from error


def verify_journal_entries(entries: Iterable[JournalEntry]) -> None:
    """Require an iterable to contain one complete, contiguous hash chain."""

    from ledgerloom.journal.integrity import inspect_journal_entries

    inspect_journal_entries(entries).require_valid()
