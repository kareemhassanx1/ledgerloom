# Streaming CSV and JSON Lines import

LedgerLoom imports external records as a lazy sequence of immutable
`ImportRowResult` values. A rejected row is data: it carries safe diagnostics
and does not hide valid rows that follow it. Import results never retain the raw
row or rejected field values.

## Shared record mapping

Both adapters use `SourceRecordMapper`. The default external names are
`record_id`, `account_id`, `amount`, `currency`, `occurred_at`, and the optional
`reference`. Amounts must be exact decimal text and timestamps must contain an
explicit UTC offset. JSON numeric values are not silently converted to text.

```python
from ledgerloom.domain import SourceId
from ledgerloom.ingest import SourceRecordColumns, SourceRecordMapper

mapper = SourceRecordMapper(
    SourceId("bank-primary"),
    SourceRecordColumns(
        record_id="transaction_id",
        account_id="ledger_account",
        amount="value",
        currency="currency_code",
        occurred_at="booked_at",
        reference="memo",
    ),
)
```

The mapper collects independent field failures before returning. Diagnostics
contain stable codes, canonical domain field names, messages, and severity, but
never the rejected values.

## CSV

Open CSV text with `newline=""` so Python's CSV parser handles quoted newlines
correctly. The iterator reads only enough input to produce the next result.

```python
from pathlib import Path

from ledgerloom.ingest import CsvImporter, reject_duplicate_source_records

importer = CsvImporter(mapper)
with Path("transactions.csv").open(encoding="utf-8", newline="") as stream:
    rows = reject_duplicate_source_records(importer.import_rows(stream))
    for row in rows:
        if row.record is not None:
            process(row.record)
        else:
            log(row.location.line_number, row.diagnostics)
```

The header must contain every required mapped field exactly once. The optional
reference column may be absent. `CsvHeaderPolicy` controls UTF-8 BOM handling,
extra columns, and the maximum number of columns. `CsvRowPolicy` controls blank
rows and the maximum characters per parsed field. Record locations preserve the
physical starting line of a quoted multiline record.

A malformed header or CSV syntax raises `IngestError` because recovery at that
stream boundary is not reliable. A wrong row shape, oversized field, or invalid
domain value produces a row result and parsing continues.

## JSON Lines

Every non-skipped physical line must contain one standard JSON object. Duplicate
keys, malformed JSON, non-object roots, `NaN`, and infinities produce row-level
diagnostics; later lines are still read. Duplicate-key checks also apply to
nested objects.

```python
from pathlib import Path

from ledgerloom.ingest import JsonlImporter, JsonlPolicy

importer = JsonlImporter(
    mapper,
    JsonlPolicy(
        skip_blank_lines=True,
        allow_utf8_bom=True,
        max_line_characters=1_000_000,
    ),
)

with Path("transactions.jsonl").open(encoding="utf-8") as stream:
    for row in importer.import_rows(stream):
        print(row.location.line_number, row.is_valid)
```

The line-character limit is applied before JSON decoding. Oversized content is
not copied into its diagnostic.

## Duplicate records and summaries

`reject_duplicate_source_records()` is a composable lazy stage. It accepts the
first valid `(source_id, record_id)` pair and turns later occurrences into
`ingest.duplicate_source_record` failures. Invalid rows do not claim an identity.

`summarize_import()` is a terminal operation: it consumes a result stream and
returns an `ImportSummary` without retaining records. The summary includes total,
accepted and rejected outcomes, warning and error totals, and lexically ordered
diagnostic-code counts.

```python
from ledgerloom.ingest import summarize_import

with Path("transactions.jsonl").open(encoding="utf-8") as stream:
    summary = summarize_import(
        reject_duplicate_source_records(JsonlImporter(mapper).import_rows(stream))
    )

print(summary.accepted, summary.rejected)
```

If records must also be processed, count them in that processing loop or create
a new stream; a consumed iterator cannot be replayed.
