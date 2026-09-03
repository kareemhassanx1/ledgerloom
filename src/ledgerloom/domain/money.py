"""Exact currency and monetary values."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from functools import total_ordering
from typing import Self

from ledgerloom.domain.errors import CurrencyMismatchError, DomainValidationError

_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_MAX_SIGNIFICANT_DIGITS = 38
_MAX_FRACTIONAL_DIGITS = 18


@dataclass(frozen=True, slots=True, order=True)
class Currency:
    """A three-letter uppercase currency code.

    The type validates the shape used by ISO 4217 without embedding a static
    registry. This permits private settlement units while keeping serialized
    values predictable.
    """

    code: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, str):
            raise DomainValidationError(
                code="currency.type",
                field="currency",
                message="must be a string",
                value=self.code,
            )
        if not _CURRENCY_PATTERN.fullmatch(self.code):
            raise DomainValidationError(
                code="currency.format",
                field="currency",
                message="must contain exactly three uppercase ASCII letters",
                value=self.code,
            )

    @classmethod
    def parse(cls, code: str) -> Self:
        return cls(code)

    def __str__(self) -> str:
        return self.code


@total_ordering
@dataclass(frozen=True, slots=True)
class Money:
    """An exact decimal amount paired with its currency."""

    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise DomainValidationError(
                code="money.amount_type",
                field="amount",
                message="must be a Decimal",
                value=self.amount,
            )
        if not isinstance(self.currency, Currency):
            raise DomainValidationError(
                code="money.currency_type",
                field="currency",
                message="must be a Currency",
                value=self.currency,
            )
        if not self.amount.is_finite():
            raise DomainValidationError(
                code="money.non_finite",
                field="amount",
                message="must be finite",
                value=self.amount,
            )

        _, digits, exponent = self.amount.as_tuple()
        assert isinstance(exponent, int)
        if len(digits) > _MAX_SIGNIFICANT_DIGITS:
            raise DomainValidationError(
                code="money.too_precise",
                field="amount",
                message=f"must contain at most {_MAX_SIGNIFICANT_DIGITS} significant digits",
                value=self.amount,
            )
        fractional_digits = max(-exponent, 0)
        if fractional_digits > _MAX_FRACTIONAL_DIGITS:
            raise DomainValidationError(
                code="money.scale_too_large",
                field="amount",
                message=f"must contain at most {_MAX_FRACTIONAL_DIGITS} fractional digits",
                value=self.amount,
            )

        if self.amount.is_zero() and self.amount.is_signed():
            object.__setattr__(self, "amount", self.amount.copy_abs())

    @classmethod
    def parse(cls, amount: str, currency: str | Currency) -> Self:
        """Parse an amount without accepting lossy binary floating-point input."""

        if not isinstance(amount, str):
            raise DomainValidationError(
                code="money.parse_type",
                field="amount",
                message="must be decimal text",
                value=amount,
            )
        if not amount or amount != amount.strip():
            raise DomainValidationError(
                code="money.parse_format",
                field="amount",
                message="must be canonical decimal text without surrounding whitespace",
                value=amount,
            )
        try:
            parsed = Decimal(amount)
        except InvalidOperation as error:
            raise DomainValidationError(
                code="money.parse_format",
                field="amount",
                message="must be valid decimal text",
                value=amount,
            ) from error
        resolved_currency = currency if isinstance(currency, Currency) else Currency(currency)
        return cls(parsed, resolved_currency)

    @classmethod
    def zero(cls, currency: str | Currency) -> Self:
        resolved_currency = currency if isinstance(currency, Currency) else Currency(currency)
        return cls(Decimal(0), resolved_currency)

    @property
    def is_zero(self) -> bool:
        return self.amount.is_zero()

    @property
    def is_positive(self) -> bool:
        return not self.is_zero and self.amount > 0

    @property
    def is_negative(self) -> bool:
        return not self.is_zero and self.amount < 0

    def canonical_amount(self) -> str:
        """Return a non-exponential representation with insignificant zeros removed."""

        if self.is_zero:
            return "0"
        rendered = format(self.amount, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return rendered

    def _require_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(self.currency.code, other.currency.code)

    def __add__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._require_same_currency(other)
        with localcontext() as context:
            context.prec = _MAX_SIGNIFICANT_DIGITS + _MAX_FRACTIONAL_DIGITS
            return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._require_same_currency(other)
        with localcontext() as context:
            context.prec = _MAX_SIGNIFICANT_DIGITS + _MAX_FRACTIONAL_DIGITS
            return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __abs__(self) -> Money:
        return Money(abs(self.amount), self.currency)

    def scale(self, factor: Decimal) -> Money:
        """Multiply by an exact decimal factor under a private precision context."""

        if not isinstance(factor, Decimal):
            raise DomainValidationError(
                code="money.factor_type",
                field="factor",
                message="must be a Decimal",
                value=factor,
            )
        if not factor.is_finite():
            raise DomainValidationError(
                code="money.factor_non_finite",
                field="factor",
                message="must be finite",
                value=factor,
            )
        with localcontext() as context:
            context.prec = _MAX_SIGNIFICANT_DIGITS + _MAX_FRACTIONAL_DIGITS
            return Money(self.amount * factor, self.currency)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._require_same_currency(other)
        return self.amount < other.amount

    def __str__(self) -> str:
        return f"{self.canonical_amount()} {self.currency.code}"
