"""Integer-only, deterministic scoring for reconciliation candidates."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ledgerloom.domain.identifiers import EntryId
from ledgerloom.domain.records import LedgerEntry
from ledgerloom.reconciliation.errors import ScoringError
from ledgerloom.rules import MatchRule, MatchVerdict, RuleEvidence, evaluate_rule
from ledgerloom.rules.evidence import validate_rule_code

MAX_RULE_WEIGHT = 1_000_000
MAX_TOTAL_SCORE = 1_000_000_000
BASIS_POINT_SCALE = 10_000


def _require_integer(
    value: object,
    *,
    path: str,
    code: str,
    minimum: int,
    maximum: int,
    label: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScoringError(
            code=f"{code}_type",
            path=path,
            message=f"{label} must be an integer",
        )
    if not minimum <= value <= maximum:
        raise ScoringError(
            code=f"{code}_range",
            path=path,
            message=f"{label} must be between {minimum} and {maximum}",
        )
    return value


def _candidate_order(score: CandidateScore) -> tuple[int, object, EntryId]:
    return (-score.score, score.candidate.occurred_at, score.candidate.entry_id)


@dataclass(frozen=True, slots=True)
class ScoringRule:
    """Attach exact integer weight and required status to one match rule."""

    rule: MatchRule
    weight: int
    required: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.rule, MatchRule):
            raise ScoringError(
                code="score.rule_type",
                path="$.scoring_rule.rule",
                message="rule must satisfy MatchRule",
            )
        validate_rule_code(
            self.rule.rule_id,
            path="$.scoring_rule.rule.rule_id",
            kind="id",
        )
        _require_integer(
            self.weight,
            path="$.scoring_rule.weight",
            code="score.weight",
            minimum=1,
            maximum=MAX_RULE_WEIGHT,
            label="rule weight",
        )
        if not isinstance(self.required, bool):
            raise ScoringError(
                code="score.required_type",
                path="$.scoring_rule.required",
                message="required must be a boolean",
            )

    @property
    def rule_id(self) -> str:
        return self.rule.rule_id


@dataclass(frozen=True, slots=True)
class CandidateScore:
    """One candidate's exact score and complete per-rule evidence."""

    anchor: LedgerEntry
    candidate: LedgerEntry
    score: int
    maximum_score: int
    evidence: tuple[RuleEvidence, ...]
    required_rules_satisfied: bool
    reason_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.anchor, LedgerEntry):
            raise ScoringError(
                code="score.anchor_type",
                path="$.candidate_score.anchor",
                message="anchor must be a LedgerEntry",
            )
        if not isinstance(self.candidate, LedgerEntry):
            raise ScoringError(
                code="score.candidate_type",
                path="$.candidate_score.candidate",
                message="candidate must be a LedgerEntry",
            )
        if self.anchor.entry_id == self.candidate.entry_id:
            raise ScoringError(
                code="score.self_candidate",
                path="$.candidate_score.candidate.entry_id",
                message="candidate must not have the anchor entry identifier",
            )
        _require_integer(
            self.maximum_score,
            path="$.candidate_score.maximum_score",
            code="score.maximum",
            minimum=1,
            maximum=MAX_TOTAL_SCORE,
            label="maximum score",
        )
        _require_integer(
            self.score,
            path="$.candidate_score.score",
            code="score.value",
            minimum=0,
            maximum=self.maximum_score,
            label="candidate score",
        )
        if not isinstance(self.evidence, tuple) or not self.evidence:
            raise ScoringError(
                code="score.evidence_type",
                path="$.candidate_score.evidence",
                message="evidence must be a non-empty tuple",
            )
        if not all(isinstance(item, RuleEvidence) for item in self.evidence):
            raise ScoringError(
                code="score.evidence_item_type",
                path="$.candidate_score.evidence",
                message="evidence must contain only RuleEvidence values",
            )
        rule_ids = tuple(item.rule_id for item in self.evidence)
        if len(rule_ids) != len(set(rule_ids)):
            raise ScoringError(
                code="score.evidence_duplicate",
                path="$.candidate_score.evidence",
                message="evidence rule identifiers must be unique",
            )
        if not isinstance(self.required_rules_satisfied, bool):
            raise ScoringError(
                code="score.required_status_type",
                path="$.candidate_score.required_rules_satisfied",
                message="required rule status must be a boolean",
            )
        validate_rule_code(
            self.reason_code,
            path="$.candidate_score.reason_code",
            kind="reason_code",
        )

    @property
    def confidence_basis_points(self) -> int:
        """Return floor-rounded score share on an exact 0..10,000 scale."""

        return (self.score * BASIS_POINT_SCALE) // self.maximum_score

    @property
    def matched_rule_ids(self) -> tuple[str, ...]:
        return tuple(
            item.rule_id for item in self.evidence if item.verdict is MatchVerdict.MATCH
        )


@dataclass(frozen=True, slots=True, init=False)
class MatchScorer:
    """Evaluate one immutable weighted rule set and rank candidates stably."""

    _rules: tuple[ScoringRule, ...]
    _maximum_score: int

    def __init__(self, rules: tuple[ScoringRule, ...]) -> None:
        if not isinstance(rules, tuple):
            raise ScoringError(
                code="score.rules_type",
                path="$.rules",
                message="scoring rules must be a tuple",
            )
        if not rules:
            raise ScoringError(
                code="score.rules_empty",
                path="$.rules",
                message="scorer must contain at least one rule",
            )
        if not all(isinstance(rule, ScoringRule) for rule in rules):
            raise ScoringError(
                code="score.rule_config_type",
                path="$.rules",
                message="rules must contain only ScoringRule values",
            )
        rule_ids = tuple(rule.rule_id for rule in rules)
        if len(rule_ids) != len(set(rule_ids)):
            raise ScoringError(
                code="score.rule_id_duplicate",
                path="$.rules",
                message="scoring rule identifiers must be unique",
            )
        maximum_score = sum(rule.weight for rule in rules)
        if maximum_score > MAX_TOTAL_SCORE:
            raise ScoringError(
                code="score.total_too_large",
                path="$.rules",
                message=f"total score must not exceed {MAX_TOTAL_SCORE}",
            )
        object.__setattr__(self, "_rules", rules)
        object.__setattr__(self, "_maximum_score", maximum_score)

    @property
    def rules(self) -> tuple[ScoringRule, ...]:
        return self._rules

    @property
    def maximum_score(self) -> int:
        return self._maximum_score

    def score(self, anchor: LedgerEntry, candidate: LedgerEntry) -> CandidateScore:
        if not isinstance(anchor, LedgerEntry):
            raise ScoringError(
                code="score.anchor_type",
                path="$.anchor",
                message="anchor must be a LedgerEntry",
            )
        if not isinstance(candidate, LedgerEntry):
            raise ScoringError(
                code="score.candidate_type",
                path="$.candidate",
                message="candidate must be a LedgerEntry",
            )
        if anchor.entry_id == candidate.entry_id:
            raise ScoringError(
                code="score.self_candidate",
                path="$.candidate.entry_id",
                message="candidate must not have the anchor entry identifier",
            )

        evidence = tuple(
            evaluate_rule(config.rule, anchor, candidate) for config in self._rules
        )
        score = sum(
            config.weight
            for config, item in zip(self._rules, evidence, strict=True)
            if item.verdict is MatchVerdict.MATCH
        )
        required_evidence = tuple(
            item
            for config, item in zip(self._rules, evidence, strict=True)
            if config.required
        )
        if any(item.verdict is MatchVerdict.NO_MATCH for item in required_evidence):
            required_rules_satisfied = False
            reason_code = "score.required_rule_rejected"
        elif any(item.verdict is MatchVerdict.INDETERMINATE for item in required_evidence):
            required_rules_satisfied = False
            reason_code = "score.required_rule_indeterminate"
        else:
            required_rules_satisfied = True
            reason_code = "score.rules_evaluated"
        return CandidateScore(
            anchor,
            candidate,
            score,
            self._maximum_score,
            evidence,
            required_rules_satisfied,
            reason_code,
        )

    def rank(
        self,
        anchor: LedgerEntry,
        candidates: Iterable[LedgerEntry],
    ) -> tuple[CandidateScore, ...]:
        """Score candidates and order by score, occurrence instant, then identifier."""

        if not isinstance(anchor, LedgerEntry):
            raise ScoringError(
                code="score.anchor_type",
                path="$.anchor",
                message="anchor must be a LedgerEntry",
            )
        if isinstance(candidates, str | bytes) or not isinstance(candidates, Iterable):
            raise ScoringError(
                code="score.candidates_type",
                path="$.candidates",
                message="candidates must be an iterable of LedgerEntry values",
            )
        materialized = tuple(candidates)
        if not all(isinstance(candidate, LedgerEntry) for candidate in materialized):
            raise ScoringError(
                code="score.candidate_type",
                path="$.candidates",
                message="candidates must contain only LedgerEntry values",
            )
        candidate_ids = tuple(candidate.entry_id for candidate in materialized)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ScoringError(
                code="score.candidate_duplicate",
                path="$.candidates",
                message="candidate entry identifiers must be unique",
            )
        scored = (self.score(anchor, item) for item in materialized)
        return tuple(sorted(scored, key=_candidate_order))
