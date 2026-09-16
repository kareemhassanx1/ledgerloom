"""Source ingestion coverage, including imports with no accepted financial rows."""

from collections import Counter

from ledgerloom.analysis.settlements import catalog_entries
from ledgerloom.application.catalog import JournalCatalog


def source_coverage(catalog: JournalCatalog) -> tuple[dict[str, object], ...]:
    """Retain row rejection counts and observed time span; never infer missing delivery dates."""
    catalog_entries(catalog)
    sources = sorted({batch.source_id for batch in catalog.batches}, key=str)
    rows = []
    for source in sources:
        batches = tuple(batch for batch in catalog.batches if batch.source_id == source)
        entries = tuple(entry for batch in batches for entry in batch.entries)
        diagnostics = Counter(
            diagnostic.code
            for batch in batches
            for result in batch.results
            for diagnostic in result.diagnostics
        )
        times = [entry.occurred_at for entry in entries]
        rows.append(
            {
                "source": str(source),
                "batches": [str(batch.batch_id) for batch in batches],
                "input_rows": sum(len(batch.results) for batch in batches),
                "accepted_rows": len(entries),
                "rejected_rows": sum(
                    result.value is None for batch in batches for result in batch.results
                ),
                "first_occurred_at": min(times).canonical() if times else None,
                "last_occurred_at": max(times).canonical() if times else None,
                "currencies": sorted({str(entry.amount.currency) for entry in entries}),
                "diagnostics": dict(sorted(diagnostics.items())),
            }
        )
    return tuple(rows)
