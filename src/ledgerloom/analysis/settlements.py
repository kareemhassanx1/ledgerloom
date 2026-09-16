"""Financial differences retained by committed one-to-one match evidence."""

from ledgerloom.analysis.balances import amount_text, amount_units
from ledgerloom.application.catalog import JournalCatalog
from ledgerloom.application.errors import ApplicationError
from ledgerloom.domain import EntryId, LedgerEntry


def catalog_entries(catalog: JournalCatalog) -> dict[EntryId, LedgerEntry]:
    """Resolve all decision references, including catalogs constructed through the API."""
    if not isinstance(catalog, JournalCatalog):
        raise ApplicationError("analysis.catalog", "$.catalog", "expected a JournalCatalog")
    entries = {entry.entry_id: entry for batch in catalog.batches for entry in batch.entries}
    for report in catalog.reports:
        for decision in report.decisions:
            references = {decision.anchor_id, *decision.competing_anchor_ids}
            references.update(candidate.candidate_id for candidate in decision.candidates)
            if not references <= entries.keys():
                raise ApplicationError(
                    "analysis.missing_entry", "$.reports", "report references an absent import"
                )
    return entries


def settlement_variances(catalog: JournalCatalog) -> tuple[dict[str, object], ...]:
    """Expose signed anchor-minus-candidate differences; never net different currencies."""
    entries = catalog_entries(catalog)
    rows: list[dict[str, object]] = []
    for report in catalog.reports:
        for anchor_id, candidate_id in report.matched_pairs:
            anchor, candidate = entries[anchor_id], entries[candidate_id]
            compatible = anchor.amount.currency == candidate.amount.currency
            issues = []
            if not compatible:
                issues.append("currency_mismatch")
            if anchor.account_id != candidate.account_id:
                issues.append("account_mismatch")
            if anchor.source_id == candidate.source_id:
                issues.append("same_source")
            variance = (
                amount_units(anchor.amount.amount) - amount_units(candidate.amount.amount)
                if compatible
                else None
            )
            if variance is not None and variance != 0:
                issues.append("amount_variance")
            rows.append(
                {
                    "report": str(report.batch_id),
                    "anchor": str(anchor_id),
                    "candidate": str(candidate_id),
                    "anchor_source": str(anchor.source_id),
                    "candidate_source": str(candidate.source_id),
                    "anchor_currency": str(anchor.amount.currency),
                    "candidate_currency": str(candidate.amount.currency),
                    "anchor_amount": amount_text(amount_units(anchor.amount.amount)),
                    "candidate_amount": amount_text(amount_units(candidate.amount.amount)),
                    "variance": None if variance is None else amount_text(variance),
                    "issues": issues,
                }
            )
    return tuple(sorted(rows, key=lambda row: (str(row["report"]), str(row["anchor"]))))
