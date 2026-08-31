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


@dataclass(frozen=True, slots=True)
class CsvImporter:
    """Read CSV records lazily and return one validation outcome per data row."""

    mapper: SourceRecordMapper
    delimiter: str = ","

    def __post_init__(self) -> None:
        if not isinstance(self.mapper, SourceRecordMapper):
            raise IngestError(
                code="ingest.mapper_type",
                path="$.csv.mapper",
                message="mapper must be a SourceRecordMapper",
            )
        _validate_delimiter(self.delimiter)

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

        if not header or all(not item for item in header):
            raise IngestError(
                code="ingest.csv_header_empty",
                path="$.csv.header",
                message="CSV header must contain named columns",
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
            record_number += 1
            location = ImportLocation(record_number, next_line)
            next_line = reader.line_num + 1
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
