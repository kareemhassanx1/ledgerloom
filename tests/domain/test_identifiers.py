from dataclasses import FrozenInstanceError

import pytest

from ledgerloom.domain.errors import DomainValidationError
from ledgerloom.domain.identifiers import (
    AccountId,
    BatchId,
    CorrelationId,
    EntryId,
    SourceId,
)


@pytest.mark.parametrize(
    "identifier_type,value",
    [
        (SourceId, "bank"),
        (SourceId, "bank.primary"),
        (AccountId, "assets:cash"),
        (AccountId, "checking_US-01"),
        (EntryId, "01K34ACR6E7W8H9J0K1M2N3P4Q"),
        (BatchId, "import-2026-08-21"),
        (CorrelationId, "run:2026.08.21:001"),
    ],
)
def test_accepts_canonical_identifier_text(identifier_type, value):
    identifier = identifier_type(value)

    assert identifier.value == value
    assert str(identifier) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        " leading",
        "trailing ",
        "two words",
        "-leading-hyphen",
        "trailing-hyphen-",
        ".leading-dot",
        "trailing-dot.",
        "contains/slash",
        "contains@symbol",
        "حساب",
        "line\nbreak",
    ],
)
def test_rejects_noncanonical_identifier_text(value):
    with pytest.raises(DomainValidationError) as captured:
        AccountId(value)

    assert captured.value.code in {"identifier.empty", "identifier.format"}
    assert captured.value.field == "account_id"
    assert captured.value.value == value


def test_rejects_non_string_values_without_leaking_them_in_message():
    secret_value = {"token": "private-value"}

    with pytest.raises(DomainValidationError) as captured:
        SourceId(secret_value)  # type: ignore[arg-type]

    assert captured.value.code == "identifier.type"
    assert captured.value.value is secret_value
    assert "private-value" not in str(captured.value)


def test_source_id_has_stricter_length_limit():
    with pytest.raises(DomainValidationError) as captured:
        SourceId("a" * 65)

    assert captured.value.code == "identifier.too_long"
    assert "64" in captured.value.message


def test_account_id_accepts_its_maximum_length():
    identifier = AccountId("a" * 128)

    assert len(identifier.value) == 128


def test_account_id_rejects_text_beyond_its_maximum_length():
    with pytest.raises(DomainValidationError) as captured:
        AccountId("a" * 129)

    assert captured.value.code == "identifier.too_long"


def test_parse_preserves_the_concrete_identifier_type():
    identifier = EntryId.parse("entry-42")

    assert type(identifier) is EntryId
    assert identifier.value == "entry-42"


def test_identifier_is_immutable():
    identifier = AccountId("assets:cash")

    with pytest.raises(FrozenInstanceError):
        identifier.value = "liabilities:card"  # type: ignore[misc]


def test_different_identifier_types_are_not_equal():
    assert AccountId("shared-1") != EntryId("shared-1")


def test_identifiers_are_hashable_for_index_keys():
    account = AccountId("assets:cash")

    index = {account: "primary"}

    assert index[AccountId("assets:cash")] == "primary"


def test_ordering_is_stable_within_one_identifier_type():
    identifiers = [EntryId("entry-10"), EntryId("entry-02"), EntryId("entry-01")]

    assert sorted(identifiers) == [
        EntryId("entry-01"),
        EntryId("entry-02"),
        EntryId("entry-10"),
    ]


def test_validation_error_exposes_stable_structured_fields():
    error = DomainValidationError(
        code="sample.invalid",
        field="sample",
        message="is not valid",
        value="sensitive",
    )

    assert error.code == "sample.invalid"
    assert error.field == "sample"
    assert error.message == "is not valid"
    assert error.value == "sensitive"
    assert str(error) == "sample: is not valid [sample.invalid]"

