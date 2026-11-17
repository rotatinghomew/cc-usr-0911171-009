import json
import unittest
from decimal import Decimal

from policy_sandbox.pair_analysis.analyzer import GAINED, STAYED_ELIGIBLE
from policy_sandbox.pair_analysis.models import PairAnalysisError
from policy_sandbox.pair_analysis.reconcile import AnalysisReconciler
from policy_sandbox.pair_analysis.service import PairAnalysisService


def payload():
    return {
        'units': {'age': 'years', 'income': 'CNY'},
        'captured_at': '2026-11-17T00:00:00+00:00',
        'persons': [
            {'id': 'p1', 'facts': {'age': 25, 'income': 8000, 'degree': 'master'}},
            {'id': 'p2', 'facts': {'age': 35, 'income': 12000, 'degree': 'bachelor'}},
            {'id': 'p3', 'facts': {'age': 28, 'degree': 'master'}},
        ],
        'old_policy': {
            'expression': "age >= 18 and income >= 5000 and degree in ['master','doctor']",
            'effective_from': '2025-01-01', 'benefit': '1200.00', 'currency': 'CNY',
            'budget_cap': '10000.00',
        },
        'new_policy': {
            'expression': "age >= 16 and income >= 4000 and degree in ['master','doctor','bachelor']",
            'effective_from': '2027-01-01', 'benefit': '1500.00', 'currency': 'CNY',
            'budget_cap': '10000.00',
        },
    }


class PairAnalysisServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = PairAnalysisService()

    def test_run_verified_balances_detail_and_headline(self):
        report = self.service.run_verified(payload())
        self.assertEqual(report['reconciliation']['status'], 'balanced')
        self.assertEqual(report['movement_counts'][STAYED_ELIGIBLE], 1)
        self.assertEqual(report['movement_counts'][GAINED], 1)
        self.assertEqual(report['movement_counts']['unknown'], 1)

    def test_every_person_has_explainable_fact_sources(self):
        report = self.service.run_verified(payload())
        for person in report['persons']:
            self.assertTrue(person['fact_sources'])
            self.assertIn('movement', person)
            self.assertIn('old', person)
            self.assertIn('new', person)

    def test_unknown_person_excluded_from_money(self):
        report = self.service.run(payload())
        figure = report.budget['old']
        eligible_ids = [c.person_id for c in report.person_comparisons
                        if c.old_verdict.result == 'eligible']
        self.assertNotIn('p3', eligible_ids)
        self.assertEqual(figure.required, Decimal('1200.00'))

    def test_new_version_cannot_predate_old(self):
        bad = payload()
        bad['new_policy']['effective_from'] = '2024-01-01'
        with self.assertRaises(PairAnalysisError):
            self.service.run(bad)

    def test_missing_policy_section_rejected(self):
        bad = payload()
        del bad['old_policy']
        with self.assertRaises(PairAnalysisError):
            self.service.run(bad)

    def test_snapshot_identity_used_across_both_versions(self):
        report = self.service.run(payload())
        self.assertEqual(report.old_policy['label'], 'old')
        self.assertEqual(report.new_policy['label'], 'new')
        self.assertTrue(report.snapshot_fingerprint)

    def test_reconciler_detects_tampered_headline(self):
        report = self.service.run(payload())
        tampered = type(report)(
            **{**report.__dict__,
               'movement_counts': {**report.movement_counts, GAINED: 99}})
        problems = AnalysisReconciler().verify(tampered)
        self.assertTrue(any('movement_count_mismatch' in p for p in problems))

    def test_reconciler_resums_budget_from_detail(self):
        report = self.service.run(payload())
        rebuilt = AnalysisReconciler().resum_budget(
            report.person_comparisons, 'new')
        self.assertEqual(rebuilt['eligible_count'], 2)
        self.assertEqual(rebuilt['required'], Decimal('3000.00'))
        self.assertEqual(rebuilt['unknown_count'], 1)

    def test_report_is_json_serializable(self):
        report = self.service.run_verified(payload())
        text = json.dumps(report, ensure_ascii=False, default=str)
        decoded = json.loads(text)
        self.assertEqual(decoded['analysis_id'], report['analysis_id'])

    def test_versions_interpreted_independently(self):
        # Same person facts, old expression has no income clause; the old
        # verdict must not borrow clauses from the new expression.
        data = payload()
        data['old_policy']['expression'] = "age >= 18 and degree in ['master','doctor']"
        report = self.service.run(data)
        p3 = next(c for c in report.person_comparisons if c.person_id == 'p3')
        self.assertEqual(p3.old_verdict.result, 'eligible')
        self.assertEqual(p3.new_verdict.result, 'unknown')
        self.assertEqual(len(p3.old_verdict.clause_results), 2)
        self.assertEqual(len(p3.new_verdict.clause_results), 3)


class CliPairAnalysisTests(unittest.TestCase):
    def test_cli_writes_verified_report(self):
        import argparse
        from pathlib import Path
        import tempfile
        from policy_sandbox.cli import main

        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / 'fixture.json'
            output = Path(directory) / 'report.json'
            fixture.write_text(json.dumps(payload()), encoding='utf-8')
            code = main(['--database', ':memory:', 'pair-analysis',
                         '--fixture', str(fixture), '--output', str(output)])
            self.assertEqual(code, 0)
            decoded = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(decoded['reconciliation']['status'], 'balanced')
            self.assertEqual(len(decoded['persons']), 3)

    def test_cli_rejects_bad_fixture_with_error_code(self):
        from pathlib import Path
        import tempfile
        from policy_sandbox.cli import main

        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / 'fixture.json'
            output = Path(directory) / 'report.json'
            fixture.write_text(json.dumps({'persons': []}), encoding='utf-8')
            with self.assertRaises(SystemExit) as caught:
                main(['--database', ':memory:', 'pair-analysis',
                      '--fixture', str(fixture), '--output', str(output)])
            self.assertEqual(caught.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
