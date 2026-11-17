import unittest
from decimal import Decimal

from policy_sandbox.pair_analysis.evaluator import (
    ELIGIBLE, INELIGIBLE, INVALID, UNKNOWN, ExpressionError, SafeEvaluator,
)
from policy_sandbox.pair_analysis.units import UnitError, UnitRegistry


class SafeEvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.ev = SafeEvaluator(UnitRegistry({
            'age': 'years', 'income': 'CNY', 'rent': 'CNY', 'commute_km': 'km',
        }))

    def test_eligible_and_ineligible(self):
        expression = "age >= 18 and degree in ['master','doctor']"
        self.assertEqual(
            self.ev.evaluate(expression, {'age': 25, 'degree': 'master'}).result,
            ELIGIBLE)
        self.assertEqual(
            self.ev.evaluate(expression, {'age': 17, 'degree': 'bachelor'}).result,
            INELIGIBLE)

    def test_missing_fact_is_unknown_never_false(self):
        outcome = self.ev.evaluate('income < 10000', {})
        self.assertEqual(outcome.result, UNKNOWN)
        self.assertEqual(outcome.missing, ('income',))
        self.assertFalse(outcome.invalid)

    def test_unknown_excluded_from_boolean_denominator(self):
        # age fails first, so the missing income branch must not be reached.
        outcome = self.ev.evaluate('age >= 65 and income < 1000', {'age': 30})
        self.assertEqual(outcome.result, INELIGIBLE)
        clause_results = {c.clause_id: c.result for c in outcome.clauses}
        self.assertEqual(clause_results['clause-1'], INELIGIBLE)
        self.assertEqual(clause_results['clause-2'], UNKNOWN)

    def test_or_unknown_propagates_when_unsettled(self):
        # Both branches unknown -> the expression itself is unknown.
        outcome = self.ev.evaluate('age >= 65 or income > 100000', {})
        self.assertEqual(outcome.result, UNKNOWN)

    def test_or_settles_on_known_branch_despite_unknown_sibling(self):
        settled = self.ev.evaluate('age >= 65 or degree == "master"',
                                   {'degree': 'master'})
        self.assertEqual(settled.result, ELIGIBLE)
        refused = self.ev.evaluate('age >= 65 or degree == "master"',
                                   {'degree': 'bachelor'})
        self.assertEqual(refused.result, UNKNOWN)

    def test_not_normalizes_unknown(self):
        outcome = self.ev.evaluate('not employed', {})
        self.assertEqual(outcome.result, UNKNOWN)

    def test_literal_adopts_fact_unit(self):
        outcome = self.ev.evaluate('age >= 18', {'age': 25})
        self.assertEqual(outcome.result, ELIGIBLE)

    def test_same_unit_fact_comparison_allowed(self):
        outcome = self.ev.evaluate('income < rent',
                                   {'income': 5000, 'rent': 8000})
        self.assertEqual(outcome.result, ELIGIBLE)

    def test_different_units_rejected(self):
        outcome = self.ev.evaluate('income < commute_km',
                                   {'income': 5000, 'commute_km': 12})
        self.assertEqual(outcome.result, INVALID)
        self.assertTrue(outcome.invalid)
        self.assertTrue(any('unit_mismatch' in reason for reason in outcome.reasons))

    def test_bare_scalar_against_unit_fact_is_guarded_at_registry(self):
        registry = UnitRegistry({'income': 'CNY'})
        # A bare scalar (no unit) compared with a unit-bearing value is
        # rejected at the registry boundary in either direction.
        with self.assertRaises(UnitError):
            registry.reconcile(None, 'CNY', 10000, 5000)
        with self.assertRaises(UnitError):
            registry.reconcile('CNY', None, 5000, 10000)
        # Two bare scalars remain comparable.
        left, right, unit = registry.reconcile(None, None, 1, 2)
        self.assertEqual((left, right, unit), (Decimal('1'), Decimal('2'), ''))

    def test_unit_conversion_table(self):
        registry = UnitRegistry(
            {'yearly': 'CNY_y', 'monthly': 'CNY'},
            {'CNY_y': {'CNY': Decimal('0.1')}},
        )
        ev = SafeEvaluator(registry)
        outcome = ev.evaluate('monthly < yearly',
                              {'monthly': 900, 'yearly': 10000})
        self.assertEqual(outcome.result, ELIGIBLE)

    def test_clause_trace_keeps_facts_and_units(self):
        outcome = self.ev.evaluate(
            "age >= 18 and degree in ['master','doctor']",
            {'age': 25, 'degree': 'master'})
        age_clause = outcome.clauses[0]
        self.assertEqual(age_clause.units, {'age': 'years'})
        self.assertEqual(age_clause.fact_values, {'age': 25})
        self.assertTrue(age_clause.evaluated)

    def test_unsafe_expression_rejected(self):
        with self.assertRaises(ExpressionError):
            self.ev.evaluate('__import__("os")', {})

    def test_attribute_access_rejected(self):
        with self.assertRaises(ExpressionError):
            self.ev.evaluate('degree.upper() == "MASTER"', {'degree': 'master'})

    def test_split_clauses(self):
        clauses = self.ev.split_clauses('a == 1 and b == 2 and c == 3')
        self.assertEqual(clauses, ('a == 1', 'b == 2', 'c == 3'))

    def test_invalid_does_not_get_reclassified_as_unknown(self):
        outcome = self.ev.evaluate('income < commute_km and age >= 18',
                                   {'income': 1, 'commute_km': 2, 'age': 20})
        self.assertEqual(outcome.result, INVALID)

    def test_chained_comparison(self):
        outcome = self.ev.evaluate('18 <= age <= 60', {'age': 40})
        self.assertEqual(outcome.result, ELIGIBLE)
        outside = self.ev.evaluate('18 <= age <= 60', {'age': 70})
        self.assertEqual(outside.result, INELIGIBLE)


if __name__ == '__main__':
    unittest.main()
