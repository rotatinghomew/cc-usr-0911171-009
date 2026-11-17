from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from .analyzer import GAINED, LOST, STAYED_ELIGIBLE, STAYED_INELIGIBLE
from .models import PairAnalysisReport, PersonComparison


class ReconciliationError(RuntimeError):
    pass


class AnalysisReconciler:
    """Rebuilds headline figures from the per-person comparison detail.

    Every count and amount published in a paired report must be derivable
    from ``person_comparisons``; this class performs that rebuild so the
    report can be checked independently of the analyzer.
    """

    def recount_movements(self,
                          comparisons: Sequence[PersonComparison]) -> dict[str, int]:
        counts = {
            STAYED_ELIGIBLE: 0, STAYED_INELIGIBLE: 0, GAINED: 0,
            LOST: 0, 'unknown': 0, 'invalid': 0,
        }
        for comparison in comparisons:
            counts[comparison.movement] += 1
        return counts

    def resum_budget(self, comparisons: Sequence[PersonComparison],
                     side: str) -> dict[str, Any]:
        attr = 'old_verdict' if side == 'old' else 'new_verdict'
        eligible_ids = [item.person_id for item in comparisons
                        if getattr(item, attr).result == 'eligible']
        unknown_ids = [item.person_id for item in comparisons
                       if getattr(item, attr).result == 'unknown']
        ineligible_ids = [item.person_id for item in comparisons
                          if getattr(item, attr).result == 'ineligible']
        required = sum((getattr(item, attr).benefit_amount for item in comparisons),
                       Decimal('0.00'))
        return {
            'eligible_ids': tuple(eligible_ids),
            'unknown_ids': tuple(unknown_ids),
            'ineligible_ids': tuple(ineligible_ids),
            'eligible_count': len(eligible_ids),
            'unknown_count': len(unknown_ids),
            'ineligible_count': len(ineligible_ids),
            'decided_count': len(eligible_ids) + len(ineligible_ids),
            'required': required.quantize(Decimal('0.01')),
        }

    def verify(self, report: PairAnalysisReport) -> tuple[str, ...]:
        problems: list[str] = []
        comparisons = report.person_comparisons
        recounted = self.recount_movements(comparisons)
        for movement, count in recounted.items():
            if report.movement_counts.get(movement) != count:
                problems.append(f'movement_count_mismatch:{movement}')
            listed = report.movements.get(movement, ())
            if len(listed) != count:
                problems.append(f'movement_list_mismatch:{movement}')
        detail_ids = sorted(item.person_id for item in comparisons)
        moved_ids = sorted(person_id for ids in report.movements.values()
                           for person_id in ids)
        if detail_ids != moved_ids:
            problems.append('person_coverage_mismatch')
        for side, label in (('old', report.old_policy['label']),
                            ('new', report.new_policy['label'])):
            rebuilt = self.resum_budget(comparisons, side)
            figure = report.budget[label]
            if rebuilt['eligible_count'] != figure.eligible_count:
                problems.append(f'eligible_count_mismatch:{label}')
            if rebuilt['unknown_count'] != figure.unknown_count:
                problems.append(f'unknown_count_mismatch:{label}')
            if rebuilt['ineligible_count'] != figure.ineligible_count:
                problems.append(f'ineligible_count_mismatch:{label}')
            if rebuilt['required'] != figure.required:
                problems.append(f'required_amount_mismatch:{label}')
            if rebuilt['decided_count'] != figure.decided_count:
                problems.append(f'decided_count_mismatch:{label}')
        # Money conservation: total benefit delta equals the person-level sum.
        person_delta = sum((item.benefit_delta for item in comparisons),
                           Decimal('0.00')).quantize(Decimal('0.01'))
        budget_delta = report.budget_delta['required_delta']
        if person_delta != budget_delta:
            problems.append('benefit_delta_mismatch')
        return tuple(problems)

    def explain_person(self, comparison: PersonComparison) -> Mapping[str, Any]:
        """Human-checkable fact provenance for one paired decision."""
        old = comparison.old_verdict
        new = comparison.new_verdict
        return {
            'person_id': comparison.person_id,
            'movement': comparison.movement,
            'old': {
                'result': old.result,
                'reasons': list(old.reasons),
                'missing': list(old.missing),
                'clause_results': dict(old.clause_results),
                'benefit_amount': str(old.benefit_amount),
            },
            'new': {
                'result': new.result,
                'reasons': list(new.reasons),
                'missing': list(new.missing),
                'clause_results': dict(new.clause_results),
                'benefit_amount': str(new.benefit_amount),
            },
            'decisive_clauses': list(comparison.decisive_clauses),
            'benefit_delta': str(comparison.benefit_delta),
            'fact_sources': dict(comparison.fact_sources),
            'fact_values': {
                'old': dict(old.fact_values),
                'new': dict(new.fact_values),
            },
        }
