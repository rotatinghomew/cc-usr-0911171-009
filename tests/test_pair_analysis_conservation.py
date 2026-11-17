import unittest
from decimal import Decimal

from policy_sandbox.pair_analysis.analyzer import (
    GAINED, LOST, STAYED_INELIGIBLE, PolicyPairAnalyzer,
)
from policy_sandbox.pair_analysis.evaluator import SafeEvaluator
from policy_sandbox.pair_analysis.models import FactSnapshot, PolicyVersion
from policy_sandbox.pair_analysis.reconcile import AnalysisReconciler
from policy_sandbox.pair_analysis.units import UnitRegistry
import datetime as dt


def version(label, expression, **overrides):
    params = {
        'label': label, 'expression': expression,
        'effective_from': dt.date(2025, 1, 1), 'benefit': Decimal('1000.00'),
        'currency': 'CNY', 'budget_cap': Decimal('5000.00'),
    }
    params.update(overrides)
    return PolicyVersion(**params)


def build(rows, units=None):
    return FactSnapshot.build(rows, units=units or {'age': 'years'})


class DenominatorHygieneTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = PolicyPairAnalyzer(
            SafeEvaluator(UnitRegistry({'age': 'years'})))

    def test_unknown_people_never_count_in_denominator(self):
        old = version('old', 'age >= 18')
        new = version('new', 'age >= 16', effective_from=dt.date(2027, 1, 1))
        snap = build([
            {'id': 'known-adult', 'facts': {'age': 25}},
            {'id': 'known-minor', 'facts': {'age': 10}},
            {'id': 'missing-age', 'facts': {'degree': 'master'}},
        ])
        report = self.analyzer.analyze(snap, old, new)
        figure = report.budget['old']
        # Only the two decided people enter the denominator.
        self.assertEqual(figure.decided_count, 2)
        self.assertEqual(figure.eligible_count + figure.ineligible_count, 2)
        self.assertEqual(figure.unknown_count, 1)
        # The unknown person's money must be zero, not a benefit payout.
        unknown_person = next(c for c in report.person_comparisons
                              if c.person_id == 'missing-age')
        self.assertEqual(unknown_person.old_verdict.benefit_amount, Decimal('0.00'))
        self.assertEqual(unknown_person.new_verdict.benefit_amount, Decimal('0.00'))

    def test_money_conservation_across_versions(self):
        old = version('old', 'age >= 18', benefit=Decimal('1000.00'))
        new = version('new', 'age >= 16', benefit=Decimal('1200.00'),
                      effective_from=dt.date(2027, 1, 1))
        snap = build([
            {'id': f'p{i}', 'facts': {'age': age}}
            for i, age in enumerate((20, 17, 15), 1)])
        report = self.analyzer.analyze(snap, old, new)
        # old: only p1 eligible -> 1000; new: p1 and p2 eligible -> 2400.
        self.assertEqual(report.budget['old'].required, Decimal('1000.00'))
        self.assertEqual(report.budget['new'].required, Decimal('2400.00'))
        detail_sum = sum((c.benefit_delta for c in report.person_comparisons),
                         Decimal('0.00'))
        self.assertEqual(detail_sum, Decimal('1400.00'))
        self.assertEqual(detail_sum, report.budget_delta['required_delta'])

    def test_lost_people_reduce_budget(self):
        old = version('old', 'age >= 18', benefit=Decimal('1000.00'))
        new = version('new', 'age >= 30', benefit=Decimal('1000.00'),
                      effective_from=dt.date(2027, 1, 1))
        snap = build([{'id': f'p{i}', 'facts': {'age': age}}
                      for i, age in enumerate((20, 40), 1)])
        report = self.analyzer.analyze(snap, old, new)
        self.assertEqual(report.movements[LOST], ('p1',))
        self.assertEqual(report.budget_delta['required_delta'], Decimal('-1000.00'))
        contribution = report.condition_contributions[0]
        self.assertEqual(contribution.lost, ('p1',))
        self.assertEqual(contribution.lost_amount, Decimal('1000.00'))

    def test_reaggregation_from_detail_matches_every_headline(self):
        old = version('old', 'age >= 18')
        new = version('new', 'age >= 16', effective_from=dt.date(2027, 1, 1))
        snap = build([
            {'id': 'p-a', 'facts': {'age': 25}},
            {'id': 'p-b', 'facts': {'age': 17}},
            {'id': 'p-c', 'facts': {}},
        ])
        report = self.analyzer.analyze(snap, old, new)
        reconciler = AnalysisReconciler()
        self.assertEqual(reconciler.verify(report), ())
        # Headline counts rebuild from the person-level detail.
        recounted = reconciler.recount_movements(report.person_comparisons)
        self.assertEqual(recounted, report.movement_counts)
        old_rebuilt = reconciler.resum_budget(report.person_comparisons, 'old')
        self.assertEqual(old_rebuilt['eligible_count'], 1)
        self.assertEqual(old_rebuilt['unknown_count'], 1)

    def test_each_person_carries_explainable_sources(self):
        snap = FactSnapshot.build([{
            'id': 'p1', 'facts': {'age': 25},
            'fact_sources': {'age': 'document-7#age'}}])
        report = self.analyzer.analyze(
            snap, version('old', 'age >= 18'),
            version('new', 'age >= 16', effective_from=dt.date(2027, 1, 1)))
        comparison = report.person_comparisons[0]
        explained = AnalysisReconciler().explain_person(comparison)
        self.assertEqual(explained['fact_sources']['age'], 'document-7#age')
        self.assertEqual(explained['old']['clause_results']['clause-1'], 'eligible')
        self.assertIn('fact_values', explained)

    def test_gained_and_listed_ids_are_disjoint_and_complete(self):
        old = version('old', 'age >= 18')
        new = version('new', 'age >= 30', effective_from=dt.date(2027, 1, 1))
        snap = build([{'id': f'p{i}', 'facts': {'age': age}}
                      for i, age in enumerate((20, 40, 12), 1)])
        report = self.analyzer.analyze(snap, old, new)
        all_listed = [pid for ids in report.movements.values() for pid in ids]
        self.assertEqual(sorted(all_listed), ['p1', 'p2', 'p3'])
        self.assertEqual(len(all_listed), len(set(all_listed)))
        self.assertEqual(report.movements[GAINED], ())
        self.assertEqual(report.movements[LOST], ('p1',))
        self.assertEqual(report.movements[STAYED_INELIGIBLE], ('p3',))


if __name__ == '__main__':
    unittest.main()
