from dataclasses import FrozenInstanceError

import pytest

from ledgerloom.domain.diagnostics import Diagnostic, DiagnosticSeverity, ValidationResult
from ledgerloom.domain.errors import DomainValidationError


def make_diagnostic(
    *, severity: DiagnosticSeverity = DiagnosticSeverity.ERROR
) -> Diagnostic:
    return Diagnostic(
        code="record.amount_invalid",
        field="amount",
        message="must be valid decimal text",
        severity=severity,
    )


def test_diagnostic_exposes_stable_machine_fields():
    diagnostic = make_diagnostic()

    assert diagnostic.code == "record.amount_invalid"
    assert diagnostic.field == "amount"
    assert diagnostic.message == "must be valid decimal text"
    assert diagnostic.severity is DiagnosticSeverity.ERROR


def test_diagnostic_from_error_does_not_retain_rejected_value():
    error = DomainValidationError(
        code="money.parse_format",
        field="amount",
        message="must be valid decimal text",
        value="private-source-value",
    )

    diagnostic = Diagnostic.from_error(error)

    assert diagnostic == Diagnostic(
        code="money.parse_format",
        field="amount",
        message="must be valid decimal text",
    )
    assert not hasattr(diagnostic, "value")
    assert "private-source-value" not in repr(diagnostic)


@pytest.mark.parametrize(
    "field,value,expected_code",
    [
        ("code", "Amount Invalid", "diagnostic.code"),
        ("code", "single", "diagnostic.code"),
        ("field", "Amount", "diagnostic.field"),
        ("message", "", "diagnostic.message_format"),
        ("message", " trailing ", "diagnostic.message_format"),
        ("message", "line\nbreak", "diagnostic.message_format"),
        ("message", "x" * 513, "diagnostic.message_too_long"),
        ("severity", "error", "diagnostic.severity_type"),
    ],
)
def test_diagnostic_rejects_noncanonical_boundary_values(field, value, expected_code):
    values = {
        "code": "record.invalid",
        "field": "record.amount",
        "message": "is invalid",
        "severity": DiagnosticSeverity.ERROR,
    }
    values[field] = value

    with pytest.raises(DomainValidationError) as captured:
        Diagnostic(**values)

    assert captured.value.code == expected_code


def test_diagnostic_is_immutable():
    diagnostic = make_diagnostic()

    with pytest.raises(FrozenInstanceError):
        diagnostic.message = "changed"  # type: ignore[misc]


def test_success_may_carry_warnings_but_not_errors():
    warning = make_diagnostic(severity=DiagnosticSeverity.WARNING)

    result = ValidationResult.success("accepted", warning)

    assert result.is_valid
    assert result.value == "accepted"
    assert result.diagnostics == (warning,)

    with pytest.raises(DomainValidationError) as captured:
        ValidationResult.success("accepted", make_diagnostic())

    assert captured.value.code == "validation_result.value_with_error"


def test_failure_requires_at_least_one_error():
    error = make_diagnostic()

    result = ValidationResult.failure(error)

    assert not result.is_valid
    assert result.value is None
    assert result.diagnostics == (error,)

    with pytest.raises(DomainValidationError) as missing:
        ValidationResult.failure()
    with pytest.raises(DomainValidationError) as warning_only:
        ValidationResult.failure(make_diagnostic(severity=DiagnosticSeverity.WARNING))

    assert missing.value.code == "validation_result.missing_error"
    assert warning_only.value.code == "validation_result.missing_error"


def test_validation_result_requires_an_immutable_diagnostic_tuple():
    diagnostic = make_diagnostic()

    with pytest.raises(DomainValidationError) as mutable:
        ValidationResult(value=None, diagnostics=[diagnostic])  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError) as wrong_item:
        ValidationResult(value=None, diagnostics=("not-a-diagnostic",))  # type: ignore[arg-type]

    assert mutable.value.code == "validation_result.diagnostics_type"
    assert wrong_item.value.code == "validation_result.diagnostic_type"
