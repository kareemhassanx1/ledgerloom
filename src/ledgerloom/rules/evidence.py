"""Immutable, machine-readable evidence returned by matching rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from ledgerloom.rules.errors import RuleError

type EvidenceScalar = str | int | bool | None

_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_FACT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_MAX_CODE_LENGTH = 128
_MAX_FACT_NAME_LENGTH = 64
_MAX_TEXT_VALUE_LENGTH = 512


def validate_rule_code(value: object, *, path: str, kind: str) -> str:
    """Validate a stable dotted identifier used in the rules API."""

    if not isinstance(value, str) or not _CODE_PATTERN.fullmatch(value):
        raise RuleError(
            code=f"rule.{kind}_format",
            path=path,
            message=f"{kind.replace('_', ' ')} must be a dotted lowercase identifier",
        )
    if len(value) > _MAX_CODE_LENGTH:
        raise RuleError(
            code=f"rule.{kind}_too_long",
            path=path,
            message=f"{kind.replace('_', ' ')} must contain at most {_MAX_CODE_LENGTH} characters",
        )
    return value


class MatchVerdict(StrEnum):
    """The three outcomes a match rule can report without using truthiness."""

    MATCH = "match"
    NO_MATCH = "no_match"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True, slots=True, order=True)
class EvidenceFact:
    """One named scalar that supports a rule verdict."""

    name: str
    value: EvidenceScalar

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _FACT_NAME_PATTERN.fullmatch(self.name):
            raise RuleError(
                code="rule.fact_name_format",
                path="$.evidence.facts.name",
                message="fact name must be a lowercase identifier",
            )
        if len(self.name) > _MAX_FACT_NAME_LENGTH:
            raise RuleError(
                code="rule.fact_name_too_long",
                path="$.evidence.facts.name",
                message=f"fact name must contain at most {_MAX_FACT_NAME_LENGTH} characters",
            )
        if not isinstance(self.value, str | int | bool) and self.value is not None:
            raise RuleError(
                code="rule.fact_value_type",
                path=f"$.evidence.facts.{self.name}",
                message="fact value must be text, an integer, a boolean, or None",
            )
        if isinstance(self.value, str):
            if self.value != self.value.strip() or not self.value.isprintable():
                raise RuleError(
                    code="rule.fact_value_format",
                    path=f"$.evidence.facts.{self.name}",
                    message="text fact value must be printable without surrounding whitespace",
                )
            if len(self.value) > _MAX_TEXT_VALUE_LENGTH:
                raise RuleError(
                    code="rule.fact_value_too_long",
                    path=f"$.evidence.facts.{self.name}",
                    message=(
                        f"text fact value must contain at most {_MAX_TEXT_VALUE_LENGTH} characters"
                    ),
                )


@dataclass(frozen=True, slots=True)
class RuleEvidence:
    """A verdict with stable reasons, ordered facts, and nested child evidence."""

    rule_id: str
    verdict: MatchVerdict
    reason_code: str
    facts: tuple[EvidenceFact, ...] = ()
    children: tuple[RuleEvidence, ...] = ()

    def __post_init__(self) -> None:
        validate_rule_code(self.rule_id, path="$.evidence.rule_id", kind="id")
        if not isinstance(self.verdict, MatchVerdict):
            raise RuleError(
                code="rule.verdict_type",
                path="$.evidence.verdict",
                message="verdict must be a MatchVerdict",
            )
        validate_rule_code(
            self.reason_code,
            path="$.evidence.reason_code",
            kind="reason_code",
        )
        if not isinstance(self.facts, tuple):
            raise RuleError(
                code="rule.facts_type",
                path="$.evidence.facts",
                message="facts must be a tuple",
            )
        if not all(isinstance(fact, EvidenceFact) for fact in self.facts):
            raise RuleError(
                code="rule.fact_type",
                path="$.evidence.facts",
                message="facts must contain only EvidenceFact values",
            )
        names = tuple(fact.name for fact in self.facts)
        if len(names) != len(set(names)):
            raise RuleError(
                code="rule.fact_duplicate",
                path="$.evidence.facts",
                message="fact names must be unique within one evidence item",
            )
        object.__setattr__(self, "facts", tuple(sorted(self.facts)))

        if not isinstance(self.children, tuple):
            raise RuleError(
                code="rule.children_type",
                path="$.evidence.children",
                message="children must be a tuple",
            )
        if not all(isinstance(child, RuleEvidence) for child in self.children):
            raise RuleError(
                code="rule.child_type",
                path="$.evidence.children",
                message="children must contain only RuleEvidence values",
            )

    @classmethod
    def create(
        cls,
        rule_id: str,
        verdict: MatchVerdict,
        reason_code: str,
        *,
        facts: tuple[EvidenceFact, ...] = (),
        children: tuple[RuleEvidence, ...] = (),
    ) -> Self:
        """Create evidence while preserving the concrete evidence type."""

        return cls(rule_id, verdict, reason_code, facts, children)

    @property
    def is_match(self) -> bool:
        return self.verdict is MatchVerdict.MATCH

    def fact(self, name: str) -> EvidenceScalar:
        """Return one named fact, raising ``KeyError`` when it was not recorded."""

        for fact in self.facts:
            if fact.name == name:
                return fact.value
        raise KeyError(name)

    def walk(self) -> tuple[RuleEvidence, ...]:
        """Return deterministic pre-order evidence for audit and reporting."""

        return (self, *(item for child in self.children for item in child.walk()))

    @property
    def failures(self) -> tuple[RuleEvidence, ...]:
        """Return every nested item that explicitly rejected the match."""

        return tuple(
            evidence
            for evidence in self.walk()
            if evidence.verdict is MatchVerdict.NO_MATCH
        )
