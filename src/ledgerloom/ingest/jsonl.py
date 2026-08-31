"""Streaming JSON Lines source-record import."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ledgerloom.domain.diagnostics import Diagnostic, ValidationResult
from ledgerloom.ingest.errors import IngestError
from ledgerloom.ingest.mapping import SourceRecordMapper
from ledgerloom.ingest.results import ImportLocation, ImportRowResult


class _DuplicateKeyError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError
        result[key] = value
    return result


def _reject_constant(_: str) -> Any:
    raise ValueError("non-standard numeric constant")


def _positive_limit(value: object, *, path: str, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise IngestError(
            code="ingest.limit_type",
            path=path,
            message=f"{label} must be an integer",
        )
    if value < 1:
        raise IngestError(
            code="ingest.limit_range",
            path=path,
            message=f"{label} must be at least one",
        )
    return value


@dataclass(frozen=True, slots=True)
class JsonlPolicy:
    """Control blank-line and first-line byte-order-mark handling."""

    skip_blank_lines: bool = True
    allow_utf8_bom: bool = True
    max_line_characters: int = 1_000_000

    def __post_init__(self) -> None:
        if not isinstance(self.skip_blank_lines, bool):
            raise IngestError(
                code="ingest.jsonl_blank_policy_type",
                path="$.jsonl.policy.skip_blank_lines",
                message="skip_blank_lines must be a boolean",
            )
        if not isinstance(self.allow_utf8_bom, bool):
            raise IngestError(
                code="ingest.jsonl_bom_policy_type",
                path="$.jsonl.policy.allow_utf8_bom",
                message="allow_utf8_bom must be a boolean",
            )
        _positive_limit(
            self.max_line_characters,
            path="$.jsonl.policy.max_line_characters",
            label="maximum line characters",
        )


@dataclass(frozen=True, slots=True)
class JsonlImporter:
    """Decode one independent JSON object per physical line, lazily."""

    mapper: SourceRecordMapper
    policy: JsonlPolicy = JsonlPolicy()

    def __post_init__(self) -> None:
        if not isinstance(self.mapper, SourceRecordMapper):
            raise IngestError(
                code="ingest.mapper_type",
                path="$.jsonl.mapper",
                message="mapper must be a SourceRecordMapper",
            )
        if not isinstance(self.policy, JsonlPolicy):
            raise IngestError(
                code="ingest.jsonl_policy_type",
                path="$.jsonl.policy",
                message="policy must be a JsonlPolicy",
            )

    def import_rows(self, lines: Iterable[str]) -> Iterator[ImportRowResult]:
        """Yield one result per submitted non-skipped line."""

        if isinstance(lines, str | bytes) or not isinstance(lines, Iterable):
            raise IngestError(
                code="ingest.lines_type",
                path="$.jsonl.lines",
                message="lines must be an iterable of text lines",
            )
        return self._import_rows(lines)

    def _import_rows(self, lines: Iterable[str]) -> Iterator[ImportRowResult]:
        record_number = 0
        for line_number, line in enumerate(lines, start=1):
            if not isinstance(line, str):
                record_number += 1
                yield self._failure(
                    record_number,
                    line_number,
                    "ingest.jsonl_line_type",
                    "line must be text",
                )
                continue
            if line_number == 1 and line.startswith("\ufeff"):
                if not self.policy.allow_utf8_bom:
                    record_number += 1
                    yield self._failure(
                        record_number,
                        line_number,
                        "ingest.jsonl_bom",
                        "UTF-8 byte-order mark is not allowed by policy",
                    )
                    continue
                line = line.removeprefix("\ufeff")
            if len(line) > self.policy.max_line_characters:
                record_number += 1
                yield self._failure(
                    record_number,
                    line_number,
                    "ingest.jsonl_line_too_long",
                    "line exceeds the configured character limit",
                )
                continue
            if not line.strip():
                if self.policy.skip_blank_lines:
                    continue
                record_number += 1
                yield self._failure(
                    record_number,
                    line_number,
                    "ingest.jsonl_line_blank",
                    "line must contain a JSON object",
                )
                continue

            record_number += 1
            try:
                value = json.loads(
                    line,
                    object_pairs_hook=_unique_object,
                    parse_float=Decimal,
                    parse_constant=_reject_constant,
                )
            except _DuplicateKeyError:
                yield self._failure(
                    record_number,
                    line_number,
                    "ingest.jsonl_duplicate_key",
                    "JSON objects must not contain duplicate keys",
                )
                continue
            except (json.JSONDecodeError, UnicodeError, ValueError):
                yield self._failure(
                    record_number,
                    line_number,
                    "ingest.jsonl_syntax",
                    "line must contain one valid standard JSON value",
                )
                continue
            if not isinstance(value, dict):
                yield self._failure(
                    record_number,
                    line_number,
                    "ingest.jsonl_object",
                    "top-level JSON value must be an object",
                )
                continue
            yield ImportRowResult(
                ImportLocation(record_number, line_number),
                self.mapper.map(value),
            )

    @staticmethod
    def _failure(
        record_number: int,
        line_number: int,
        code: str,
        message: str,
    ) -> ImportRowResult:
        return ImportRowResult(
            ImportLocation(record_number, line_number),
            ValidationResult.failure(Diagnostic(code, "row", message)),
        )
