from collections.abc import Callable

import pytest

from ledgerloom.domain.identifiers import AccountId, EntryId, SourceId, SourceRecordId
from ledgerloom.domain.money import Money
from ledgerloom.domain.records import LedgerEntry
from ledgerloom.domain.time import UtcTimestamp


@pytest.fixture
def make_entry() -> Callable[..., LedgerEntry]:
    def factory(
        entry_id: str = "entry-1",
        *,
        source_id: str = "bank-primary",
        account_id: str = "assets:checking",
    ) -> LedgerEntry:
        return LedgerEntry(
            entry_id=EntryId(entry_id),
            source_id=SourceId(source_id),
            source_record_id=SourceRecordId(f"record-{entry_id}"),
            account_id=AccountId(account_id),
            amount=Money.parse("125.50", "USD"),
            occurred_at=UtcTimestamp.parse("2026-08-27T10:15:00+03:00"),
            reference=f"invoice-{entry_id}",
        )

    return factory
