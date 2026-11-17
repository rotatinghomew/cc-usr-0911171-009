from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping, Sequence

from .evaluator import INELIGIBLE, UNKNOWN


class PairAnalysisError(ValueError):
    pass


def _money(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:  # pragma: no cover - defensive
        raise PairAnalysisError(f'invalid amount: {value!r}') from exc
    return result.quantize(Decimal('0.01'))


@dataclass(frozen=True)
class FactSnapshot:
    """Immutable fact set shared by both policy versions.

    ``captured_at`` and ``fingerprint`` pin the snapshot so an analysis can
    always be traced back to the exact facts it was computed from.
    """

    persons: tuple['PersonFacts', ...]
    captured_at: dt.datetime
    units: Mapping[str, str] = field(default_factory=dict)
    fingerprint: str = ''

    def __post_init__(self) -> None:
        if not self.persons:
            raise PairAnalysisError('snapshot requires at least one person')
        keys = [person.person_id for person in self.persons]
        if len(set(keys)) != len(keys):
            raise PairAnalysisError('duplicate person_id in snapshot')
        if not self.fingerprint:
            digest = self._compute_fingerprint()
            object.__setattr__(self, 'fingerprint', digest)

    def _compute_fingerprint(self) -> str:
        payload = [
            {
                'person_id': person.person_id,
                'facts': person.facts,
                'fact_sources': dict(person.fact_sources),
                'as_of': person.as_of.isoformat() if person.as_of else None,
            }
            for person in self.persons
        ]
        raw = json.dumps({'units': dict(self.units), 'persons': payload},
                         ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    @classmethod
    def build(cls, rows: Sequence[Mapping[str, Any]], units: Mapping[str, str] | None = None,
              captured_at: dt.datetime | None = None) -> 'FactSnapshot':
        persons = tuple(PersonFacts.from_row(row) for row in rows)
        return cls(persons, captured_at or dt.datetime.now(dt.timezone.utc),
                   dict(units or {}))


@dataclass(frozen=True)
class PersonFacts:
    person_id: str
    facts: Mapping[str, Any]
    fact_sources: Mapping[str, str]
    as_of: dt.date | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> 'PersonFacts':
        person_id = str(row.get('id') or row.get('person_id') or '').strip()
        if len(person_id) < 2:
            raise PairAnalysisError('person id is required')
        facts = dict(row.get('facts', {}))
        if not isinstance(facts, Mapping):
            raise PairAnalysisError('facts must be a mapping')
        sources = row.get('fact_sources')
        if sources is None:
            # Default provenance: every fact is attributed to the snapshot row.
            sources = {name: f'snapshot:{person_id}:{name}' for name in sorted(facts)}
        if not isinstance(sources, Mapping):
            raise PairAnalysisError('fact_sources must be a mapping')
        missing_sources = sorted(set(facts) - set(sources))
        if missing_sources:
            raise PairAnalysisError(
                'missing fact source for ' + ','.join(missing_sources))
        as_of = row.get('as_of')
        if isinstance(as_of, str):
            as_of = dt.date.fromisoformat(as_of)
        return cls(person_id, dict(facts), dict(sources), as_of)


@dataclass(frozen=True)
class PolicyVersion:
    """One effective version of the eligibility rule."""

    label: str
    expression: str
    effective_from: dt.date
    benefit: Decimal
    currency: str
    budget_cap: Decimal
    effective_to: dt.date | None = None

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise PairAnalysisError('policy label is required')
        if not self.expression.strip():
            raise PairAnalysisError('policy expression is required')
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise PairAnalysisError('effective period is reversed')
        object.__setattr__(self, 'benefit', _money(self.benefit))
        object.__setattr__(self, 'budget_cap', _money(self.budget_cap))
        if self.benefit < 0 or self.budget_cap < 0:
            raise PairAnalysisError('benefit and budget cap cannot be negative')

    @classmethod
    def from_mapping(cls, label: str, value: Mapping[str, Any]) -> 'PolicyVersion':
        return cls(
            label=label,
            expression=str(value['expression']),
            effective_from=dt.date.fromisoformat(str(value['effective_from'])),
            effective_to=(dt.date.fromisoformat(str(value['effective_to']))
                          if value.get('effective_to') else None),
            benefit=_money(value.get('benefit', '0')),
            currency=str(value.get('currency', 'CNY')),
            budget_cap=_money(value.get('budget_cap', '0')),
        )


@dataclass(frozen=True)
class PersonVerdict:
    """Per-person evaluation under one policy version, with provenance."""

    person_id: str
    result: str
    missing: tuple[str, ...]
    reasons: tuple[str, ...]
    facts_used: tuple[str, ...]
    clause_results: Mapping[str, str]
    clause_reasons: Mapping[str, tuple[str, ...]]
    fact_sources: Mapping[str, str]
    fact_values: Mapping[str, Any]
    benefit_amount: Decimal

    @property
    def decided(self) -> bool:
        return self.result in {'eligible', INELIGIBLE}

    @property
    def is_unknown(self) -> bool:
        return self.result == UNKNOWN


@dataclass(frozen=True)
class PersonComparison:
    """Paired verdict for one person across the two policy versions."""

    person_id: str
    old_result: str
    new_result: str
    movement: str
    old_benefit: Decimal
    new_benefit: Decimal
    benefit_delta: Decimal
    decisive_clauses: tuple[str, ...]
    old_verdict: PersonVerdict
    new_verdict: PersonVerdict
    fact_sources: Mapping[str, str]


@dataclass(frozen=True)
class BudgetFigure:
    eligible_count: int
    unknown_count: int
    ineligible_count: int
    decided_count: int
    required: Decimal
    budget_cap: Decimal
    within_budget: bool
    shortfall: Decimal
    currency: str


@dataclass(frozen=True)
class ConditionContribution:
    """How one matched clause moved people and money between versions."""

    clause_id: str
    old_expression: str
    new_expression: str
    changed: bool
    gained: tuple[str, ...]
    lost: tuple[str, ...]
    unknown_old: tuple[str, ...]
    unknown_new: tuple[str, ...]
    gained_amount: Decimal
    lost_amount: Decimal
    amount_delta: Decimal


@dataclass(frozen=True)
class PairAnalysisReport:
    analysis_id: str
    snapshot_fingerprint: str
    captured_at: dt.datetime
    old_policy: Mapping[str, Any]
    new_policy: Mapping[str, Any]
    movements: Mapping[str, tuple[str, ...]]
    movement_counts: Mapping[str, int]
    condition_contributions: tuple[ConditionContribution, ...]
    budget: Mapping[str, BudgetFigure]
    budget_delta: Mapping[str, Decimal]
    person_comparisons: tuple[PersonComparison, ...]
    unit_errors: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self) | {
            'movements': {key: list(value) for key, value in self.movements.items()},
        }
