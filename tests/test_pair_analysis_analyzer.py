import datetime as dt
import unittest
from decimal import Decimal

from policy_sandbox.pair_analysis.analyzer import (
    GAINED, LOST, STAYED_ELIGIBLE, STAYED_INELIGIBLE, UNKNOWN_EITHER,
    PolicyPairAnalyzer,
)
from policy_sandbox.pair_analysis.evaluator import SafeEvaluator
from policy_sandbox.pair_analysis.models import (
    FactSnapshot, PairAnalysisError, PolicyVersion,
)
from policy_sandbox.pair_analysis.units import UnitRegistry


def policy(label, expression, benefit='1200.00', cap='10000.00',
           effective_from='2025-01-01'):
    return PolicyVersion(
        label=label, expression=expression,
        effective_from=dt.date.fromisoformat(effective_from),
        benefit=Decimal(benefit), currency='CNY',
        budget_cap=Decimal(cap))


def snapshot(rows, units=None):
    return FactSnapshot.build(rows, units=units or {'age': 'years', 'income': 'CNY'})


class FactSnapshotTests(unittest.TestCase):
    def test_fingerprint_is_stable_for_same_facts(self):
        rows = [{'id': 'p1', 'facts': {'age': 25}}]
        first = snapshot(rows)
        second = snapshot(rows)
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_fingerprint_changes_when_facts_change(self):
        first = snapshot([{'id': 'p1', 'facts': {'age': 25}}])
        second = snapshot([{'id': 'p1', 'facts': {'age': 26}}])
        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_duplicate_person_rejected(self):
        with self.assertRaises(PairAnalysisError):
            snapshot([{'id': 'p1', 'facts': {}}, {'id': 'p1', 'facts': {}}])

    def test_explicit_provenance_required_for_every_fact(self):
        with self.assertRaises(PairAnalysisError):
            FactSnapshot.build([
                {'id': 'p1', 'facts': {'age': 25, 'income': 1},
                 'fact_sources': {'age': 'doc:age'}}])

    def test_explicit_provenance_accepted(self):
        snap = FactSnapshot.build([{
            'id': 'p1', 'facts': {'age': 25},
            'fact_sources': {'age': 'doc-42#age'}}])
        self.assertEqual(snap.persons[0].fact_sources['age'], 'doc-42#age')

    def test_empty_snapshot_rejected(self):
        with self.assertRaises(PairAnalysisError):
            FactSnapshot.build([])


class PolicyVersionTests(unittest.TestCase):
    def test_amounts_quantized(self):
        version = policy('old', 'age >= 18', benefit='1200.555')
        self.assertEqual(version.benefit, Decimal('1200.56'))

    def test_reversed_period_rejected(self):
        with self.assertRaises(PairAnalysisError):
            PolicyVersion('x', 'age >= 18', dt.date(2026, 1, 1),
                          Decimal('1'), 'CNY', Decimal('1'),
                          effective_to=dt.date(2025, 1, 1))

    def test_negative_amounts_rejected(self):
        with self.assertRaises(PairAnalysisError):
            policy('old', 'age >= 18', benefit='-1')


class PolicyPairAnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = PolicyPairAnalyzer(SafeEvaluator(
            UnitRegistry({'age': 'years', 'income': 'CNY'})))
        self.old = policy('old',
                          "age >= 18 and income >= 5000 and degree in ['master','doctor']")
        self.new = policy('new',
                          "age >= 16 and income >= 4000 and degree in ['master','doctor','bachelor']",
                          benefit='1500.00', effective_from='2027-01-01')
        self.snap = snapshot([
            {'id': 'p1', 'facts': {'age': 25, 'income': 8000, 'degree': 'master'}},
            {'id': 'p2', 'facts': {'age': 35, 'income': 12000, 'degree': 'bachelor'}},
            {'id': 'p3', 'facts': {'age': 28, 'degree': 'master'}},
            {'id': 'p4', 'facts': {'age': 17, 'income': 3000, 'degree': 'bachelor'}},
        ])

    def analyze(self):
        return self.analyzer.analyze(self.snap, self.old, self.new)

    def test_movement_classification(self):
        report = self.analyze()
        self.assertEqual(report.movements[STAYED_ELIGIBLE], ('p1',))
        self.assertEqual(report.movements[GAINED], ('p2',))
        self.assertEqual(report.movements[STAYED_INELIGIBLE], ('p4',))
        self.assertEqual(report.movements[UNKNOWN_EITHER], ('p3',))
        self.assertEqual(report.movement_counts['lost'], 0)

    def test_unknown_never_mixed_with_ineligible(self):
        report = self.analyze()
        old_budget = report.budget['old']
        self.assertEqual(old_budget.eligible_count, 1)
        self.assertEqual(old_budget.ineligible_count, 2)
        self.assertEqual(old_budget.unknown_count, 1)
        # Denominator only contains decided people.
        self.assertEqual(old_budget.decided_count, 3)
        self.assertNotIn('p3', report.movements[STAYED_INELIGIBLE])

    def test_budget_amounts_and_shortfall(self):
        old = policy('tight', 'age >= 18', benefit='1200.00', cap='1000.00')
        new = policy('wide', 'age >= 16', benefit='1200.00', cap='1000.00',
                     effective_from='2027-01-01')
        snap = snapshot([
            {'id': f'p{i}', 'facts': {'age': age}} for i, age in enumerate(
                (20, 30, 17, 17), 1)])
        report = self.analyzer.analyze(snap, old, new)
        self.assertEqual(report.budget['tight'].eligible_count, 2)
        self.assertEqual(report.budget['tight'].required, Decimal('2400.00'))
        self.assertEqual(report.budget['tight'].shortfall, Decimal('1400.00'))
        self.assertFalse(report.budget['tight'].within_budget)
        self.assertEqual(report.budget['wide'].eligible_count, 4)
        self.assertEqual(report.budget_delta['required_delta'], Decimal('2400.00'))
        self.assertEqual(report.budget_delta['eligible_count_delta'], 2)

    def test_condition_contribution_attributes_gains_and_losses(self):
        report = self.analyze()
        by_id = {c.clause_id: c for c in report.condition_contributions}
        self.assertTrue(all(c.changed for c in report.condition_contributions))
        # clause-1 age threshold 18 -> 16 lets p4 through that clause.
        self.assertIn('p4', by_id['clause-1'].gained)
        # clause-3 degree list widens for bachelor p2 and p4.
        self.assertEqual(by_id['clause-3'].gained, ('p2', 'p4'))
        # clause-2 income is unknown for p3 under both versions.
        self.assertEqual(by_id['clause-2'].unknown_old, ('p3',))
        self.assertEqual(by_id['clause-2'].unknown_new, ('p3',))

    def test_contribution_amounts_follow_version_benefit(self):
        report = self.analyze()
        clause3 = next(c for c in report.condition_contributions
                       if c.clause_id == 'clause-3')
        self.assertEqual(clause3.gained_amount, Decimal('3000.00'))
        self.assertEqual(clause3.lost_amount, Decimal('0.00'))

    def test_lost_movement_when_rule_tightens(self):
        old = policy('old', 'age >= 18')
        new = policy('new', 'age >= 30', effective_from='2027-01-01')
        snap = snapshot([{'id': 'p1', 'facts': {'age': 25}}])
        report = self.analyzer.analyze(snap, old, new)
        self.assertEqual(report.movements[LOST], ('p1',))
        comparison = report.person_comparisons[0]
        self.assertEqual(comparison.decisive_clauses, ('clause-1',))

    def test_person_provenance_retained(self):
        report = self.analyze()
        p1 = next(c for c in report.person_comparisons if c.person_id == 'p1')
        self.assertEqual(p1.fact_sources['age'], 'snapshot:p1:age')
        self.assertEqual(p1.old_verdict.fact_values['income'], 8000)
        self.assertEqual(p1.old_verdict.clause_results['clause-1'], 'eligible')

    def test_snapshot_fingerprint_pinned_in_report(self):
        report = self.analyze()
        self.assertEqual(report.snapshot_fingerprint, self.snap.fingerprint)

    def test_currency_mismatch_rejected(self):
        new = PolicyVersion('new', 'age >= 16', dt.date(2027, 1, 1),
                            Decimal('1500'), 'USD', Decimal('10000'))
        with self.assertRaises(PairAnalysisError):
            self.analyzer.analyze(self.snap, self.old, new)

    def test_unit_mismatch_collected_not_silently_compared(self):
        snap = FactSnapshot.build(
            [{'id': 'p1', 'facts': {'income': 100, 'commute_km': 5}}],
            units={'income': 'CNY', 'commute_km': 'km'})
        analyzer = PolicyPairAnalyzer(SafeEvaluator(
            UnitRegistry({'income': 'CNY', 'commute_km': 'km'})))
        old = policy('old', 'income < commute_km')
        new = policy('new', 'income < commute_km', effective_from='2027-01-01')
        report = analyzer.analyze(snap, old, new)
        self.assertTrue(report.unit_errors)
        self.assertTrue(all('unit_mismatch' in item for item in report.unit_errors))
        self.assertEqual(report.movement_counts['invalid'], 1)

    def test_detail_reaggregates_to_headline(self):
        report = self.analyze()
        total = sum(report.movement_counts.values())
        self.assertEqual(total, len(self.snap.persons))
        money = sum((c.benefit_delta for c in report.person_comparisons),
                    Decimal('0.00'))
        self.assertEqual(money, report.budget_delta['required_delta'])

    def test_stable_analysis_id_deterministic(self):
        first = PolicyPairAnalyzer.stable_analysis_id(self.snap, self.old, self.new)
        second = PolicyPairAnalyzer.stable_analysis_id(self.snap, self.old, self.new)
        self.assertEqual(first, second)
        self.assertTrue(first.startswith('pair-'))


if __name__ == '__main__':
    unittest.main()
