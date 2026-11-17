from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal
from typing import Any, Mapping, Sequence

from .evaluator import (
    ELIGIBLE, INELIGIBLE, INVALID, UNKNOWN, Evaluation, SafeEvaluator,
)
from .models import (
    BudgetFigure, ConditionContribution, PairAnalysisError, PairAnalysisReport,
    PersonComparison, PersonVerdict, PolicyVersion, FactSnapshot,
)

# Movement labels.  Unknown never collapses into ineligible.
STAYED_ELIGIBLE = 'stayed_eligible'
STAYED_INELIGIBLE = 'stayed_ineligible'
GAINED = 'gained'           # ineligible -> eligible
LOST = 'lost'               # eligible -> ineligible
UNKNOWN_EITHER = 'unknown'  # unknown on either side, in any combination
INVALID_EITHER = 'invalid'  # expression could not be evaluated safely

_MOVEMENTS = (
    STAYED_ELIGIBLE, STAYED_INELIGIBLE, GAINED, LOST, UNKNOWN_EITHER, INVALID_EITHER,
)


class PolicyPairAnalyzer:
    """Produces an independent paired analysis from one fact snapshot.

    The analyzer is read-only with respect to application state: it never
    writes records or events, so running it cannot change any current
    approval outcome.  Both policy versions are interpreted through their
    own :class:`PolicyVersion` definition on the same snapshot.
    """

    def __init__(self, evaluator: SafeEvaluator | None = None):
        self.evaluator = evaluator or SafeEvaluator()

    def analyze(self, snapshot: FactSnapshot, old: PolicyVersion,
                new: PolicyVersion, analysis_id: str | None = None) -> PairAnalysisReport:
        if old.label == new.label:
            raise PairAnalysisError('old and new policy need distinct version labels')
        if old.currency != new.currency:
            raise PairAnalysisError(
                f'currency_mismatch:{old.currency}:{new.currency}')
        unit_errors: list[str] = []
        comparisons: list[PersonComparison] = []
        for person in snapshot.persons:
            old_verdict = self._verdict(person, old, unit_errors)
            new_verdict = self._verdict(person, new, unit_errors)
            comparisons.append(self._compare_person(person, old_verdict, new_verdict))

        movements = self._classify(comparisons)
        contributions = self._contributions(snapshot, old, new, unit_errors)
        budget = {
            old.label: self._budget(old, comparisons, side='old'),
            new.label: self._budget(new, comparisons, side='new'),
        }
        old_required = budget[old.label].required
        new_required = budget[new.label].required
        budget_delta = {
            'required_delta': new_required - old_required,
            'shortfall_delta': budget[new.label].shortfall - budget[old.label].shortfall,
            'eligible_count_delta': (budget[new.label].eligible_count
                                     - budget[old.label].eligible_count),
        }
        identifier = analysis_id or f'pair-{uuid.uuid4().hex[:12]}'
        return PairAnalysisReport(
            analysis_id=identifier,
            snapshot_fingerprint=snapshot.fingerprint,
            captured_at=snapshot.captured_at,
            old_policy=self._policy_view(old),
            new_policy=self._policy_view(new),
            movements=movements,
            movement_counts={key: len(movements[key]) for key in _MOVEMENTS},
            condition_contributions=tuple(contributions),
            budget=budget,
            budget_delta=budget_delta,
            person_comparisons=tuple(comparisons),
            unit_errors=tuple(sorted(set(unit_errors))),
        )

    def _verdict(self, person, policy: PolicyVersion, unit_errors: list[str]) -> PersonVerdict:
        evaluation: Evaluation = self.evaluator.evaluate(
            policy.expression, person.facts, policy.label)
        for reason in evaluation.reasons:
            if reason.startswith('unit_mismatch'):
                unit_errors.append(f'{person.person_id}:{policy.label}:{reason}')
        clause_results = {clause.clause_id: clause.result
                          for clause in evaluation.clauses}
        clause_reasons = {clause.clause_id: clause.reasons
                          for clause in evaluation.clauses}
        benefit = policy.benefit if evaluation.result == ELIGIBLE else Decimal('0.00')
        return PersonVerdict(
            person_id=person.person_id,
            result=evaluation.result,
            missing=evaluation.missing,
            reasons=evaluation.reasons,
            facts_used=evaluation.facts_used,
            clause_results=clause_results,
            clause_reasons=clause_reasons,
            fact_sources={name: person.fact_sources[name]
                          for name in evaluation.facts_used
                          if name in person.fact_sources},
            fact_values={name: person.facts[name]
                         for name in evaluation.facts_used if name in person.facts},
            benefit_amount=benefit,
        )

    def _compare_person(self, person, old: PersonVerdict,
                        new: PersonVerdict) -> PersonComparison:
        movement = self._movement(old.result, new.result)
        decisive = self._decisive_clauses(old, new)
        delta = new.benefit_amount - old.benefit_amount
        return PersonComparison(
            person_id=person.person_id,
            old_result=old.result,
            new_result=new.result,
            movement=movement,
            old_benefit=old.benefit_amount,
            new_benefit=new.benefit_amount,
            benefit_delta=delta,
            decisive_clauses=tuple(decisive),
            old_verdict=old,
            new_verdict=new,
            fact_sources=dict(person.fact_sources),
        )

    @staticmethod
    def _movement(old_result: str, new_result: str) -> str:
        if INVALID in (old_result, new_result):
            return INVALID_EITHER
        if UNKNOWN in (old_result, new_result):
            return UNKNOWN_EITHER
        if old_result == ELIGIBLE and new_result == ELIGIBLE:
            return STAYED_ELIGIBLE
        if old_result == INELIGIBLE and new_result == INELIGIBLE:
            return STAYED_INELIGIBLE
        if old_result == INELIGIBLE and new_result == ELIGIBLE:
            return GAINED
        return LOST

    @staticmethod
    def _decisive_clauses(old: PersonVerdict, new: PersonVerdict) -> list[str]:
        """Clauses whose own result flipped and therefore explain the move."""
        shared = sorted(set(old.clause_results) & set(new.clause_results))
        return [clause_id for clause_id in shared
                if old.clause_results[clause_id] != new.clause_results[clause_id]]

    def _classify(self, comparisons: Sequence[PersonComparison]) -> dict[str, tuple[str, ...]]:
        buckets: dict[str, list[str]] = {key: [] for key in _MOVEMENTS}
        for comparison in comparisons:
            buckets[comparison.movement].append(comparison.person_id)
        return {key: tuple(sorted(values)) for key, values in buckets.items()}

    def _contributions(self, snapshot: FactSnapshot, old: PolicyVersion,
                       new: PolicyVersion, unit_errors: list[str]) -> list[ConditionContribution]:
        old_clauses = self.evaluator.split_clauses(old.expression)
        new_clauses = self.evaluator.split_clauses(new.expression)
        contributions: list[ConditionContribution] = []
        for index, (old_text, new_text) in enumerate(
                self._zip_clauses(old_clauses, new_clauses), 1):
            clause_id = f'clause-{index}'
            gained: list[str] = []
            lost: list[str] = []
            unknown_old: list[str] = []
            unknown_new: list[str] = []
            for person in snapshot.persons:
                old_trace = self.evaluator._trace_clause(index, old_text, person.facts)
                new_trace = self.evaluator._trace_clause(index, new_text, person.facts)
                for trace, policy in ((old_trace, old), (new_trace, new)):
                    for reason in trace.reasons:
                        if reason.startswith('unit_mismatch'):
                            unit_errors.append(
                                f'{person.person_id}:{policy.label}:{clause_id}:{reason}')
                if old_trace.result == UNKNOWN:
                    unknown_old.append(person.person_id)
                if new_trace.result == UNKNOWN:
                    unknown_new.append(person.person_id)
                if old_trace.result == INELIGIBLE and new_trace.result == ELIGIBLE:
                    gained.append(person.person_id)
                if old_trace.result == ELIGIBLE and new_trace.result == INELIGIBLE:
                    lost.append(person.person_id)
            gained_amount = (new.benefit * len(gained)).quantize(Decimal('0.01'))
            lost_amount = (old.benefit * len(lost)).quantize(Decimal('0.01'))
            contributions.append(ConditionContribution(
                clause_id=clause_id,
                old_expression=old_text,
                new_expression=new_text,
                changed=old_text != new_text,
                gained=tuple(sorted(gained)),
                lost=tuple(sorted(lost)),
                unknown_old=tuple(sorted(unknown_old)),
                unknown_new=tuple(sorted(unknown_new)),
                gained_amount=gained_amount.quantize(Decimal('0.01')),
                lost_amount=lost_amount.quantize(Decimal('0.01')),
                amount_delta=(gained_amount - lost_amount).quantize(Decimal('0.01')),
            ))
        return contributions

    @staticmethod
    def _zip_clauses(old_clauses: Sequence[str],
                     new_clauses: Sequence[str]) -> Sequence[tuple[str, str]]:
        count = max(len(old_clauses), len(new_clauses))
        padded_old = list(old_clauses) + ['True'] * (count - len(old_clauses))
        padded_new = list(new_clauses) + ['True'] * (count - len(new_clauses))
        return tuple(zip(padded_old, padded_new))

    def _budget(self, policy: PolicyVersion, comparisons: Sequence[PersonComparison],
                side: str) -> BudgetFigure:
        verdict_attr = 'old_verdict' if side == 'old' else 'new_verdict'
        eligible = [item for item in comparisons
                    if getattr(item, verdict_attr).result == ELIGIBLE]
        unknown = [item for item in comparisons
                   if getattr(item, verdict_attr).result == UNKNOWN]
        invalid = [item for item in comparisons
                   if getattr(item, verdict_attr).result == INVALID]
        ineligible = [item for item in comparisons
                      if getattr(item, verdict_attr).result == INELIGIBLE]
        # Unknown and invalid people are excluded from the denominator and
        # from the money calculation.
        required = (policy.benefit * len(eligible)).quantize(Decimal('0.01'))
        shortfall = max(Decimal('0.00'), required - policy.budget_cap)
        return BudgetFigure(
            eligible_count=len(eligible),
            unknown_count=len(unknown),
            ineligible_count=len(ineligible),
            decided_count=len(eligible) + len(ineligible),
            required=required,
            budget_cap=policy.budget_cap,
            within_budget=required <= policy.budget_cap,
            shortfall=shortfall.quantize(Decimal('0.01')),
            currency=policy.currency,
        )

    @staticmethod
    def _policy_view(policy: PolicyVersion) -> Mapping[str, Any]:
        return {
            'label': policy.label,
            'expression': policy.expression,
            'effective_from': policy.effective_from.isoformat(),
            'effective_to': policy.effective_to.isoformat() if policy.effective_to else None,
            'benefit': str(policy.benefit),
            'currency': policy.currency,
            'budget_cap': str(policy.budget_cap),
        }

    @staticmethod
    def stable_analysis_id(snapshot: FactSnapshot, old: PolicyVersion,
                           new: PolicyVersion) -> str:
        raw = '|'.join([
            snapshot.fingerprint, old.label, old.expression, str(old.benefit),
            str(old.budget_cap), new.label, new.expression, str(new.benefit),
            str(new.budget_cap),
        ])
        return 'pair-' + hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
