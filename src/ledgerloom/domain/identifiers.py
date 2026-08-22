"""Strongly typed identifiers used throughout the ledger domain."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar, Self

from ledgerloom.domain.errors import DomainValidationError

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:-]*[A-Za-z0-9])?$")


@dataclass(frozen=True, slots=True, order=True)
class OpaqueId:
    """An immutable identifier that never performs silent normalization.

    Identifiers cross storage and import boundaries, so changing case or
    trimming whitespace could accidentally join unrelated records. Construction
    therefore accepts only an already-canonical, printable ASCII representation.
    Concrete subclasses provide type separation between otherwise equal text.
    """

    value: str

    kind: ClassVar[str] = "identifier"
    max_length: ClassVar[int] = 128

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise DomainValidationError(
                code="identifier.type",
                field=self.kind,
                message="must be a string",
                value=self.value,
            )
        if not self.value:
            raise DomainValidationError(
                code="identifier.empty",
                field=self.kind,
                message="must not be empty",
                value=self.value,
            )
        if len(self.value) > self.max_length:
            raise DomainValidationError(
                code="identifier.too_long",
                field=self.kind,
                message=f"must contain at most {self.max_length} characters",
                value=self.value,
            )
        if not _IDENTIFIER_PATTERN.fullmatch(self.value):
            raise DomainValidationError(
                code="identifier.format",
                field=self.kind,
                message=(
                    "must start and end with an ASCII letter or digit and contain "
                    "only letters, digits, dots, underscores, colons, or hyphens"
                ),
                value=self.value,
            )

    @classmethod
    def parse(cls, value: str) -> Self:
        """Construct the concrete identifier type from external text."""

        return cls(value)

    def __str__(self) -> str:
        return self.value


class SourceId(OpaqueId):
    """Identify one upstream system or import source."""

    kind = "source_id"
    max_length = 64


class SourceRecordId(OpaqueId):
    """Identify one record within an upstream source."""

    kind = "source_record_id"


class AccountId(OpaqueId):
    """Identify an account within LedgerLoom's normalized namespace."""

    kind = "account_id"


class EntryId(OpaqueId):
    """Identify one normalized ledger entry."""

    kind = "entry_id"


class BatchId(OpaqueId):
    """Identify an immutable ingestion or reconciliation batch."""

    kind = "batch_id"


class CorrelationId(OpaqueId):
    """Link operations that belong to the same external workflow."""

    kind = "correlation_id"
