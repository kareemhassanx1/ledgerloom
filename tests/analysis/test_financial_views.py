from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
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
from ledgerloom.assurance.evidence import build_close_evidence, verify_close_evidence
from ledgerloom.assurance.planning import CapacityWindow, plan_journal_reviews
from ledgerloom.assurance.risk import assess_journal_close
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


def close_inputs(make_entry, *, amount="125.5", candidate_amount=None):
    a = make_entry("anchor", amount=amount)
    b = make_entry("candidate", source_id="processor", amount=candidate_amount or amount)
    entries = chain(catalog(a, b, reports=(report(a, b),)))
    controls = (
        control(count=1, inflow=amount, outflow="0"),
        control(count=1, inflow=candidate_amount or amount, outflow="0", source="processor"),
    )
    return entries, controls


def review_window(*, units=1, currencies=("USD",)):
    return CapacityWindow(
        "desk",
        AS_OF.shift(timedelta(hours=-1)).value,
        AS_OF.shift(timedelta(hours=2)).value,
        units,
        frozenset("currency:" + currency for currency in currencies),
    )


def close_options(controls=None, windows=()):
    return dict(
        as_of=AS_OF,
        due_at=AS_OF.shift(timedelta(hours=3)),
        controls=controls,
        windows=windows,
        actor="financial-reviewer",
    )


def failed_codes(result):
    return {row["code"] for row in result["findings"] if not row["passed"]}


def test_financial_close_requires_real_settlements_and_independent_controls(make_entry):
    entries, controls = close_inputs(make_entry)
    accepted = assess_journal_close(iter(entries), as_of=AS_OF, controls=iter(controls))
    assert accepted["disposition"] == "accept" and not accepted["is_human_approval"]
    assert accepted["audit"]["open_items"] == []
    assert accepted["audit"]["head_fingerprint"] == str(entries[-1].fingerprint)
    for missing in (None, ()):
        result = assess_journal_close(entries, as_of=AS_OF, controls=missing)
        assert result["disposition"] == "hold"
        assert "independent-controls" in failed_codes(result)
    incomplete = assess_journal_close(entries[:-1], as_of=AS_OF, controls=controls)
    assert failed_codes(incomplete) == {"open-entries"}
    assert incomplete["audit"]["open_items"][0]["status"] == "unreviewed"


def test_close_amount_checks_are_exact_under_low_decimal_precision(make_entry):
    exact = "99999999999999999999.000000000000000001"
    entries, controls = close_inputs(make_entry, amount=exact)
    with localcontext() as context:
        context.prec = 2
        good = assess_journal_close(entries, as_of=AS_OF, controls=controls)
        wrong = assess_journal_close(
            entries,
            as_of=AS_OF,
            controls=(replace(controls[0], inflow=Decimal("99999999999999999999")), controls[1]),
        )
    assert good["disposition"] == "accept"
    assert good["independent_controls"][0]["inflow"] == exact
    assert failed_codes(wrong) == {"statement-discrepancies"}


def test_close_detects_real_settlement_variance_and_preserves_report_identity(make_entry):
    entries, controls = close_inputs(make_entry, candidate_amount="125.49")
    result = assess_journal_close(entries, as_of=AS_OF, controls=controls)
    assert failed_codes(result) == {"settlement-discrepancies"}
    variance = result["audit"]["settlements"][0]
    assert variance["variance"] == "0.01" and variance["report"] == "report"


def test_close_empty_unknown_rejected_and_future_data_cannot_pass(make_entry):
    assert "imported-rows" in failed_codes(assess_journal_close((), as_of=AS_OF))
    future = make_entry("future", occurred_at="2026-09-20T00:00:00Z")
    rejected = LedgerBatch(
        BatchId("rejected"),
        SourceId("failed"),
        (ValidationResult.failure(Diagnostic("import.bad_amount", "amount", "bad amount")),),
    )
    view = catalog(future)
    entries = chain(JournalCatalog((*view.batches, rejected), ()))
    unknown = JournalEntry.create(
        len(entries) + 1,
        SchemaDocument.build(SchemaRef.parse("vendor.note", "1.0"), {"ok": True}),
        entries[-1].fingerprint,
    )
    result = assess_journal_close((*entries, unknown), as_of=AS_OF)
    assert {
        "future-source-entries",
        "unknown-journal-events",
        "rejected-import-rows",
    } <= failed_codes(result)
    with pytest.raises(JournalIntegrityError):
        assess_journal_close((unknown,), as_of=AS_OF)


def test_review_plan_excludes_both_settled_participants_and_keeps_last_decision(make_entry):
    a, b = make_entry("a"), make_entry("b", source_id="processor")
    c, d = make_entry("c"), make_entry("d", currency="EUR")
    entries = chain(catalog(a, b, c, d, reports=(report(a, b), report(c, name="last-review"))))
    plan = plan_journal_reviews(
        iter(entries),
        as_of=AS_OF,
        due_at=AS_OF.shift(timedelta(hours=3)),
        windows=iter((review_window(),)),
    )
    assert [item["entry"] for item in plan["items"]] == ["c", "d"]
    assert plan["items"][0]["last_report"] == "last-review"
    assert plan["items"][0]["reason"] == "decision.no_candidates"
    assert plan["allocations"][0]["entry"] == "c"
    assert plan["allocations"][0]["starts_at"] == AS_OF.canonical()
    assert plan["unscheduled"] == [{"entry": "d", "units": 1, "reason": "no-compatible-window"}]


def test_review_plan_prioritizes_decisions_without_splitting_or_overbooking(make_entry):
    a, b, c = (make_entry(name) for name in ("a", "b", "c"))
    entries = chain(catalog(a, b, c, reports=(report(c),)))
    plan = plan_journal_reviews(
        entries, as_of=AS_OF, due_at=AS_OF.shift(timedelta(hours=3)), windows=(review_window(),)
    )
    assert [row["entry"] for row in plan["allocations"]] == ["c"]
    assert len(plan["unscheduled"]) == 2
    assert all(
        row["units"] == 1 and row["reason"] == "capacity-exhausted" for row in plan["unscheduled"]
    )
    assert plan["items"][2]["status"] == "unmatched"


def test_review_future_entries_and_empty_capacity_are_explicit(make_entry):
    entries = chain(
        catalog(make_entry("now"), make_entry("future", occurred_at="2026-09-17T00:00:00Z"))
    )
    plan = plan_journal_reviews(
        entries, as_of=AS_OF, due_at=AS_OF.shift(timedelta(hours=3)), windows=()
    )
    assert plan["allocations"] == []
    assert [row["reason"] for row in plan["unscheduled"]] == [
        "source-at-or-after-deadline",
        "no-compatible-window",
    ]
    with pytest.raises(ValueError, match="deadline"):
        plan_journal_reviews(entries, as_of=AS_OF, due_at=AS_OF, windows=())


@pytest.mark.parametrize("units", [True, 1.5])
def test_financial_review_requires_integer_capacity(make_entry, units):
    with pytest.raises(ValueError, match="integer"):
        plan_journal_reviews(
            chain(catalog(make_entry())),
            as_of=AS_OF,
            due_at=AS_OF.shift(timedelta(hours=3)),
            windows=(review_window(units=units),),
        )


def test_close_evidence_is_deterministic_and_independently_reproducible(make_entry):
    entries, controls = close_inputs(make_entry)
    options = close_options(controls, (review_window(),))
    first = build_close_evidence(iter(entries), **options)
    assert verify_close_evidence(first, iter(entries), **options)
    assert first == build_close_evidence(entries, **{**options, "controls": reversed(controls)})
    assert first["review_plan"]["items"] == []
    assert all(record["subject_id"] == str(entries[-1].fingerprint) for record in first["records"])
    assert not first["is_human_approval"]


@pytest.mark.parametrize(
    "changed", ["head", "count", "decision", "control", "capacity", "actor", "digest", "extra"]
)
def test_financial_evidence_rejects_altered_or_foreign_evidence(make_entry, changed):
    entries, controls = close_inputs(make_entry)
    options = close_options(controls, (review_window(),))
    evidence = build_close_evidence(entries, **options)
    altered = deepcopy(evidence)
    if changed == "head":
        altered["assessment"]["audit"]["head_fingerprint"] = "foreign"
    elif changed == "count":
        altered["assessment"]["audit"]["entry_count"] = 0
    elif changed == "decision":
        altered["assessment"]["audit"]["settlements"][0]["report"] = "invented"
    elif changed == "control":
        altered["assessment"]["independent_controls"][0]["count"] = 999
    elif changed == "capacity":
        altered["review_plan"]["windows"][0]["units"] = 999
    elif changed == "actor":
        altered["records"][0]["actor"] = "other"
    elif changed == "digest":
        altered["terminal_digest"] = "0" * 64
    else:
        altered["approved"] = True
    assert not verify_close_evidence(altered, entries, **options)
    assert not verify_close_evidence(evidence, entries[:-1], **options)


def test_rehashed_foreign_chain_is_not_financial_evidence(make_entry):
    from ledgerloom.assurance.evidence import CloseApprovalEvidenceChain, EvidenceDraft
    from ledgerloom.serialization import canonical_json_text

    entries, controls = close_inputs(make_entry)
    options = close_options(controls)
    forged = build_close_evidence(entries, **options)
    forged["assessment"]["audit"]["settlements"][0]["candidate"] = "fabricated"
    chain = CloseApprovalEvidenceChain(str(entries[-1].fingerprint))
    for row in forged["records"]:
        attributes = row["attributes"]
        if row["kind"] == "financial-close-assessment":
            attributes = {"canonical_evidence": canonical_json_text(forged["assessment"])}
        sealed = chain.append(
            EvidenceDraft(row["evidence_id"], row["kind"], AS_OF.value, row["actor"], attributes)
        )
        row.update(
            attributes=attributes, previous_digest=sealed.previous_digest, digest=sealed.digest
        )
    forged["terminal_digest"] = chain.terminal_digest
    assert chain.verify().valid
    assert not verify_close_evidence(forged, entries, **options)
