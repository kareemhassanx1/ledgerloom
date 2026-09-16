from dataclasses import replace
from decimal import Decimal, localcontext
from itertools import permutations

import pytest

from ledgerloom.analysis.balances import amount_units, source_balances
from ledgerloom.analysis.control_totals import StatementControl, check_statement_controls
from ledgerloom.analysis.open_items import open_items
from ledgerloom.analysis.period_balances import period_balances
from ledgerloom.analysis.report_changes import report_changes
from ledgerloom.analysis.settlements import settlement_variances
from ledgerloom.analysis.source_coverage import source_coverage
from ledgerloom.application import JournalCatalog
from ledgerloom.application.audit_pack import build_audit_pack
from ledgerloom.application.errors import ApplicationError
from ledgerloom.domain import (
    AccountId,
    BatchId,
    Currency,
    Diagnostic,
    LedgerBatch,
    SourceId,
    TimeRange,
    UtcTimestamp,
    ValidationResult,
)
from ledgerloom.journal import JournalEntry, JournalIntegrityError
from ledgerloom.reconciliation import (
    CandidateReport,
    DecisionReport,
    DecisionStatus,
    MatchScorer,
    ReconciliationReport,
    ReconciliationSummary,
    ScoringRule,
    build_reconciliation_report_document,
)
from ledgerloom.rules import SameAccountRule
from ledgerloom.serialization import (
    SchemaDocument,
    SchemaRef,
    canonical_json_bytes,
    domain_v1_registry,
)

AS_OF = UtcTimestamp.parse("2026-09-16T00:00:00Z")


def catalog(*entries, reports=()):
    return JournalCatalog(
        tuple(
            LedgerBatch(
                BatchId("import-" + str(source)),
                source,
                tuple(ValidationResult.success(e) for e in entries if e.source_id == source),
            )
            for source in sorted({e.source_id for e in entries}, key=str)
        ),
        reports,
    )


def report(anchor, candidate=None, *, name="report", reason="decision.no_candidates"):
    scores = (
        ()
        if candidate is None
        else (
            CandidateReport.from_score(
                MatchScorer((ScoringRule(SameAccountRule(), 100),)).score(anchor, candidate)
            ),
        )
    )
    matched = candidate is not None
    decision = DecisionReport(
        anchor.entry_id,
        DecisionStatus.MATCHED if matched else DecisionStatus.UNMATCHED,
        "decision.unique_leader" if matched else reason,
        candidate.entry_id if matched else None,
        (candidate.entry_id,) if matched else (),
        (),
        scores,
    )
    return ReconciliationReport(
        BatchId(name),
        ReconciliationSummary(
            1, int(matched), int(not matched), 0, 0, len(scores), 10000 if matched else 0
        ),
        (decision,),
    )


def chain(view):
    documents = [domain_v1_registry().encode(batch) for batch in view.batches]
    documents += [build_reconciliation_report_document(r) for r in view.reports]
    entries = []
    for document in documents:
        entries.append(
            JournalEntry.create(
                len(entries) + 1, document, entries[-1].fingerprint if entries else None
            )
        )
    return tuple(entries)


def test_balances_are_exact_permutation_invariant_and_one_pass(make_entry):
    entries = (
        make_entry("a", amount="99999999999999999999.000000000000000001"),
        make_entry("b", amount="-99999999999999999999"),
        make_entry("c", amount="0.000000000000000009"),
    )
    with localcontext() as context:
        context.prec = 2
        for ordering in permutations(entries):
            (row,) = source_balances(iter(ordering))
            assert row["net"] == "0.00000000000000001"
            assert row["count"] == 3


def test_balances_never_net_sources_accounts_or_currencies(make_entry):
    rows = source_balances(
        (
            make_entry("a"),
            make_entry("b", source_id="processor"),
            make_entry("c", account_id="fees"),
            make_entry("d", currency="EUR"),
        )
    )
    assert len(rows) == 4
    assert all(row["count"] == 1 for row in rows)
    assert source_balances(()) == ()


@pytest.mark.parametrize(
    "value", [Decimal("NaN"), Decimal("Infinity"), Decimal("1E-19"), Decimal("1E80"), 1.0]
)
def test_amounts_fail_before_unsafe_or_lossy_aggregation(value):
    with pytest.raises(ApplicationError):
        amount_units(value)


def test_balance_identity_checks_cover_both_identities(make_entry):
    first = make_entry()
    for entries in (
        (first, first),
        (first, replace(first, entry_id=make_entry("other").entry_id)),
        ("bad",),
    ):
        with pytest.raises(ApplicationError):
            source_balances(entries)


def control(*, count=2, inflow="10", outflow="10", source="bank-primary"):
    return StatementControl(
        SourceId(source),
        AccountId("assets:checking"),
        Currency("USD"),
        count,
        Decimal(inflow),
        Decimal(outflow),
    )


def test_controls_detect_offsetting_omissions_even_when_net_is_zero(make_entry):
    values = (make_entry("a", amount="10"), make_entry("b", amount="-10"))
    assert check_statement_controls(values, [control()])["passed"]
    missing = check_statement_controls((), [control()])
    assert not missing["passed"]
    assert set(missing["groups"][0]["issues"]) == {
        "count_mismatch",
        "inflow_mismatch",
        "outflow_mismatch",
    }
    assert not check_statement_controls(values, [control(count=3)])["passed"]
    assert not check_statement_controls(values, ())["passed"]
    assert check_statement_controls((), [control(count=0, inflow="0", outflow="0")])["passed"]


@pytest.mark.parametrize(
    "changes", [{"count": True}, {"count": -1}, {"inflow": "-1"}, {"outflow": "NaN"}]
)
def test_invalid_statement_controls_fail(changes):
    with pytest.raises(ApplicationError):
        control(**changes)


def test_control_duplicates_and_wrong_types_are_not_silent():
    for controls in ([control(), control()], ["bad"]):
        with pytest.raises(ApplicationError):
            check_statement_controls((), controls)
    with pytest.raises(ApplicationError):
        replace(control(), source="bank")


def test_period_boundaries_timezone_and_opening_identity(make_entry):
    entries = tuple(
        make_entry(str(i), amount=amount, occurred_at=date)
        for i, (amount, date) in enumerate(
            (
                ("12", "2026-09-01T02:59:59+03:00"),
                ("5", "2026-09-01T03:00:00+03:00"),
                ("-2", "2026-09-02T00:00:00Z"),
                ("100", "2026-10-01T00:00:00Z"),
            )
        )
    )
    interval = TimeRange(
        UtcTimestamp.parse("2026-09-01T00:00:00Z"), UtcTimestamp.parse("2026-10-01T00:00:00Z")
    )
    (row,) = period_balances(entries, interval)
    assert (row["opening"], row["movement"], row["closing"], row["period_count"]) == (
        "12",
        "3",
        "15",
        2,
    )
    assert period_balances((), interval) == ()
    with pytest.raises(ApplicationError):
        period_balances(entries, "bad")


@pytest.mark.parametrize(
    ("days", "bucket"),
    [(0, "0-7"), (7, "0-7"), (8, "8-30"), (30, "8-30"), (31, "31-90"), (90, "31-90"), (91, "91+")],
)
def test_open_item_aging_boundaries(make_entry, days, bucket):
    from datetime import timedelta

    entry = make_entry(occurred_at=AS_OF.shift(-timedelta(days=days)).canonical())
    (row,) = open_items(catalog(entry), as_of=AS_OF)
    assert (row["age_days"], row["age_bucket"], row["status"]) == (days, bucket, "unreviewed")


def test_open_items_remove_both_claimed_sides_and_keep_latest_unsettled_reason(make_entry):
    anchor = make_entry("a")
    candidate = make_entry("b", source_id="processor")
    other = make_entry("c", occurred_at="2026-10-01T00:00:00Z")
    reports = (
        report(anchor, candidate),
        report(other, name="review-1"),
        report(other, name="review-2", reason="decision.no_eligible_candidates"),
    )
    (row,) = open_items(catalog(anchor, candidate, other, reports=reports), as_of=AS_OF)
    assert row["entry"] == "c" and row["last_report"] == "review-2"
    assert row["age_days"] is None and row["age_bucket"] == "future"
    assert row["reason"] == "decision.no_eligible_candidates"
    with pytest.raises(ApplicationError):
        open_items(catalog(anchor), as_of="yesterday")


@pytest.mark.parametrize(
    ("changes", "issues", "variance"),
    [
        ({"amount": "125.49"}, ["amount_variance"], "0.01"),
        ({"currency": "EUR"}, ["currency_mismatch"], None),
        ({"source_id": "bank-primary"}, ["same_source"], "0"),
        ({}, [], "0"),
    ],
)
def test_settlement_variance_preserves_currency_and_source_semantics(
    make_entry, changes, issues, variance
):
    anchor = make_entry("a")
    candidate = make_entry("b", **({"source_id": "processor"} | changes))
    (row,) = settlement_variances(catalog(anchor, candidate, reports=(report(anchor, candidate),)))
    assert row["issues"] == issues and row["variance"] == variance


def test_analysis_rejects_catalog_with_missing_import_evidence(make_entry):
    anchor = make_entry("a")
    missing = make_entry("b", source_id="processor")
    with pytest.raises(ApplicationError, match="absent import"):
        settlement_variances(catalog(anchor, reports=(report(anchor, missing),)))
    with pytest.raises(ApplicationError):
        settlement_variances(None)


def test_report_changes_identify_added_removed_and_evidence_only_changes(make_entry):
    first = report(make_entry("a"), name="one")
    second = report(make_entry("b"), name="two")
    assert [r["change"] for r in report_changes(first, second)["changes"]] == ["removed", "added"]
    revised = replace(first, decisions=(replace(first.decisions[0], reason_code="decision.other"),))
    (change,) = report_changes(first, revised)["changes"]
    assert change["change"] == "changed"
    assert change["before_status"] == change["after_status"]
    assert report_changes(first, first)["changes"] == []
    with pytest.raises(ApplicationError):
        report_changes(first, None)


def test_source_coverage_retains_all_rejected_batches(make_entry):
    diagnostic = Diagnostic("import.bad_amount", "amount", "Invalid amount")
    rejected = LedgerBatch(
        BatchId("rejects"), SourceId("failed"), (ValidationResult.failure(diagnostic),)
    )
    accepted = catalog(make_entry())
    rows = source_coverage(JournalCatalog((*accepted.batches, rejected), ()))
    failed = next(row for row in rows if row["source"] == "failed")
    assert failed["accepted_rows"] == 0 and failed["rejected_rows"] == 1
    assert failed["first_occurred_at"] is None
    assert failed["diagnostics"] == {"import.bad_amount": 1}
    assert rows[0]["accepted_rows"] == 1


def test_audit_pack_is_deterministic_head_bound_and_reports_controls(make_entry):
    entries = chain(catalog(make_entry("a"), reports=(report(make_entry("a")),)))
    first = build_audit_pack(
        iter(entries),
        as_of=AS_OF,
        controls=[control(count=1, inflow="125.5", outflow="0")],
        compare_reports=(BatchId("report"), BatchId("report")),
    )
    assert first["statement_controls"]["passed"]
    assert first["head_fingerprint"] == str(entries[-1].fingerprint)
    assert first["report_changes"]["changes"] == []
    assert canonical_json_bytes(first) == canonical_json_bytes(
        build_audit_pack(
            entries,
            as_of=AS_OF,
            controls=[control(count=1, inflow="125.5", outflow="0")],
            compare_reports=(BatchId("report"), BatchId("report")),
        )
    )
    empty = build_audit_pack((), as_of=AS_OF)
    assert empty["entry_count"] == 0 and empty["head_fingerprint"] is None
    assert empty["fingerprint"] != first["fingerprint"]


def test_audit_pack_unknown_schema_is_explicit_and_invalid_history_fails(make_entry):
    entry = JournalEntry.create(
        1, SchemaDocument.build(SchemaRef.parse("vendor.note", "1.0"), {"ok": True}), None
    )
    assert build_audit_pack((entry,), as_of=AS_OF)["unknown_schemas"] == ["vendor.note"]
    with pytest.raises(JournalIntegrityError):
        build_audit_pack((entry, entry), as_of=AS_OF)
    values = chain(catalog(make_entry()))
    with pytest.raises(ApplicationError, match="report not found"):
        build_audit_pack(
            values, as_of=AS_OF, compare_reports=(BatchId("missing"), BatchId("other"))
        )
    with pytest.raises(ApplicationError):
        build_audit_pack(values, as_of=AS_OF, compare_reports=("bad",))
