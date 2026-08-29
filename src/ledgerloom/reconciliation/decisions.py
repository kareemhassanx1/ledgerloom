"""Ambiguity decisions and deterministic cross-anchor conflict policies."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from ledgerloom.domain.identifiers import EntryId
from ledgerloom.domain.records import LedgerEntry
from ledgerloom.reconciliation.errors import DecisionError
from ledgerloom.reconciliation.scoring import MAX_TOTAL_SCORE, CandidateScore
from ledgerloom.rules.evidence import validate_rule_code


def _entry_order(entry: LedgerEntry) -> tuple[object, EntryId]:
    return (entry.occurred_at, entry.entry_id)


def _score_order(score: CandidateScore) -> tuple[int, object, EntryId]:
    return (-score.score, score.candidate.occurred_at, score.candidate.entry_id)


def _require_policy_integer(value: object, *, path: str, code: str, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DecisionError(
            code=f"{code}_type",
            path=path,
            message=f"{label} must be an integer",
        )
    if not 0 <= value <= MAX_TOTAL_SCORE:
        raise DecisionError(
            code=f"{code}_range",
            path=path,
            message=f"{label} must be between 0 and {MAX_TOTAL_SCORE}",
        )
    return value


class DecisionStatus(StrEnum):
    """Outcome of selecting one candidate for an anchor entry."""

    MATCHED = "matched"
    UNMATCHED = "unmatched"
    AMBIGUOUS = "ambiguous"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class MatchDecision:
    """One anchor's ranked evidence and final selection state."""

    anchor: LedgerEntry
    status: DecisionStatus
    reason_code: str
    ranking: tuple[CandidateScore, ...] = ()
    selection: CandidateScore | None = None
    contenders: tuple[CandidateScore, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.anchor, LedgerEntry):
            raise DecisionError(
                code="decision.anchor_type",
                path="$.decision.anchor",
                message="anchor must be a LedgerEntry",
            )
        if not isinstance(self.status, DecisionStatus):
            raise DecisionError(
                code="decision.status_type",
                path="$.decision.status",
                message="status must be a DecisionStatus",
            )
        validate_rule_code(
            self.reason_code,
            path="$.decision.reason_code",
            kind="reason_code",
        )
        if not isinstance(self.ranking, tuple) or not all(
            isinstance(item, CandidateScore) for item in self.ranking
        ):
            raise DecisionError(
                code="decision.ranking_type",
                path="$.decision.ranking",
                message="ranking must be a tuple of CandidateScore values",
            )
        candidate_ids = tuple(item.candidate.entry_id for item in self.ranking)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise DecisionError(
                code="decision.candidate_duplicate",
                path="$.decision.ranking",
                message="ranked candidate identifiers must be unique",
            )
        if any(item.anchor != self.anchor for item in self.ranking):
            raise DecisionError(
                code="decision.score_anchor_mismatch",
                path="$.decision.ranking",
                message="every score must belong to the decision anchor",
            )
        if self.ranking != tuple(sorted(self.ranking, key=_score_order)):
            raise DecisionError(
                code="decision.ranking_order",
                path="$.decision.ranking",
                message="ranking must use deterministic score and entry ordering",
            )
        if self.selection is not None and not isinstance(self.selection, CandidateScore):
            raise DecisionError(
                code="decision.selection_type",
                path="$.decision.selection",
                message="selection must be a CandidateScore or None",
            )
        if not isinstance(self.contenders, tuple) or not all(
            isinstance(item, CandidateScore) for item in self.contenders
        ):
            raise DecisionError(
                code="decision.contenders_type",
                path="$.decision.contenders",
                message="contenders must be a tuple of CandidateScore values",
            )
        if any(item not in self.ranking for item in self.contenders):
            raise DecisionError(
                code="decision.contender_unknown",
                path="$.decision.contenders",
                message="every contender must occur in the ranking",
            )
        contender_ids = tuple(item.candidate.entry_id for item in self.contenders)
        if len(contender_ids) != len(set(contender_ids)):
            raise DecisionError(
                code="decision.contender_duplicate",
                path="$.decision.contenders",
                message="contender identifiers must be unique",
            )
        expected_contenders = tuple(item for item in self.ranking if item in self.contenders)
        if self.contenders != expected_contenders:
            raise DecisionError(
                code="decision.contender_order",
                path="$.decision.contenders",
                message="contenders must preserve ranking order",
            )
        if self.status is DecisionStatus.MATCHED:
            if self.selection is None or self.contenders != (self.selection,):
                raise DecisionError(
                    code="decision.matched_shape",
                    path="$.decision",
                    message="matched decision must select its sole contender",
                )
        elif self.selection is not None:
            raise DecisionError(
                code="decision.unselected_shape",
                path="$.decision.selection",
                message="only a matched decision may contain a selection",
            )
        if self.status is DecisionStatus.AMBIGUOUS and len(self.contenders) < 2:
            raise DecisionError(
                code="decision.ambiguous_shape",
                path="$.decision.contenders",
                message="ambiguous decision must contain at least two contenders",
            )
        if self.status is DecisionStatus.UNMATCHED and self.contenders:
            raise DecisionError(
                code="decision.unmatched_shape",
                path="$.decision.contenders",
                message="unmatched decision must not contain contenders",
            )
        if self.status is DecisionStatus.CONFLICT and len(self.contenders) != 1:
            raise DecisionError(
                code="decision.conflict_shape",
                path="$.decision.contenders",
                message="conflict decision must retain its one disputed contender",
            )


@dataclass(frozen=True, slots=True)
class DecisionPolicy:
    """Select a qualified leader only when its score lead is sufficient."""

    minimum_score: int
    minimum_margin: int = 0

    def __post_init__(self) -> None:
        _require_policy_integer(
            self.minimum_score,
            path="$.decision_policy.minimum_score",
            code="decision.minimum_score",
            label="minimum score",
        )
        _require_policy_integer(
            self.minimum_margin,
            path="$.decision_policy.minimum_margin",
            code="decision.minimum_margin",
            label="minimum margin",
        )

    def decide(
        self,
        anchor: LedgerEntry,
        scores: Iterable[CandidateScore],
    ) -> MatchDecision:
        if not isinstance(anchor, LedgerEntry):
            raise DecisionError(
                code="decision.anchor_type",
                path="$.anchor",
                message="anchor must be a LedgerEntry",
            )
        if isinstance(scores, str | bytes) or not isinstance(scores, Iterable):
            raise DecisionError(
                code="decision.scores_type",
                path="$.scores",
                message="scores must be an iterable of CandidateScore values",
            )
        materialized = tuple(scores)
        if not all(isinstance(item, CandidateScore) for item in materialized):
            raise DecisionError(
                code="decision.score_type",
                path="$.scores",
                message="scores must contain only CandidateScore values",
            )
        ranking = tuple(sorted(materialized, key=_score_order))
        candidate_ids = tuple(item.candidate.entry_id for item in ranking)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise DecisionError(
                code="decision.candidate_duplicate",
                path="$.scores",
                message="candidate identifiers must be unique",
            )
        if any(item.anchor != anchor for item in ranking):
            raise DecisionError(
                code="decision.score_anchor_mismatch",
                path="$.scores",
                message="every score must belong to the requested anchor",
            )
        score_scales = {item.maximum_score for item in ranking}
        if len(score_scales) > 1:
            raise DecisionError(
                code="decision.score_scale_mismatch",
                path="$.scores",
                message="candidate scores must use one maximum score",
            )

        qualified = tuple(
            item
            for item in ranking
            if item.required_rules_satisfied and item.score >= self.minimum_score
        )
        if not qualified:
            reason = "decision.no_candidates" if not ranking else "decision.none_qualified"
            return MatchDecision(anchor, DecisionStatus.UNMATCHED, reason, ranking)

        leader = qualified[0]
        contenders = tuple(
            item
            for item in qualified
            if item.score == leader.score or leader.score - item.score < self.minimum_margin
        )
        if len(contenders) > 1:
            return MatchDecision(
                anchor,
                DecisionStatus.AMBIGUOUS,
                "decision.insufficient_margin",
                ranking,
                contenders=contenders,
            )
        return MatchDecision(
            anchor,
            DecisionStatus.MATCHED,
            "decision.unique_leader",
            ranking,
            selection=leader,
            contenders=(leader,),
        )


class ConflictStrategy(StrEnum):
    """How to handle multiple anchors selecting the same candidate."""

    REJECT_ALL = "reject_all"
    PREFER_HIGHER_SCORE = "prefer_higher_score"


@dataclass(frozen=True, slots=True)
class ConflictPolicy:
    """Resolve candidate reuse without relying on processing order."""

    strategy: ConflictStrategy = ConflictStrategy.REJECT_ALL

    def __post_init__(self) -> None:
        if not isinstance(self.strategy, ConflictStrategy):
            raise DecisionError(
                code="conflict.strategy_type",
                path="$.conflict_policy.strategy",
                message="strategy must be a ConflictStrategy",
            )

    def resolve(self, decisions: Iterable[MatchDecision]) -> tuple[MatchDecision, ...]:
        if isinstance(decisions, str | bytes) or not isinstance(decisions, Iterable):
            raise DecisionError(
                code="conflict.decisions_type",
                path="$.decisions",
                message="decisions must be an iterable of MatchDecision values",
            )
        materialized = tuple(decisions)
        if not all(isinstance(item, MatchDecision) for item in materialized):
            raise DecisionError(
                code="conflict.decision_type",
                path="$.decisions",
                message="decisions must contain only MatchDecision values",
            )
        ordered = tuple(sorted(materialized, key=lambda item: _entry_order(item.anchor)))
        anchor_ids = tuple(item.anchor.entry_id for item in ordered)
        if len(anchor_ids) != len(set(anchor_ids)):
            raise DecisionError(
                code="conflict.anchor_duplicate",
                path="$.decisions",
                message="anchor identifiers must be unique",
            )

        groups: dict[EntryId, list[int]] = {}
        for index, decision in enumerate(ordered):
            if decision.status is DecisionStatus.MATCHED:
                assert decision.selection is not None
                groups.setdefault(decision.selection.candidate.entry_id, []).append(index)

        resolved = list(ordered)
        for indexes in groups.values():
            if len(indexes) < 2:
                continue
            losers = indexes
            loser_reason = "conflict.shared_candidate"
            if self.strategy is ConflictStrategy.PREFER_HIGHER_SCORE:
                score_scales = {
                    resolved[index].selection.maximum_score  # type: ignore[union-attr]
                    for index in indexes
                }
                if len(score_scales) > 1:
                    raise DecisionError(
                        code="conflict.score_scale_mismatch",
                        path="$.decisions",
                        message="score preference requires one maximum score",
                    )
                ranked_indexes = sorted(
                    indexes,
                    key=lambda index: (
                        -resolved[index].selection.score,  # type: ignore[union-attr]
                        *_entry_order(resolved[index].anchor),
                    ),
                )
                first = resolved[ranked_indexes[0]].selection
                second = resolved[ranked_indexes[1]].selection
                assert first is not None and second is not None
                if first.score > second.score:
                    losers = ranked_indexes[1:]
                    loser_reason = "conflict.lower_score"
                else:
                    loser_reason = "conflict.equal_top_score"
            for index in losers:
                disputed = resolved[index]
                assert disputed.selection is not None
                resolved[index] = MatchDecision(
                    disputed.anchor,
                    DecisionStatus.CONFLICT,
                    loser_reason,
                    disputed.ranking,
                    contenders=(disputed.selection,),
                )
        return tuple(resolved)
