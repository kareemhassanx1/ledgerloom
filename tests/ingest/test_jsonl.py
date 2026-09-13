import json

import pytest

from ledgerloom.domain import SourceId
from ledgerloom.ingest import IngestError, JsonlImporter, JsonlPolicy, SourceRecordMapper


def _line(**changes) -> str:
    value = {
        "record_id": "txn-1",
        "account_id": "assets:cash",
        "amount": "10.50",
        "currency": "USD",
        "occurred_at": "2026-08-31T08:00:00Z",
        "reference": "فاتورة-واحد",
    }
    value.update(changes)
    return json.dumps(value, ensure_ascii=False) + "\n"


def _importer(policy: JsonlPolicy | None = None) -> JsonlImporter:
    return JsonlImporter(
        SourceRecordMapper(SourceId("bank")),
        JsonlPolicy() if policy is None else policy,
    )


def test_jsonl_streams_unicode_source_records():
    first, second = tuple(
        _importer().import_rows(
            (
                _line(),
                _line(record_id="txn-2", amount="-2", reference=None),
            )
        )
    )

    assert first.is_valid
    assert first.record.reference == "فاتورة-واحد"
    assert second.record.amount.canonical_amount() == "-2"
    assert tuple(item.location.line_number for item in (first, second)) == (1, 2)


def test_jsonl_continues_after_syntax_object_and_domain_failures():
    lines = (
        "{not-json}\n",
        "[]\n",
        _line(record_id="bad id"),
        _line(record_id="txn-4"),
    )

    results = tuple(_importer().import_rows(lines))

    assert tuple(item.is_valid for item in results) == (False, False, False, True)
    assert tuple(item.diagnostics[0].code for item in results[:3]) == (
        "ingest.jsonl_syntax",
        "ingest.jsonl_object",
        "identifier.format",
    )


def test_jsonl_rejects_duplicate_keys_without_exposing_values():
    line = (
        '{"record_id":"first","record_id":"private-second",'
        '"account_id":"assets:cash","amount":"10","currency":"USD",'
        '"occurred_at":"2026-08-31T08:00:00Z"}\n'
    )

    result = next(_importer().import_rows((line,)))

    assert result.diagnostics[0].code == "ingest.jsonl_duplicate_key"
    assert "private-second" not in repr(result.diagnostics)


@pytest.mark.parametrize("line", ["NaN\n", "Infinity\n", "-Infinity\n"])
def test_jsonl_rejects_nonstandard_numeric_constants(line):
    result = next(_importer().import_rows((line,)))

    assert result.diagnostics[0].code == "ingest.jsonl_syntax"


def test_jsonl_numbers_do_not_silently_become_text_fields():
    result = next(_importer().import_rows((_line(amount=10.5),)))

    assert result.diagnostics[0].code == "ingest.field_type"
    assert result.diagnostics[0].field == "amount"


def test_jsonl_blank_lines_are_policy_controlled_and_locations_stay_physical():
    permissive = tuple(_importer().import_rows(("\n", _line())))
    strict = tuple(_importer(JsonlPolicy(skip_blank_lines=False)).import_rows(("\n", _line())))

    assert len(permissive) == 1
    assert permissive[0].location == permissive[0].location.__class__(1, 2)
    assert strict[0].diagnostics[0].code == "ingest.jsonl_line_blank"
    assert strict[1].is_valid


def test_jsonl_accepts_first_line_bom_or_diagnoses_it_by_policy():
    accepted = next(_importer().import_rows(("\ufeff" + _line(),)))
    strict = next(_importer(JsonlPolicy(allow_utf8_bom=False)).import_rows(("\ufeff" + _line(),)))

    assert accepted.is_valid
    assert strict.diagnostics[0].code == "ingest.jsonl_bom"


def test_jsonl_iterator_is_lazy():
    consumed = 0

    def lines():
        nonlocal consumed
        for line in (_line(), _line(record_id="txn-2")):
            consumed += 1
            yield line

    rows = _importer().import_rows(lines())

    assert consumed == 0
    assert next(rows).is_valid
    assert consumed == 1


def test_jsonl_rejects_non_iterable_style_inputs():
    with pytest.raises(IngestError) as captured:
        tuple(_importer().import_rows("{}"))

    assert captured.value.code == "ingest.lines_type"


def test_jsonl_line_limit_rejects_one_line_and_continues_without_value_leakage():
    private_value = "private-" + "x" * 100
    importer = _importer(JsonlPolicy(max_line_characters=200))

    rejected, accepted = tuple(
        importer.import_rows(
            (
                _line(reference=private_value),
                _line(record_id="txn-2", reference=None),
            )
        )
    )

    assert rejected.diagnostics[0].code == "ingest.jsonl_line_too_long"
    assert private_value not in repr(rejected.diagnostics)
    assert accepted.is_valid


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_jsonl_line_limit_requires_a_positive_integer(value):
    with pytest.raises(IngestError) as captured:
        JsonlPolicy(max_line_characters=value)  # type: ignore[arg-type]

    assert captured.value.code in {"ingest.limit_type", "ingest.limit_range"}
