"""Streaming CSV source-record import."""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from ledgerloom.domain.diagnostics import Diagnostic, ValidationResult
from ledgerloom.ingest.errors import IngestError
from ledgerloom.ingest.mapping import SourceRecordMapper
from ledgerloom.ingest.results import ImportLocation, ImportRowResult


def _validate_delimiter(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 1
        or value in {'"', "\r", "\n"}
    ):
        raise IngestError(
            code="ingest.csv_delimiter",
            path="$.csv.delimiter",
            message="delimiter must be one character other than quote or a newline",
        )
    return value


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
class CsvHeaderPolicy:
    """Control BOM handling and whether unmapped CSV columns are accepted."""

    allow_extra_columns: bool = True
    allow_utf8_bom: bool = True
    max_columns: int = 256

    def __post_init__(self) -> None:
        if not isinstance(self.allow_extra_columns, bool):
            raise IngestError(
                code="ingest.csv_extra_policy_type",
                path="$.csv.header_policy.allow_extra_columns",
                message="allow_extra_columns must be a boolean",
            )
        if not isinstance(self.allow_utf8_bom, bool):
            raise IngestError(
                code="ingest.csv_bom_policy_type",
                path="$.csv.header_policy.allow_utf8_bom",
                message="allow_utf8_bom must be a boolean",
            )
        _positive_limit(
            self.max_columns,
            path="$.csv.header_policy.max_columns",
            label="maximum columns",
        )


@dataclass(frozen=True, slots=True)
class CsvRowPolicy:
    """Control whether rows containing no values are skipped or diagnosed."""

    skip_blank_rows: bool = True
    max_field_characters: int = 4096

    def __post_init__(self) -> None:
        if not isinstance(self.skip_blank_rows, bool):
            raise IngestError(
                code="ingest.csv_blank_policy_type",
                path="$.csv.row_policy.skip_blank_rows",
                message="skip_blank_rows must be a boolean",
            )
        _positive_limit(
            self.max_field_characters,
            path="$.csv.row_policy.max_field_characters",
            label="maximum field characters",
        )


@dataclass(frozen=True, slots=True)
class CsvImporter:
    """Read CSV records lazily and return one validation outcome per data row."""

    mapper: SourceRecordMapper
    delimiter: str = ","
    header_policy: CsvHeaderPolicy = CsvHeaderPolicy()
    row_policy: CsvRowPolicy = CsvRowPolicy()

    def __post_init__(self) -> None:
        if not isinstance(self.mapper, SourceRecordMapper):
            raise IngestError(
                code="ingest.mapper_type",
                path="$.csv.mapper",
                message="mapper must be a SourceRecordMapper",
            )
        _validate_delimiter(self.delimiter)
        if not isinstance(self.header_policy, CsvHeaderPolicy):
            raise IngestError(
                code="ingest.csv_header_policy_type",
                path="$.csv.header_policy",
                message="header_policy must be a CsvHeaderPolicy",
            )
        if not isinstance(self.row_policy, CsvRowPolicy):
            raise IngestError(
                code="ingest.csv_row_policy_type",
                path="$.csv.row_policy",
                message="row_policy must be a CsvRowPolicy",
            )

    def import_rows(self, lines: Iterable[str]) -> Iterator[ImportRowResult]:
        """Yield rows without materializing the source or prior outcomes."""

        if isinstance(lines, str | bytes) or not isinstance(lines, Iterable):
            raise IngestError(
                code="ingest.lines_type",
                path="$.csv.lines",
                message="lines must be an iterable of text lines",
            )
        return self._import_rows(lines)

    def _import_rows(self, lines: Iterable[str]) -> Iterator[ImportRowResult]:
        reader = csv.reader(lines, delimiter=self.delimiter, strict=True)
        try:
            header = next(reader)
        except StopIteration as error:
            raise IngestError(
                code="ingest.csv_empty",
                path="$.csv",
                message="CSV input must contain a header row",
            ) from error
        except (csv.Error, UnicodeError) as error:
            raise IngestError(
                code="ingest.csv_syntax",
                path="$.csv.header",
                message="CSV header is malformed",
            ) from error

        if header and header[0].startswith("\ufeff"):
            if not self.header_policy.allow_utf8_bom:
                raise IngestError(
                    code="ingest.csv_header_bom",
                    path="$.csv.header[0]",
                    message="UTF-8 byte-order mark is not allowed by the header policy",
                )
            header[0] = header[0].removeprefix("\ufeff")
        if not header or all(not item for item in header):
            raise IngestError(
                code="ingest.csv_header_empty",
                path="$.csv.header",
                message="CSV header must contain named columns",
            )
        if len(header) > self.header_policy.max_columns:
            raise IngestError(
                code="ingest.csv_header_too_wide",
                path="$.csv.header",
                message="CSV header exceeds the configured column limit",
            )
        if any(
            not item
            or item != item.strip()
            or not item.isprintable()
            or len(item) > 128
            for item in header
        ):
            raise IngestError(
                code="ingest.csv_header_name",
                path="$.csv.header",
                message=(
                    "CSV header names must be non-empty printable text without "
                    "surrounding whitespace and at most 128 characters"
                ),
            )
        if len(header) != len(set(header)):
            raise IngestError(
                code="ingest.csv_header_duplicate",
                path="$.csv.header",
                message="CSV header names must be unique",
            )
        missing = tuple(name for name in self.mapper.columns.required if name not in header)
        if missing:
            raise IngestError(
                code="ingest.csv_header_missing",
                path="$.csv.header",
                message="CSV header is missing one or more required mapped columns",
            )
        unexpected = tuple(name for name in header if name not in self.mapper.columns.all)
        if unexpected and not self.header_policy.allow_extra_columns:
            raise IngestError(
                code="ingest.csv_header_unexpected",
                path="$.csv.header",
                message="CSV header contains one or more unmapped columns",
            )

        next_line = reader.line_num + 1
        record_number = 0
        while True:
            try:
                row = next(reader)
            except StopIteration:
                return
            except (csv.Error, UnicodeError) as error:
                raise IngestError(
                    code="ingest.csv_syntax",
                    path=f"$.csv.line[{next_line}]",
                    message="CSV record is malformed",
                ) from error
            line_number = next_line
            next_line = reader.line_num + 1
            if not row or all(value == "" for value in row):
                if self.row_policy.skip_blank_rows:
                    continue
                record_number += 1
                location = ImportLocation(record_number, line_number)
                yield ImportRowResult(
                    location,
                    ValidationResult.failure(
                        Diagnostic(
                            "ingest.csv_row_blank",
                            "row",
                            "row must contain at least one value",
                        )
                    ),
                )
                continue
            record_number += 1
            location = ImportLocation(record_number, line_number)
            if any(len(value) > self.row_policy.max_field_characters for value in row):
                yield ImportRowResult(
                    location,
                    ValidationResult.failure(
                        Diagnostic(
                            "ingest.csv_field_too_long",
                            "row",
                            "one or more fields exceed the configured character limit",
                        )
                    ),
                )
                continue
            if len(row) != len(header):
                yield ImportRowResult(
                    location,
                    ValidationResult.failure(
                        Diagnostic(
                            "ingest.csv_row_shape",
                            "row",
                            "row must contain exactly one value for each header column",
                        )
                    ),
                )
                continue
            mapped = dict(zip(header, row, strict=True))
            yield ImportRowResult(location, self.mapper.map(mapped))
