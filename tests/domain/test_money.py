from dataclasses import FrozenInstanceError
from decimal import Decimal, getcontext

import pytest

from ledgerloom.domain.errors import CurrencyMismatchError, DomainValidationError
from ledgerloom.domain.money import Currency, Money


@pytest.mark.parametrize("code", ["USD", "EUR", "EGP", "JPY", "XTS"])
def test_currency_accepts_three_uppercase_ascii_letters(code):
    currency = Currency(code)

    assert currency.code == code
    assert str(currency) == code


@pytest.mark.parametrize(
    "code",
    ["", "US", "USDD", "usd", "Usd", "12A", "€UR", " EGP", "EGP "],
)
def test_currency_rejects_noncanonical_codes(code):
    with pytest.raises(DomainValidationError) as captured:
        Currency(code)

    assert captured.value.code == "currency.format"
    assert captured.value.field == "currency"


def test_currency_rejects_non_string_value():
    with pytest.raises(DomainValidationError) as captured:
        Currency(123)  # type: ignore[arg-type]

    assert captured.value.code == "currency.type"


def test_currency_is_immutable_and_hashable():
    currency = Currency("USD")

    with pytest.raises(FrozenInstanceError):
        currency.code = "EUR"  # type: ignore[misc]

    assert {currency: "dollar"}[Currency("USD")] == "dollar"


@pytest.mark.parametrize(
    "amount,expected",
    [
        ("0", "0"),
        ("-0", "0"),
        ("0.000", "0"),
        ("12", "12"),
        ("12.3400", "12.34"),
        ("-98.700", "-98.7"),
        ("1E+3", "1000"),
        ("1E-3", "0.001"),
    ],
)
def test_canonical_amount_is_plain_and_stable(amount, expected):
    money = Money.parse(amount, "USD")

    assert money.canonical_amount() == expected


def test_parse_accepts_an_existing_currency_value():
    currency = Currency("EGP")

    money = Money.parse("150.25", currency)

    assert money == Money(Decimal("150.25"), currency)


@pytest.mark.parametrize("value", ["", " 1.00", "1.00 ", "one", "1,000", "--1"])
def test_parse_rejects_invalid_or_noncanonical_decimal_text(value):
    with pytest.raises(DomainValidationError) as captured:
        Money.parse(value, "USD")

    assert captured.value.code == "money.parse_format"


def test_constructor_rejects_binary_float():
    with pytest.raises(DomainValidationError) as captured:
        Money(0.1, Currency("USD"))  # type: ignore[arg-type]

    assert captured.value.code == "money.amount_type"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "sNaN"])
def test_money_rejects_non_finite_decimal_values(value):
    with pytest.raises(DomainValidationError) as captured:
        Money.parse(value, "USD")

    assert captured.value.code == "money.non_finite"


def test_money_rejects_more_than_38_significant_digits():
    with pytest.raises(DomainValidationError) as captured:
        Money.parse("123456789012345678901234567890123456789", "USD")

    assert captured.value.code == "money.too_precise"


def test_money_rejects_more_than_18_fractional_digits():
    with pytest.raises(DomainValidationError) as captured:
        Money.parse("0.1234567890123456789", "USD")

    assert captured.value.code == "money.scale_too_large"


def test_adds_values_with_the_same_currency_exactly():
    total = Money.parse("0.10", "USD") + Money.parse("0.20", "USD")

    assert total == Money.parse("0.30", "USD")


def test_subtracts_values_with_the_same_currency_exactly():
    difference = Money.parse("10.05", "EGP") - Money.parse("0.15", "EGP")

    assert difference == Money.parse("9.90", "EGP")


@pytest.mark.parametrize("operator", ["add", "subtract", "compare"])
def test_operations_reject_currency_mismatches(operator):
    dollars = Money.parse("1", "USD")
    euros = Money.parse("1", "EUR")

    with pytest.raises(CurrencyMismatchError) as captured:
        if operator == "add":
            dollars + euros
        elif operator == "subtract":
            dollars - euros
        else:
            _ = dollars < euros

    assert captured.value.left == "USD"
    assert captured.value.right == "EUR"


def test_negation_and_absolute_value_preserve_currency():
    amount = Money.parse("-12.50", "USD")

    assert -amount == Money.parse("12.50", "USD")
    assert abs(amount) == Money.parse("12.50", "USD")


def test_scale_uses_exact_decimal_arithmetic():
    amount = Money.parse("19.99", "USD")

    assert amount.scale(Decimal("3")) == Money.parse("59.97", "USD")


def test_scale_rejects_float_and_non_finite_factors():
    amount = Money.parse("19.99", "USD")

    with pytest.raises(DomainValidationError) as float_error:
        amount.scale(1.5)  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError) as infinite_error:
        amount.scale(Decimal("Infinity"))

    assert float_error.value.code == "money.factor_type"
    assert infinite_error.value.code == "money.factor_non_finite"


def test_arithmetic_does_not_depend_on_callers_decimal_precision():
    original_precision = getcontext().prec
    getcontext().prec = 6
    try:
        left = Money.parse("123456789.123456", "USD")
        right = Money.parse("0.000001", "USD")

        result = left + right
    finally:
        getcontext().prec = original_precision

    assert result.canonical_amount() == "123456789.123457"


def test_same_currency_values_have_total_ordering():
    amounts = [Money.parse("3", "USD"), Money.parse("-1", "USD"), Money.zero("USD")]

    assert sorted(amounts) == [
        Money.parse("-1", "USD"),
        Money.zero("USD"),
        Money.parse("3", "USD"),
    ]


@pytest.mark.parametrize(
    "value,zero,positive,negative",
    [
        ("0", True, False, False),
        ("10", False, True, False),
        ("-10", False, False, True),
    ],
)
def test_sign_properties_are_mutually_exclusive(value, zero, positive, negative):
    money = Money.parse(value, "USD")

    assert money.is_zero is zero
    assert money.is_positive is positive
    assert money.is_negative is negative


def test_string_representation_includes_canonical_amount_and_currency():
    assert str(Money.parse("42.5000", "EGP")) == "42.5 EGP"
